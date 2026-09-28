"""Regression tests for deployment and setup safety boundaries."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import basaltwater
from lib.config import SetupConfig
from lib.command_display import redacted_setup_parts
from lib.deployment import DeploymentOrchestrator
from lib.deploy_utils import repository_stage_name, validate_repository_source_tree
from lib.project_manifest import Component, Manifest, _parse_component
from lib.setup_common import prepare_deployments
from lib.state_read import StateReadError
from lib.validation import validate_agent_repositories, validate_deploy_specs
from lib import setup_common, sysadmin_svc
import remote_setup
from plugins.post_setup import build_post_setup_steps


class DeploymentSafetyTests(unittest.TestCase):
    def test_failed_old_release_cleanup_keeps_new_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = os.path.join(directory, "www")
            source = os.path.join(directory, "source")
            os.mkdir(base)
            os.mkdir(source)
            with open(os.path.join(source, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("new")
            orchestrator = DeploymentOrchestrator(base_dir=base)
            url = "https://example.com/site.git"
            destination = orchestrator.get_deployment_path("example.com", "/", url)
            os.mkdir(destination)
            with open(os.path.join(destination, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("old")
            manifest = Manifest(1, [Component(
                name="site", type="static", domain="example.com", output=".",
            )])
            real_rmtree = shutil.rmtree

            def cleanup(path, *args, **kwargs):
                if ".previous-" in str(path):
                    raise OSError("cleanup failed")
                return real_rmtree(path, *args, **kwargs)

            with patch("lib.deployment.run", return_value=MagicMock(returncode=0)), \
                 patch.object(orchestrator, "_ensure_build_user"), \
                 patch.object(orchestrator, "_prepare_build_toolchain"), \
                 patch.object(orchestrator, "_app_unit_snapshots", return_value={}), \
                 patch("lib.deployment.shutil.rmtree", side_effect=cleanup):
                orchestrator.deploy_manifest(
                    manifest, source, "example.com", "/", url, "new", keep_source=True,
                )
            with open(os.path.join(destination, "index.html"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new")

    def test_repository_symlink_cannot_copy_an_external_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source")
            stage = os.path.join(directory, "stage")
            secret = os.path.join(directory, "secret")
            os.mkdir(source)
            with open(secret, "w", encoding="utf-8") as handle:
                handle.write("private")
            os.symlink(secret, os.path.join(source, "leak"))

            with self.assertRaisesRegex(ValueError, "symlink"):
                DeploymentOrchestrator._copy_deployment_source(source, stage)
            self.assertTrue(os.path.islink(os.path.join(stage, "leak")))
            linked_source = os.path.join(directory, "linked-source")
            os.symlink(source, linked_source)
            with self.assertRaisesRegex(ValueError, "symlink"):
                DeploymentOrchestrator._copy_deployment_source(
                    linked_source, os.path.join(directory, "other-stage"),
                )

    def test_manifest_rejects_nginx_directives_in_static_output(self) -> None:
        component = {
            "name": "site", "type": "static", "domain": "example.com",
            "path": "/", "output": "dist; add_header X-Leak yes",
        }
        with self.assertRaisesRegex(ValueError, "safe Nginx path"):
            _parse_component(component, 0)
        component["output"] = "dist"
        component["path"] = "/x; return 200"
        with self.assertRaisesRegex(ValueError, "nginx path"):
            _parse_component(component, 0)


class SetupSafetyTests(unittest.TestCase):
    def test_runtime_staging_does_not_follow_controller_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = os.path.join(directory, "project")
            runtime_lib = os.path.join(project, "lib")
            destination = os.path.join(directory, "staged")
            secret = os.path.join(directory, "secret")
            os.makedirs(runtime_lib)
            os.mkdir(destination)
            with open(secret, "w", encoding="utf-8") as handle:
                handle.write("private")
            os.chmod(secret, 0o600)
            os.symlink(secret, os.path.join(runtime_lib, "linked-secret"))
            with patch.object(setup_common, "SCRIPT_DIR", runtime_lib), \
                 patch.object(setup_common, "write_setup_snapshot_metadata"):
                with self.assertRaisesRegex(ValueError, "symlinked runtime source"):
                    setup_common.copy_project_files(destination)
            self.assertEqual(os.stat(secret).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(os.path.join(destination, "lib", "linked-secret")))

    def test_git_clone_rejects_option_like_url_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(setup_common.subprocess, "run") as run:
            result = setup_common.clone_repository("-cunsafe.git", directory)
        self.assertIsNone(result)
        run.assert_not_called()

    def test_git_clone_rejects_inline_secret_without_logging_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(setup_common.subprocess, "run") as run, \
             patch("builtins.print") as printed:
            result = setup_common.clone_repository(
                "https://user:secret@example.com/site.git", directory,
            )
        self.assertIsNone(result)
        run.assert_not_called()
        self.assertNotIn(
            "secret", "\n".join(str(call.args[0]) for call in printed.call_args_list),
        )

    def test_deploy_url_rejects_inline_credentials_and_query_tokens(self) -> None:
        for git_url in (
            "https://user:secret@example.com/site.git",
            "git+https://user:secret@example.com/site.git",
            "https://example.com/site.git?token=secret",
            "https://example.com/site.git#secret",
        ):
            with self.subTest(git_url=git_url):
                with self.assertRaisesRegex(ValueError, "credentials|query or fragment"):
                    validate_deploy_specs([["example.com", git_url]])
        validate_deploy_specs([["example.com", "git@example.com:owner/site.git"]])
        with self.assertRaisesRegex(ValueError, "query or fragment"):
            validate_agent_repositories(["https://example.com/site.git?token=secret"])

    def test_display_redacts_legacy_git_url_credentials(self) -> None:
        parts = [
            "--deploy example.com 'https://user:secret@example.com/site.git'",
            "--repo 'https://example.com/agent.git?token=secret'",
            "--deploy public.example.com https://example.com/public.git",
        ]
        visible = " ".join(redacted_setup_parts(parts))
        self.assertNotIn("secret", visible)
        self.assertEqual(visible.count("REPLACE_WITH_GIT_URL"), 2)
        self.assertIn("https://example.com/public.git", visible)

    def test_repository_link_is_rejected_before_manifest_inspection(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            deploy_specs=[["example.com", "https://example.com/site.git"]],
        )
        with tempfile.TemporaryDirectory() as directory:
            checkout = os.path.join(directory, "checkout")
            outside = os.path.join(directory, "outside.json")
            os.mkdir(checkout)
            with open(outside, "w", encoding="utf-8") as handle:
                handle.write("private")
            os.symlink(outside, os.path.join(checkout, "basaltwater.json"))
            with patch.object(setup_common, "clone_repository", return_value=(checkout, "abc123")), \
                 patch("lib.project_manifest.load_manifest") as load_manifest:
                with self.assertRaisesRegex(ValueError, "link or special file"):
                    prepare_deployments(config, directory)
            load_manifest.assert_not_called()
            self.assertFalse(os.path.exists(os.path.join(directory, "checkout.commit")))

    def test_repository_special_file_is_rejected_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.mkfifo(os.path.join(directory, "pipe"))
            with self.assertRaisesRegex(ValueError, "link or special file"):
                validate_repository_source_tree(directory)

    def test_post_setup_routes_use_results_of_deployment_step(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            deploy_specs=[["example.com", "https://example.com/site.git"]],
            enable_ssl=True, ssl_email="ops@example.com",
        )
        result = {
            "domain": "example.com", "path": "/", "needs_proxy": False,
            "serve_path": "/var/www/example_com", "project_type": "static",
        }
        steps = build_post_setup_steps(
            config, lambda _url, _mode, _dry_run: ("/staged/site", "abc123"),
        )
        with patch("deploy.deploy_steps.deploy_repository", return_value=[result]) as deploy, \
             patch("lib.nginx_config.create_nginx_sites_for_groups") as nginx, \
             patch("web.ssl_steps.install_certbot") as certbot, \
             patch("web.ssl_steps.setup_ssl_for_deployments") as ssl:
            for _name, function in steps:
                function(config)
        deploy.assert_called_once()
        nginx.assert_called_once_with(
            {"example.com": [result]}, enable_https_redirect=True,
        )
        certbot.assert_called_once_with(config)
        ssl.assert_called_once_with(
            [result], "ops@example.com", enable_https_redirect=True,
        )

    def test_dry_run_lists_work_after_plugin_steps(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            enable_cloudflare=True, enable_samba=True,
            deploy_specs=[["example.com", "https://example.com/site.git"]],
            smb_mounts=[["mount"]],
        )
        plan = [
            name for name, _step in build_post_setup_steps(
                config, lambda _url, _mode, _dry_run: ("/unused", ""),
            )
        ]
        self.assertIn("Deploying uploaded repositories", plan)
        self.assertIn("Configuring Samba and reconciling shares", plan)
        self.assertIn("Configuring SMB mounts", plan)
        self.assertLess(
            plan.index("Deploying uploaded repositories"),
            plan.index("Verifying Cloudflare tunnel activation"),
        )

    def test_display_redacts_webhook_url_without_changing_replay_config(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            notify_specs=[["webhook", "https://example.com/receiver?key=private#token=secret"]],
        )
        command = config.to_setup_command()
        visible = " ".join(redacted_setup_parts(command))
        self.assertIn("REPLACE_WITH_WEBHOOK_URL", visible)
        self.assertNotIn("private", visible)
        self.assertNotIn("secret", visible)
        self.assertIn("token=secret", " ".join(command))

    def test_copy_failure_preserves_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = os.path.join(directory, "basaltwater")
            source = os.path.join(directory, "source")
            state = os.path.join(directory, "state")
            os.mkdir(runtime)
            os.mkdir(source)
            marker = os.path.join(runtime, "previous")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("live")
            with patch.object(setup_common, "REMOTE_INSTALL_DIR", runtime), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state), \
                 patch.object(setup_common.shutil, "copytree", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    setup_common._activate_local_runtime(source)
            self.assertTrue(os.path.isfile(marker))

    def test_activation_failure_restores_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = os.path.join(directory, "basaltwater")
            source = os.path.join(directory, "source")
            state = os.path.join(directory, "state")
            os.mkdir(runtime)
            os.mkdir(source)
            marker = os.path.join(runtime, "previous")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("live")
            with open(os.path.join(source, "new"), "w", encoding="utf-8") as handle:
                handle.write("candidate")
            real_rename = os.rename

            def rename(old, new):
                if ".basaltwater-stage-" in old and new == runtime:
                    raise OSError("activation failed")
                return real_rename(old, new)

            with patch.object(setup_common, "REMOTE_INSTALL_DIR", runtime), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state), \
                 patch.object(setup_common.os, "rename", side_effect=rename):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    setup_common._activate_local_runtime(source)
            self.assertTrue(os.path.isfile(marker))
            self.assertFalse(os.path.exists(os.path.join(runtime, "new")))

    def test_managed_runtime_copy_failure_preserves_uploaded_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = os.path.join(directory, "basaltwater")
            source = os.path.join(directory, "source")
            state = os.path.join(directory, "state")
            os.makedirs(os.path.join(runtime, ".git"))
            os.makedirs(os.path.join(runtime, "deployments"))
            os.makedirs(os.path.join(source, "deployments"))
            old_source = os.path.join(runtime, "deployments", "old-repo")
            with open(old_source, "w", encoding="utf-8") as handle:
                handle.write("live")
            with open(os.path.join(source, "deployments", "new-repo"), "w", encoding="utf-8") as handle:
                handle.write("candidate")
            real_copytree = shutil.copytree

            def copytree(src, dst, **kwargs):
                if src == os.path.join(source, "deployments"):
                    raise OSError("disk full")
                return real_copytree(src, dst, **kwargs)

            with patch.object(setup_common, "SCRIPT_DIR", os.path.join(runtime, "lib")), \
                 patch.object(setup_common, "REMOTE_INSTALL_DIR", runtime), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state), \
                 patch.object(setup_common.shutil, "copytree", side_effect=copytree):
                with self.assertRaisesRegex(OSError, "disk full"):
                    setup_common._activate_local_runtime(source)
            with open(old_source, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "live")

    def test_managed_runtime_activation_failure_restores_uploaded_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = os.path.join(directory, "basaltwater")
            source = os.path.join(directory, "source")
            state = os.path.join(directory, "state")
            os.makedirs(os.path.join(runtime, ".git"))
            os.makedirs(os.path.join(runtime, "deployments"))
            os.makedirs(os.path.join(source, "deployments"))
            old_source = os.path.join(runtime, "deployments", "old-repo")
            with open(old_source, "w", encoding="utf-8") as handle:
                handle.write("live")
            with open(os.path.join(source, "deployments", "new-repo"), "w", encoding="utf-8") as handle:
                handle.write("candidate")
            real_rename = os.rename

            def rename(old, new):
                if ".basaltwater-managed-stage-" in old and new == os.path.join(runtime, "deployments"):
                    raise OSError("activation failed")
                return real_rename(old, new)

            with patch.object(setup_common, "SCRIPT_DIR", os.path.join(runtime, "lib")), \
                 patch.object(setup_common, "REMOTE_INSTALL_DIR", runtime), \
                 patch.object(setup_common, "PERSISTENT_STATE_DIR", state), \
                 patch.object(setup_common.os, "rename", side_effect=rename):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    setup_common._activate_local_runtime(source)
            with open(old_source, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "live")
            self.assertFalse(os.path.exists(os.path.join(runtime, "deployments", "new-repo")))

    def test_same_basename_repositories_stage_separately(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            deploy_specs=[
                ["a.example.com", "https://git.example.com/one/site.git"],
                ["b.example.com", "https://git.example.com/two/site.git"],
            ],
        )
        first_url = config.deploy_specs[0][1]
        second_url = config.deploy_specs[1][1]
        self.assertNotEqual(repository_stage_name(first_url), repository_stage_name(second_url))

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(setup_common, "clone_repository") as clone:
            def staged_source(url, _target_dir, **_kwargs):
                path = os.path.join(directory, repository_stage_name(url))
                os.mkdir(path)
                return path, "abc123"

            clone.side_effect = staged_source
            prepare_deployments(config, directory)
            self.assertEqual(clone.call_count, 2)
            self.assertTrue(os.path.isfile(os.path.join(directory, repository_stage_name(first_url) + ".commit")))
            self.assertTrue(os.path.isfile(os.path.join(directory, repository_stage_name(second_url) + ".commit")))


class SysadminSafetyTests(unittest.TestCase):
    def test_stopped_service_returns_action_result(self) -> None:
        with patch.object(sysadmin_svc, "_resolve_credentials", return_value=("root", None)), \
             patch.object(sysadmin_svc, "build_ssh_command", return_value=["ssh"]) as build, \
             patch.object(sysadmin_svc, "run_command") as run:
            run.return_value.returncode = 0
            self.assertEqual(sysadmin_svc.run_svc("host", "demo", action="stop"), 0)
        command = build.call_args.kwargs["remote_command"]
        self.assertIn("action_rc=$?", command)
        self.assertIn('exit "$action_rc"', command)


class SavedConfigurationDisplayTests(unittest.TestCase):
    def test_inventory_outputs_redact_legacy_urls_without_changing_cache(self) -> None:
        config = SetupConfig(
            host="host", username="person", system_type="server_web",
            deploy_specs=[["example.com", "https://user:secret@example.com/site.git"]],
            agent_repos=["https://example.com/agent.git?token=secret"],
            notify_specs=[["webhook", "https://example.com/hook?token=secret"]],
        )
        record = {
            "host": config.host, "system_type": config.system_type,
            "command": "basaltw setup --notify webhook https://example.com/hook?token=secret",
            "args": {**config.to_dict(), "git_auth_token": "secret"},
        }

        with patch.object(basaltwater, "get_all_configs", return_value=[record]):
            json_output = io.StringIO()
            with redirect_stdout(json_output):
                self.assertEqual(basaltwater.list_configurations(json_output=True), 0)
            listed = json.loads(json_output.getvalue())
            info_output = io.StringIO()
            with redirect_stdout(info_output):
                self.assertEqual(basaltwater.show_info(), 0)

        self.assertNotIn("secret", json_output.getvalue())
        self.assertNotIn("secret", info_output.getvalue())
        self.assertNotIn("command", listed[0])
        self.assertNotIn("git_auth_token", listed[0]["args"])
        self.assertEqual(listed[0]["args"]["deploy_specs"][0][1], "https://REPLACE_WITH_GIT_URL")
        self.assertEqual(listed[0]["args"]["agent_repos"][0], "https://REPLACE_WITH_GIT_URL")
        self.assertEqual(listed[0]["args"]["notify_specs"][0][1], "https://REPLACE_WITH_WEBHOOK_URL")
        self.assertIn("https://REPLACE_WITH_GIT_URL", info_output.getvalue())
        self.assertIn("user:secret", record["args"]["deploy_specs"][0][1])

    def test_malformed_cache_fails_inventory_instead_of_disappearing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "broken.json"), "w", encoding="utf-8") as handle:
                handle.write("{broken")
            with patch("lib.cache.get_setup_cache_dir", return_value=directory):
                with self.assertRaises(StateReadError):
                    basaltwater.get_all_configs()
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    self.assertEqual(basaltwater.list_configurations(json_output=True), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("broken.json", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
