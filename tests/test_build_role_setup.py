"""Saved shared-host roles survive an ordinary public setup rerun."""

from __future__ import annotations

import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.build_role_setup import setup_config_with_saved_build_roles
from lib.cache import load_setup_command, save_setup_command
from lib.config import SetupConfig
from lib.github_runner_config import runner_repositories
from tests.test_build_selection import enrollment


class SavedBuildRoleSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"BASALTWATER_WORKSPACE": str(self.root)}))
        self.output = self.enterContext(patch("sys.stdout", new_callable=io.StringIO))
        self.enterContext(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
        self.enterContext(patch.object(basaltwater, "prompt_for_missing_passwords"))
        self.enterContext(patch.object(basaltwater, "prepare_runtime_config", side_effect=copy.deepcopy))
        self.enterContext(patch.object(basaltwater, "store_cli_credentials"))
        self.enterContext(patch.object(basaltwater, "print_setup_summary"))
        self.enterContext(patch.object(basaltwater, "print_service_access_summary"))
        self.remote = self.enterContext(patch.object(basaltwater, "run_remote_setup", return_value=0))
        for name in ("subprocess.run", "subprocess.Popen", "socket.socket", "socket.getaddrinfo"):
            self.enterContext(patch(name, side_effect=AssertionError("Unexpected system action: " + name)))
        self.cached = SetupConfig(
            host="192.0.2.40", username="buildadmin", system_type="server_lite", machine_type="vm",
            is_build_server=True, build_host_enrollment=enrollment(),
            enable_github_runner=True, ci_repositories=["example/private-one", "example/private-two"],
            ci_allowed_users=["trusted-user"], ci_labels=["shared-host"],
            enable_webhook_builds=True, webhook_repositories=["example/project"],
            webhook_branches=["main", "stable"], webhook_domain="builds.example.com", enable_ssl=True,
        )
        save_setup_command(self.cached)

    def rerun(self, *options):
        args = self.parser.parse_args([
            "setup", "server_lite", "192.0.2.40", "buildadmin", "--machine", "vm", *options,
        ])
        self.assertEqual(basaltwater.run_setup_command(args), 0, self.output.getvalue())
        return load_setup_command("192.0.2.40")

    def test_omitted_roles_keep_policy_in_controller_and_remote_arguments(self):
        saved = self.rerun()
        fields = ("is_build_server", "build_host_enrollment", "enable_github_runner", "ci_repositories",
                  "ci_allowed_users", "ci_labels", "enable_webhook_builds", "webhook_repositories",
                  "webhook_branches", "webhook_domain", "enable_ssl")
        remote = self.remote.call_args.args[0]
        for field in fields:
            with self.subTest(field=field):
                self.assertEqual(getattr(saved, field), getattr(self.cached, field))
                self.assertEqual(getattr(remote, field), getattr(self.cached, field))
        arguments = remote.to_remote_args()
        for flag in ("--build-server", "--github-runner", "--webhook-builds"):
            self.assertIn(flag, arguments)
        self.assertTrue(any(argument.startswith("--build-host-enrollment-json ") for argument in arguments))
        # Use the receiving parser and constructor as well as the sending one.
        parser = create_setup_argument_parser("Remote setup", for_remote=True, allow_steps=True)
        received_args = parser.parse_args(shlex.split(" ".join(arguments)))
        received_args.host = "localhost"
        received = SetupConfig.from_args(received_args, "server_lite")
        for field in fields:
            with self.subTest(remote_saved_field=field):
                self.assertEqual(received.to_dict()[field], self.cached.to_dict()[field])

    def test_explicit_disables_survive_another_omitted_rerun(self):
        for saved in (self.rerun("--no-build-server", "--no-github-runner", "--no-webhook-builds"),
                      self.rerun()):
            self.assertFalse(saved.is_build_server)
            self.assertTrue(saved.disable_build_host)
            self.assertFalse(saved.enable_github_runner)
            self.assertTrue(saved.disable_github_runner)
            self.assertFalse(saved.enable_webhook_builds)
            self.assertIsNone(saved.build_host_enrollment)
            self.assertIsNone(saved.ci_repositories)

    def test_builder_disable_also_disables_inherited_webhook_but_keeps_runner(self):
        saved = self.rerun("--no-build-server")
        self.assertTrue(saved.disable_build_host)
        self.assertFalse(saved.enable_webhook_builds)
        self.assertTrue(saved.enable_github_runner)
        self.assertEqual(saved.ci_repositories, self.cached.ci_repositories)

    def test_explicit_scope_and_webhook_policy_replace_inherited_values(self):
        saved = self.rerun(
            "--ci-organization", "new-org", "--ci-runner-group", "restricted",
            "--ci-allowed-user", "new-user", "--ci-label", "new-label",
            "--webhook-repository", "new-org/project", "--webhook-branch", "release",
            "--webhook-domain", "next-builds.example.com",
        )
        self.assertEqual(saved.ci_organization, "new-org")
        self.assertIsNone(saved.ci_repositories)
        self.assertEqual(saved.ci_runner_group, "restricted")
        self.assertEqual(saved.ci_allowed_users, ["new-user"])
        self.assertEqual(saved.ci_labels, ["new-label"])
        self.assertEqual(saved.webhook_repositories, ["new-org/project"])
        self.assertEqual(saved.webhook_branches, ["release"])
        self.assertEqual(saved.webhook_domain, "next-builds.example.com")

    def test_organization_group_is_preserved_only_for_the_same_scope(self):
        self.cached = replace(self.cached, ci_repositories=None, ci_organization="example", ci_runner_group="private")
        save_setup_command(self.cached)
        self.assertEqual(self.rerun().ci_runner_group, "private")
        changed = self.rerun("--ci-repository", "example/replacement")
        self.assertEqual(runner_repositories(changed), ["example/replacement"])
        self.assertEqual(changed.ci_runner_group, "")
        self.assertEqual(changed.ci_organization, "")

    def test_explicit_builder_policy_replaces_saved_grants(self):
        policy = enrollment()
        del policy["keys"]["bob"]
        del policy["policy"]["principals"]["bob"]
        path = self.root / "replacement-policy.json"
        path.write_text(json.dumps(policy))
        saved = self.rerun("--build-host-policy", str(path))
        self.assertEqual(saved.build_host_enrollment, policy)

    def test_explicit_panel_default_keeps_its_parser_sentinel(self):
        self.assertEqual(self.rerun("--web-panel").web_panel_port, 443)

    def test_dedicated_runner_keeps_its_single_repository_policy(self):
        self.cached = SetupConfig(
            host="192.0.2.40", username="buildadmin", system_type="server_github_runner", machine_type="vm",
            ci_repository="example/private-one", ci_allowed_users=["trusted-user"], ci_labels=["dedicated"],
        )
        save_setup_command(self.cached)
        args = self.parser.parse_args(["setup", "server_github_runner", "192.0.2.40", "buildadmin"])
        self.assertEqual(basaltwater.run_setup_command(args), 0, self.output.getvalue())
        saved = load_setup_command("192.0.2.40")
        self.assertEqual(runner_repositories(saved), ["example/private-one"])
        self.assertEqual(saved.ci_allowed_users, ["trusted-user"])

    def test_resource_update_keeps_restricted_projects_and_other_principals(self):
        with patch("lib.build_enrollment_cli._local_path", return_value="/unused/key"), \
             patch("lib.build_enrollment_cli._check_private_identity"), \
             patch("lib.build_enrollment_cli._public_companion", return_value=self.cached.build_host_enrollment["keys"]["alice"]):
            saved = self.rerun("--build-limit", "memory_bytes", str(512 * 1024**2))
        principals = saved.build_host_enrollment["policy"]["principals"]
        self.assertEqual(set(principals["alice"]["projects"]), {"site"})
        self.assertEqual(principals["alice"]["projects"]["site"]["limits"]["memory_bytes"], 512 * 1024**2)
        self.assertEqual(principals["bob"], self.cached.build_host_enrollment["policy"]["principals"]["bob"])

    def test_invalid_explicit_combination_fails_before_mutation(self):
        for options in (("--no-build-server", "--webhook-builds"), ("--no-ssl",)):
            with self.subTest(options=options):
                args = self.parser.parse_args(["setup", "server_lite", "192.0.2.40", "buildadmin", *options])
                self.assertEqual(basaltwater.run_setup_command(args), 1)
                self.remote.assert_not_called()
                self.assertTrue(load_setup_command("192.0.2.40").enable_webhook_builds)

    def test_different_profile_does_not_inherit_roles(self):
        args = self.parser.parse_args(["setup", "server_web", "192.0.2.40", "buildadmin"])
        self.assertEqual(basaltwater.run_setup_command(args), 0, self.output.getvalue())
        saved = load_setup_command("192.0.2.40")
        self.assertFalse(saved.is_build_server)
        self.assertFalse(saved.enable_github_runner)
        self.assertFalse(saved.enable_webhook_builds)

    def test_inheritance_keeps_tunnel_mode_and_never_copies_credentials_or_mutates_args(self):
        self.cached.enable_cloudflare = True
        self.cached.cloudflare_allow_direct = False
        self.cached.ci_register_with_gh = True
        self.cached.ci_registration_token_file = "/unused/stale-token"
        self.cached.ci_registration_token = "stale-token"
        self.cached.webhook_secret = "stale-secret"
        self.cached.webhook_github_token = "stale-token"
        args = self.parser.parse_args(["setup", "server_lite", "192.0.2.40", "buildadmin"])
        before = copy.deepcopy(vars(args))
        config = setup_config_with_saved_build_roles(args, self.cached)
        self.assertEqual(vars(args), before)
        self.assertTrue(config.enable_cloudflare)
        self.assertFalse(config.cloudflare_allow_direct)
        self.assertFalse(config.ci_register_with_gh)
        for field in ("ci_registration_token_file", "ci_registration_token", "webhook_secret", "webhook_github_token"):
            self.assertIsNone(getattr(config, field))


if __name__ == "__main__":
    unittest.main()
