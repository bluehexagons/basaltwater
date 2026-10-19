"""Multiple personal repositories share one VM while retaining registrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.service_tools import web_panel_github_runner as panel
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib import github_runner_credentials as credentials
from lib.github_runner_config import PROFILE, repository_instance
from tests import test_github_runner as fixtures
from web import github_runner_steps as steps


def multiple(**overrides) -> SetupConfig:
    return fixtures.config(ci_repository="", ci_repositories=["owner/first", "owner/second"], **overrides)


class MultipleConfigurationTests(unittest.TestCase):
    def test_queue_preparation_keeps_the_locked_inode_across_setup_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(steps, "DATA", temporary):
            account = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
            steps._prepare_queue(account)
            before = Path(temporary, "queue.lock").stat()
            steps._prepare_queue(account)
            after = Path(temporary, "queue.lock").stat()
            self.assertEqual(before.st_ino, after.st_ino)
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)

    def test_instance_scope_mismatch_and_disabled_runner_fail_before_filesystem_access(self) -> None:
        for instance, policy in ((repository_instance("owner/second"), fixtures.policy()),
                                 ("", fixtures.policy() | {"enabled": False})):
            with (patch.object(fixtures.gate, "read_json_file", return_value=policy),
                  patch.object(fixtures.gate.os, "lstat") as inspect,
                  self.assertRaises(ValueError)):
                fixtures.gate.check_installation(instance=instance)
            inspect.assert_not_called()

    def test_repeated_repository_flags_survive_saved_and_remote_round_trips(self) -> None:
        parser = create_setup_argument_parser("test")
        parsed = parser.parse_args(["localhost", "operator", "--ci-repository", "owner/first",
                                    "--ci-repository", "owner/second", "--ci-allowed-user", "alice"])
        original = SetupConfig.from_args(parsed, PROFILE)
        remote_parser = create_setup_argument_parser("remote", for_remote=True)
        remote_args = remote_parser.parse_args(shlex.split(" ".join(original.to_remote_args())))
        remote_args.host = "localhost"
        for restored in (SetupConfig.from_args(remote_args, PROFILE),
                         SetupConfig.from_dict("localhost", PROFILE, original.to_dict())):
            self.assertEqual(restored.ci_repositories, ["owner/first", "owner/second"])
        self.assertEqual(" ".join(original.to_setup_command()).count("--ci-repository"), 2)

    def test_invalid_repositories_and_ambiguous_token_file_are_rejected(self) -> None:
        for fields in (dict(ci_repository="owner/first"), dict(ci_organization="owner"),
                       dict(ci_repositories=["owner/first", "OWNER/FIRST"]), dict(ci_repositories=["../bad"]),
                       dict(ci_repositories="owner/first"), dict(ci_repositories=[f"owner/repo{i}" for i in range(21)]),
                       dict(ci_registration_token_file="/private/token")):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                fixtures.config(**(dict(ci_repository="", ci_repositories=["owner/first", "owner/second"]) | fields))

    def test_gh_permission_failure_for_one_repository_still_stages_other_tokens(self) -> None:
        with (tempfile.TemporaryDirectory() as temporary,
              patch.object(credentials, "_token_from_gh", side_effect=[ValueError("denied"), b"B" * 40]) as gh):
            target = Path(temporary, "payload")
            self.assertTrue(credentials.prepare_registration_payload(multiple(ci_register_with_gh=True), str(target)))
            self.assertEqual([call.args[0].ci_repository for call in gh.call_args_list], ["owner/first", "owner/second"])
            self.assertEqual(json.loads((target / "tokens.json").read_text()), {"owner/second": "B" * 40})
            self.assertEqual(stat.S_IMODE((target / "tokens.json").stat().st_mode), 0o600)


class MultipleInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        fixtures.InstallationTests.setUp(self)
        self.units.side_effect = None

        def register(application, policy, token, account, paths=None):
            for name in steps.CREDENTIAL_FILES:
                (application / name).write_text("generated:" + policy["repository"])

        self.register.side_effect = register

    def paths(self, repository: str):
        return steps._paths(repository_instance(repository))

    def test_registers_multiple_repositories_and_preserves_ids_when_reordered(self) -> None:
        bundle = self.token.with_name("tokens.json")
        bundle.write_text(json.dumps({"owner/first": "A" * 40, "owner/second": "B" * 40}))
        bundle.chmod(0o600)
        with patch.object(steps, "_download_release", wraps=steps._download_release) as download:
            steps.setup_github_runner(multiple(ci_registration_payload=True))
        download.assert_called_once()
        self.assertEqual(sum(call.args[0] == ["apt-get", "update"] for call in self.run.call_args_list), 1)
        self.assertFalse(bundle.exists())
        secondary = self.paths("owner/second")
        self.assertEqual(json.loads(Path(secondary.policy).read_text())["registration"], "registered")
        self.assertEqual(Path(secondary.app, ".credentials").read_text(), "generated:owner/second")
        self.assertEqual(self.register.call_count, 2)
        steps.setup_github_runner(fixtures.config(ci_repository="", ci_repositories=["owner/second", "owner/first"]))
        self.assertEqual(self.register.call_count, 2)
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["repository"], "owner/first")

    def test_registration_failure_does_not_block_other_repositories_or_future_retry(self) -> None:
        bundle = self.token.with_name("tokens.json")
        bundle.write_text(json.dumps({"owner/first": "A" * 40, "owner/second": "B" * 40}))
        bundle.chmod(0o600)
        register = self.register.side_effect

        def fail_first(application, policy, token, account, paths=None):
            if policy["repository"] == "owner/first":
                raise RuntimeError("rejected")
            register(application, policy, token, account, paths)

        self.register.side_effect = fail_first
        steps.setup_github_runner(multiple(ci_registration_payload=True))
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["registration"], "pending")
        self.assertEqual(json.loads(Path(self.paths("owner/second").policy).read_text())["registration"], "registered")

    def test_removing_primary_retains_secondary_registration_without_creating_duplicate(self) -> None:
        steps.setup_github_runner(multiple())
        secondary = self.paths("owner/second")
        steps.setup_github_runner(fixtures.config(ci_repository="owner/second"))
        self.assertFalse(json.loads(Path(steps.POLICY).read_text())["enabled"])
        self.assertTrue(json.loads(Path(secondary.policy).read_text()).get("enabled", True))
        self.assertIn(unittest.mock.call(["systemctl", "disable", "--now", steps.SERVICE], timeout=120), self.run.call_args_list)
        steps.setup_github_runner(multiple())
        self.assertTrue(json.loads(Path(steps.POLICY).read_text()).get("enabled", True))

    def test_removed_secondary_is_disabled_and_can_be_readded(self) -> None:
        steps.setup_github_runner(multiple())
        secondary = self.paths("owner/second")
        steps.setup_github_runner(fixtures.config(ci_repository="owner/first"))
        self.assertFalse(json.loads(Path(secondary.policy).read_text())["enabled"])
        steps.setup_github_runner(multiple())
        self.assertTrue(json.loads(Path(secondary.policy).read_text()).get("enabled", True))

    def test_panel_registers_next_pending_repository_using_only_installed_policy(self) -> None:
        steps.setup_github_runner(multiple())
        primary = json.loads(Path(steps.POLICY).read_text()) | {"registration": "registered"}
        secondary = self.paths("owner/second")
        pending = json.loads(Path(secondary.policy).read_text())
        with patch.object(fixtures.gate, "check_installation", side_effect=[primary, pending]) as check:
            steps.register_pending_runner(b"B" * 40)
        self.assertEqual(check.call_args.kwargs["instance"], secondary.instance)
        self.assertEqual(json.loads(Path(secondary.policy).read_text())["registration"], "registered")


class MultiplePanelTests(unittest.TestCase):
    def test_lists_all_repositories_and_registers_one_pending_scope_at_a_time(self) -> None:
        snapshot = panel.RunnerSnapshot(policy=fixtures.policy() | {"registration": "registered"})
        snapshot.instances = [panel.RunnerSnapshot(policy=fixtures.policy() | {"repository": name, "registration": "pending"})
                              for name in ("owner/first", "owner/second")]
        with patch.object(panel, "collect_runner_status", return_value=snapshot):
            page = panel.render_runner_page(True, "", "host", navigation=(), csrf="csrf", secure=True)
        for name in ("owner/repo", "owner/first", "owner/second"):
            self.assertIn(name, page)
        self.assertEqual(page.count('name="token"'), 1)
        self.assertIn("owner/first/settings/actions/runners/new", page)
        self.assertIn("One workflow job runs on this host at a time", page)
