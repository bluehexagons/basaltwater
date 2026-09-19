"""Tests for managed origin-scoped Git HTTPS credentials."""

from __future__ import annotations

import os
import shlex
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.git_credential_steps import configure_git_https_credentials
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.credentials import prepare_runtime_config, set_workspace_credential
from lib.git_credentials import (
    normalize_git_ca_source,
    normalize_git_https_origin,
    parse_git_ca_ssh_source,
)
from lib.validation import validate_agent_git_settings
from plugins.common import extend_agent_steps


ORIGIN = "https://192.168.0.51:3000"


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "192.168.0.41",
        "username": "agent",
        "system_type": "server_lite",
        "git_access": "read-write",
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestGitCredentialArguments(unittest.TestCase):
    def test_setup_parser_accepts_origin_credential_and_ca(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "192.168.0.41",
                "agent",
                "--git-access",
                "read-write",
                "--git-credential",
                ORIGIN,
                "gitadmin",
                "--git-ca-certificate",
                ORIGIN,
                "gogs.crt",
            ]
        )

        config = SetupConfig.from_args(args, "server_lite")

        self.assertEqual(config.git_credentials, [[ORIGIN, "gitadmin"]])
        self.assertEqual(
            config.git_ca_certificates,
            [[ORIGIN, os.path.abspath("gogs.crt")]],
        )

    def test_setup_parser_accepts_authenticated_ssh_ca_source(self) -> None:
        parser = create_setup_argument_parser("test")
        source = (
            "ssh://gitadmin@192.168.0.51/"
            "etc/nginx/ssl/192.168.0.51.crt"
        )
        args = parser.parse_args(
            [
                "192.168.0.41",
                "agent",
                "--git-ca-certificate",
                ORIGIN,
                source,
            ]
        )

        config = SetupConfig.from_args(args, "server_lite")

        self.assertEqual(config.git_ca_certificates, [[ORIGIN, source]])

    def test_clear_cannot_be_combined_with_a_new_credential(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "192.168.0.41",
                "agent",
                "--git-credential",
                ORIGIN,
                "gitadmin",
                "--no-git-credentials",
            ]
        )

        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            SetupConfig.from_args(args, "server_lite")


class TestGitCredentialConfiguration(unittest.TestCase):
    def test_origin_is_canonicalized(self) -> None:
        self.assertEqual(
            normalize_git_https_origin("https://GIT.EXAMPLE.test:443/"),
            "https://git.example.test",
        )
        self.assertEqual(
            normalize_git_https_origin("https://[2001:db8::51]:3000"),
            "https://[2001:db8::51]:3000",
        )

    def test_origin_rejects_http_and_repository_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "https"):
            normalize_git_https_origin("http://git.example.test")
        with self.assertRaisesRegex(ValueError, "repository path"):
            normalize_git_https_origin("https://git.example.test/team/repo.git")

    def test_ssh_ca_source_is_canonical_and_rejects_embedded_password(self) -> None:
        source = "ssh://agent@[2001:DB8::51]:2222/etc/nginx/ssl/gogs%20ca.crt"

        normalized = normalize_git_ca_source(source)
        parsed = parse_git_ca_ssh_source(normalized)

        self.assertEqual(
            normalized,
            "ssh://agent@[2001:db8::51]:2222/etc/nginx/ssl/gogs%20ca.crt",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.host, "2001:db8::51")
        self.assertEqual(parsed.path, "/etc/nginx/ssl/gogs ca.crt")
        with self.assertRaisesRegex(ValueError, "must not contain a password"):
            parse_git_ca_ssh_source(
                "ssh://agent:secret@192.168.0.51/etc/nginx/ssl/gogs.crt"
            )

    def test_to_dict_keeps_nonsecret_declaration_only(self) -> None:
        config = _config(
            git_credentials=[[ORIGIN, "gitadmin"]],
            git_ca_certificates=[[ORIGIN, "/run/secrets/gogs.crt"]],
            git_ca_pems=[[ORIGIN, "encoded-secret-transport"]],
            share_credentials=[["gitadmin", "super-secret-value"]],
        )

        saved = config.to_dict()

        self.assertEqual(saved["git_credentials"], [[ORIGIN, "gitadmin"]])
        self.assertEqual(
            saved["git_ca_certificates"],
            [[ORIGIN, "/run/secrets/gogs.crt"]],
        )
        self.assertNotIn("git_ca_pems", saved)
        self.assertNotIn("share_credentials", saved)
        self.assertNotIn("super-secret-value", str(saved))
        reconstructed = "\n".join(config.to_setup_command())
        self.assertIn(f"--git-credential {ORIGIN} gitadmin", reconstructed)
        self.assertIn("--git-ca-certificate", reconstructed)
        self.assertIn("--credential gitadmin [REDACTED]", reconstructed)
        self.assertNotIn("super-secret-value", reconstructed)

    def test_runtime_resolution_uses_workspace_password_and_stages_ca(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            source = os.path.join(workspace, "gogs.crt")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write("placeholder")
            os.chmod(source, 0o600)
            set_workspace_credential("gitadmin", "chosen-password", workspace)
            config = _config(
                git_credentials=[[ORIGIN, "gitadmin"]],
                git_ca_certificates=[[ORIGIN, source]],
            )

            with patch(
                "lib.credentials._read_git_ca_bundle",
                return_value=(
                    "-----BEGIN CERTIFICATE-----\nQQ==\n"
                    "-----END CERTIFICATE-----\n"
                ),
            ):
                runtime = prepare_runtime_config(config, workspace)

        self.assertEqual(
            runtime.share_credentials,
            [["gitadmin", "chosen-password"]],
        )
        self.assertIsNone(runtime.git_ca_certificates)
        self.assertEqual(runtime.git_ca_pems[0][0], ORIGIN)
        remote_args = "\n".join(runtime.to_remote_args())
        self.assertIn("--git-credential", remote_args)
        self.assertIn("--git-ca-pem", remote_args)
        self.assertNotIn(source, remote_args)
        self.assertNotIn("chosen-password", str(config.to_dict()))

        parser = create_setup_argument_parser("remote", for_remote=True)
        remote_tokens: list[str] = []
        for argument in runtime.to_remote_args():
            remote_tokens.extend(shlex.split(argument))
        remote_namespace = parser.parse_args(remote_tokens)
        remote_namespace.host = "localhost"
        parsed_remote = SetupConfig.from_args(remote_namespace, "server_lite")
        self.assertEqual(parsed_remote.git_credentials, [[ORIGIN, "gitadmin"]])
        self.assertEqual(parsed_remote.git_ca_pems, runtime.git_ca_pems)
        self.assertEqual(
            parsed_remote.share_credentials,
            [["gitadmin", "chosen-password"]],
        )

    def test_runtime_fetches_ca_over_host_key_verified_ssh(self) -> None:
        source = (
            "ssh://gitadmin@192.168.0.51/"
            "etc/nginx/ssl/192.168.0.51.crt"
        )
        pem = (
            "-----BEGIN CERTIFICATE-----\nQQ==\n"
            "-----END CERTIFICATE-----\n"
        )
        with tempfile.TemporaryDirectory() as workspace:
            known_hosts = os.path.join(workspace, "known_hosts")
            with open(known_hosts, "w", encoding="utf-8") as file_obj:
                file_obj.write("192.168.0.51 ssh-ed25519 AAAA\n")
            with (
                patch("lib.credentials.build_ssh_command", return_value=["ssh"]) as build,
                patch("lib.credentials.ssh_batch_mode", return_value=True),
                patch("lib.credentials.subprocess.run") as run,
            ):
                run.return_value = SimpleNamespace(
                    returncode=0,
                    stdout=pem,
                    stderr="",
                )
                runtime = prepare_runtime_config(
                    _config(git_ca_certificates=[[ORIGIN, source]]),
                    workspace,
                )

        self.assertIsNotNone(runtime.git_ca_pems)
        build.assert_called_once()
        build_kwargs = build.call_args.kwargs
        self.assertEqual(build.call_args.args, ("192.168.0.51", "gitadmin"))
        self.assertEqual(build_kwargs["known_hosts_path"], known_hosts)
        self.assertIn("sudo -n head -c", build_kwargs["remote_command"])
        run.assert_called_once()

    def test_runtime_ssh_ca_source_requires_enrolled_host_key(self) -> None:
        source = (
            "ssh://gitadmin@192.168.0.51/"
            "etc/nginx/ssl/192.168.0.51.crt"
        )
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "ssh-key enroll"):
                prepare_runtime_config(
                    _config(git_ca_certificates=[[ORIGIN, source]]),
                    workspace,
                )

    def test_missing_workspace_password_fails_before_remote_setup(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(ValueError, "Missing credential for Git user"):
                prepare_runtime_config(
                    _config(git_credentials=[[ORIGIN, "gitadmin"]]),
                    workspace,
                )

    def test_validation_requires_git_policy_and_rejects_github_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "--git-access"):
            validate_agent_git_settings(
                _config(
                    git_access="none",
                    git_credentials=[[ORIGIN, "gitadmin"]],
                    share_credentials=[["gitadmin", "password"]],
                )
            )
        with self.assertRaisesRegex(ValueError, "GitHub credentials"):
            validate_agent_git_settings(
                _config(
                    git_credentials=[["https://github.com", "agent"]],
                    share_credentials=[["agent", "password"]],
                )
            )

    def test_patch_merges_by_origin_and_clear_removes_all(self) -> None:
        cached = _config(
            git_credentials=[[ORIGIN, "old-user"]],
            git_ca_certificates=[[ORIGIN, "/old.crt"]],
        )
        updated = _config(
            git_credentials=[[ORIGIN, "new-user"]],
            git_ca_certificates=[[ORIGIN, "/new.crt"]],
        )

        merged = merge_setup_configs(cached, updated)

        self.assertEqual(merged.git_credentials, [[ORIGIN, "new-user"]])
        self.assertEqual(merged.git_ca_certificates, [[ORIGIN, "/new.crt"]])

        cleared = merge_setup_configs(
            merged,
            _config(clear_git_credentials=True),
        )
        self.assertIsNone(cleared.git_credentials)
        self.assertIsNone(cleared.git_ca_certificates)
        self.assertTrue(cleared.clear_git_credentials)

    def test_plugin_installs_git_before_configuring_credentials(self) -> None:
        steps: list[tuple[str, object]] = []
        extend_agent_steps(
            _config(
                git_credentials=[[ORIGIN, "gitadmin"]],
                share_credentials=[["gitadmin", "password"]],
            ),
            steps,
        )

        names = [name for name, _function in steps]
        self.assertLess(
            names.index("Installing Git for agent repositories"),
            names.index("Configuring managed Git HTTPS credentials"),
        )


class TestTargetGitCredentialSetup(unittest.TestCase):
    def _run_as_user(self, username, home, command, **_kwargs):
        self.assertEqual(username, "agent")
        self.assertEqual(home, self.home)
        if "--get credential." in command:
            return SimpleNamespace(returncode=0, stdout="gitadmin\n", stderr="")
        if "--get http." in command:
            from lib.git_credentials import git_ca_filename

            ca_path = os.path.join(
                self.home,
                ".config",
                "basaltwater",
                "git",
                "ca",
                git_ca_filename(ORIGIN),
            )
            return SimpleNamespace(returncode=0, stdout=ca_path + "\n", stderr="")
        if "--unset-all" in command:
            return SimpleNamespace(returncode=5, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_writes_scoped_helper_ca_and_encoded_credential(self) -> None:
        with tempfile.TemporaryDirectory() as self.home:
            stale_ca_dir = os.path.join(
                self.home,
                ".config",
                "basaltwater",
                "git",
                "ca",
            )
            os.makedirs(stale_ca_dir)
            stale_ca = os.path.join(stale_ca_dir, "stale.pem")
            with open(stale_ca, "w", encoding="utf-8") as file_obj:
                file_obj.write("stale")
            account = SimpleNamespace(
                pw_dir=self.home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            config = _config(
                git_credentials=[[ORIGIN, "gitadmin"]],
                git_ca_pems=[[ORIGIN, "encoded"]],
                share_credentials=[["gitadmin", "p@ss word"]],
            )
            pem = (
                "-----BEGIN CERTIFICATE-----\nQQ==\n"
                "-----END CERTIFICATE-----\n"
            )
            with (
                patch("common.git_credential_steps._target_account", return_value=account),
                patch("common.git_credential_steps._run_as_login_user", side_effect=self._run_as_user),
                patch("common.git_credential_steps.decode_git_ca_pem", return_value=pem),
                patch("common.git_credential_steps.os.chown"),
                patch("common.git_credential_steps.is_dry_run", return_value=False),
            ):
                configure_git_https_credentials(config)

            managed = os.path.join(self.home, ".config", "basaltwater", "git")
            credential_path = os.path.join(managed, "credentials")
            include_path = os.path.join(managed, "config")
            with open(credential_path, encoding="utf-8") as file_obj:
                credential_content = file_obj.read()
            with open(include_path, encoding="utf-8") as file_obj:
                include_content = file_obj.read()

            self.assertIn("gitadmin:p%40ss%20word@192.168.0.51:3000", credential_content)
            self.assertNotIn("p@ss word", include_content)
            self.assertIn(f'[credential "{ORIGIN}"]', include_content)
            self.assertIn("helper =", include_content)
            self.assertIn(f'[http "{ORIGIN}"]', include_content)
            self.assertEqual(os.stat(credential_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(include_path).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(stale_ca))

    def test_clear_removes_only_managed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as self.home:
            managed = os.path.join(self.home, ".config", "basaltwater", "git")
            os.makedirs(managed)
            with open(os.path.join(managed, "credentials"), "w", encoding="utf-8") as file_obj:
                file_obj.write("secret")
            unrelated = os.path.join(self.home, "keep.txt")
            with open(unrelated, "w", encoding="utf-8") as file_obj:
                file_obj.write("keep")
            account = SimpleNamespace(
                pw_dir=self.home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            with (
                patch("common.git_credential_steps._target_account", return_value=account),
                patch("common.git_credential_steps._run_as_login_user", side_effect=self._run_as_user),
                patch("common.git_credential_steps.is_dry_run", return_value=False),
            ):
                configure_git_https_credentials(
                    _config(clear_git_credentials=True)
                )

            self.assertFalse(os.path.exists(managed))
            self.assertTrue(os.path.isfile(unrelated))

    def test_refuses_symlinked_managed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as self.home:
            outside = os.path.join(self.home, "outside")
            os.mkdir(outside)
            os.symlink(outside, os.path.join(self.home, ".config"))
            account = SimpleNamespace(
                pw_dir=self.home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            with (
                patch("common.git_credential_steps._target_account", return_value=account),
                patch("common.git_credential_steps._run_as_login_user", side_effect=self._run_as_user),
                patch("common.git_credential_steps.os.chown"),
                patch("common.git_credential_steps.is_dry_run", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "unsafe managed Git"):
                    configure_git_https_credentials(
                        _config(
                            git_credentials=[[ORIGIN, "gitadmin"]],
                            share_credentials=[["gitadmin", "password"]],
                        )
                    )


if __name__ == "__main__":
    unittest.main()
