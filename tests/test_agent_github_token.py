"""GitHub setup token selection, workspace replay, and private payload tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from basaltwater import _patch_preserve_keys
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.credentials import prepare_runtime_config, set_workspace_credential
from lib.display import print_setup_summary
from lib.setup_common import prepare_agent_payload
from lib.validation import validate_agent_git_settings, validate_github_token


TOKEN = "github_pat_fixture_only"


class GitHubSetupTokenTests(unittest.TestCase):
    def _args(self, *options: str):
        parser = create_setup_argument_parser("test")
        return parser.parse_args(["192.0.2.41", "agent", *options])

    def _config(self, *options: str, profile: str = "agent_vm") -> SetupConfig:
        with patch("lib.system_utils.get_local_timezone", return_value="UTC"):
            return SetupConfig.from_args(self._args(*options), profile)

    def test_explicit_sources_override_active_profile_default(self) -> None:
        for option, field, value in (
            ("--git-auth-token", "git_auth_token", TOKEN),
            ("--git-auth-credential", "git_auth_credential", "vm-github"),
        ):
            with self.subTest(option=option):
                config = self._config(option, value, profile="agent_code_vm")
                self.assertIsNone(config.git_auth_source)
                self.assertEqual(getattr(config, field), value)
                self.assertTrue(config.copy_agent_keys)
                validate_agent_git_settings(config)

    def test_cli_rejects_mixed_sources(self) -> None:
        selections = (
            ["--git-auth", "active"],
            ["--git-auth", "none"],
            ["--git-auth-file", "/unused/token"],
            ["--git-auth-token", TOKEN],
            ["--git-auth-credential", "vm-github"],
        )
        for index, first in enumerate(selections):
            for second in selections[index + 1:]:
                if first[0] == second[0]:
                    continue
                with self.subTest(first=first[0], second=second[0]):
                    with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                        self._args(*first, *second)

    def test_empty_token_and_invalid_names_fail_before_store_read(self) -> None:
        for field, value in (
            ("git_auth_token", ""),
            ("git_auth_token", "contains secret whitespace"),
            ("git_auth_token", "secret\x00"),
            ("git_auth_credential", ""),
            ("git_auth_credential", "bad:name"),
            ("git_auth_credential", "bad\nname"),
        ):
            with self.subTest(field=field, value=value):
                config = SetupConfig("target", "agent", "agent_vm", **{field: value})
                with patch("lib.credentials.load_workspace_credentials") as read_store:
                    with self.assertRaises(ValueError) as error:
                        prepare_runtime_config(config)
                    read_store.assert_not_called()
                if value:
                    self.assertNotIn(value, str(error.exception))

    def test_validator_accepts_opaque_classic_and_fine_grained_tokens(self) -> None:
        for token in ("ghp_fixture", TOKEN, "opaque-future-token"):
            self.assertEqual(validate_github_token(f" {token}\n"), token)

    def test_requires_gh_tool_git_access_and_supported_host(self) -> None:
        for overrides, message in (
            ({"agent_tools": None, "install_gh": False}, "agent-tool gh"),
            ({"git_access": "none"}, "git-access read"),
            ({"git_host": "git.example.test"}, "only --git-host github.com"),
            ({"agent_auth_files": [["gh", "/unused/token"]]}, "either --git-auth"),
        ):
            config = self._config("--git-access", "read", "--git-auth-token", TOKEN)
            for field, value in overrides.items():
                setattr(config, field, value)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_agent_git_settings(config)

    def test_rejects_ambiguous_programmatic_sources_before_resolution(self) -> None:
        config = self._config("--git-auth-credential", "vm-github")
        config.git_auth_token = TOKEN
        with patch("lib.credentials.load_workspace_credentials") as read_store:
            with self.assertRaisesRegex(ValueError, "exactly one"):
                prepare_runtime_config(config)
            read_store.assert_not_called()

    def test_named_token_round_trip_and_private_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = str(Path(directory) / "workspace")
            set_workspace_credential("vm-github", TOKEN, workspace)
            config = self._config(
                "--git-auth-credential", "vm-github", profile="agent_code_vm"
            )
            saved = config.to_dict()
            replay = SetupConfig.from_dict(config.host, config.system_type, saved)
            self.assertEqual(replay.git_auth_credential, "vm-github")
            self.assertIsNone(replay.git_auth_source)
            command = " ".join(replay.to_setup_command())
            self.assertIn("--git-auth-credential vm-github", command)
            self.assertNotIn(TOKEN, command + json.dumps(saved))
            with redirect_stdout(StringIO()) as summary:
                print_setup_summary(replay)
            self.assertIn("GitHub auth: supplied", summary.getvalue())
            self.assertNotIn(TOKEN, summary.getvalue())

            runtime = prepare_runtime_config(replay, workspace)
            self.assertEqual(runtime.git_auth_token, TOKEN)
            self.assertIsNone(runtime.git_auth_credential)
            self.assertIsNone(replay.git_auth_token)
            validate_agent_git_settings(runtime)
            output = StringIO()
            payload = Path(directory) / "payload"
            with (
                patch("lib.setup_common._local_user_home", return_value=directory),
                patch("lib.setup_common.shutil.which", return_value=None),
                patch("lib.setup_common.subprocess.run") as run_command,
                redirect_stdout(output),
            ):
                prepare_agent_payload(runtime, str(payload))
            run_command.assert_not_called()
            token_file = payload / "secrets/gh/hosts.yml"
            self.assertIn(f'oauth_token: "{TOKEN}"', token_file.read_text())
            self.assertIn("git_protocol: https", token_file.read_text())
            self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
            self.assertTrue(runtime.github_auth_payload)
            self.assertNotIn(TOKEN, output.getvalue())
            self.assertNotIn(TOKEN, " ".join(runtime.to_remote_args()))
            set_workspace_credential("vm-github", "updated-fixture-token", workspace)
            updated = prepare_runtime_config(replay, workspace)
            self.assertEqual(updated.git_auth_token, "updated-fixture-token")

    def test_direct_token_stages_without_controller_gh_or_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._config("--git-access", "read", "--git-auth-token", TOKEN)
            runtime = prepare_runtime_config(config, directory)
            with (
                patch("lib.setup_common._local_user_home", return_value=directory),
                patch("lib.setup_common.shutil.which", return_value=None),
                patch("lib.setup_common.subprocess.run") as run_command,
                redirect_stdout(StringIO()),
            ):
                prepare_agent_payload(runtime, str(Path(directory) / "payload"))
            run_command.assert_not_called()
            self.assertTrue(runtime.github_auth_payload)
            self.assertNotIn(TOKEN, json.dumps(config.to_dict()))
            self.assertNotIn(TOKEN, " ".join(config.to_setup_command()))
            self.assertNotIn(TOKEN, " ".join(runtime.to_remote_args()))

    def test_inline_workspace_credential_overrides_saved_value(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            set_workspace_credential("vm-github", "old-token", workspace)
            config = self._config(
                "--git-access", "read", "--git-auth-credential", "vm-github",
                "--credential", "vm-github", TOKEN,
            )
            runtime = prepare_runtime_config(config, workspace)
            self.assertEqual(runtime.git_auth_token, TOKEN)
            self.assertIsNone(config.git_auth_token)

    def test_missing_or_malformed_saved_token_stops_setup(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = self._config("--git-auth-credential", "vm-github")
            with self.assertRaisesRegex(ValueError, "Missing GitHub credential"):
                prepare_runtime_config(config, workspace)
            set_workspace_credential("vm-github", "malformed secret token", workspace)
            with self.assertRaises(ValueError) as error:
                prepare_runtime_config(config, workspace)
            self.assertNotIn("malformed secret token", str(error.exception))

    def test_dry_run_does_not_stage_or_read_controller_auth(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = self._config(
                "--git-access", "read", "--git-auth-token", TOKEN, "--dry-run"
            )
            runtime = prepare_runtime_config(config, workspace)
            payload = Path(workspace) / "payload"
            with (
                patch("lib.setup_common._local_user_home") as home,
                redirect_stdout(StringIO()) as output,
            ):
                prepare_agent_payload(runtime, str(payload))
            home.assert_not_called()
            self.assertFalse(payload.exists())
            self.assertFalse(runtime.github_auth_payload)
            self.assertNotIn(TOKEN, output.getvalue())

    def test_runtime_validation_rejects_token_without_git_access(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config = self._config("--git-auth-token", TOKEN)
            with self.assertRaisesRegex(ValueError, "git-access read"):
                prepare_runtime_config(config, workspace)

    def test_patch_replaces_or_preserves_named_source(self) -> None:
        for options, name, source, token in (
            ((), "vm-github", None, None),
            (("--git-auth-token", TOKEN), None, None, TOKEN),
            (("--git-auth", "none"), None, None, None),
            (("--git-auth", "active"), None, "active", None),
            (("--git-auth-file", "/unused/token"), None, None, None),
        ):
            with self.subTest(options=options):
                cached = self._config("--git-auth-credential", "vm-github")
                args = self._args(*options)
                new = self._config(*options)
                merged = merge_setup_configs(
                    cached, new, preserve_keys=_patch_preserve_keys(args)
                )
                self.assertEqual(merged.git_auth_credential, name)
                self.assertEqual(merged.git_auth_source, source)
                self.assertEqual(merged.git_auth_token, token)

    def test_patch_named_source_replaces_saved_active_login(self) -> None:
        cached = self._config("--git-access", "read", "--git-auth", "active")
        options = ("--git-access", "read", "--git-auth-credential", "vm-github")
        new = self._config(*options)
        merged = merge_setup_configs(
            cached, new, preserve_keys=_patch_preserve_keys(self._args(*options))
        )
        self.assertEqual(merged.git_auth_credential, "vm-github")
        self.assertIsNone(merged.git_auth_source)
        validate_agent_git_settings(merged)


if __name__ == "__main__":
    unittest.main()
