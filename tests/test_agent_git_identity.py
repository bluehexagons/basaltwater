"""Git identity seeding works independently of credential transfer."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from common.agent_steps import _configure_git_identity, configure_git_commit_identity, copy_agent_tooling_payload
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.setup_common import prepare_agent_payload
from plugins.common import extend_agent_steps


class GitIdentityTests(unittest.TestCase):
    def test_identity_without_github_or_config_copy(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", agent_repos=["https://example.test/team/project.git"])
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory) / "payload"
            with patch("lib.setup_common._local_user_home", return_value=directory), patch("lib.setup_common._active_git_identity", return_value={"name": "Controller", "email": "controller@example.test"}):
                prepare_agent_payload(config, str(payload))
            self.assertTrue(config.git_identity_payload)
            self.assertFalse(config.github_auth_payload)
            self.assertEqual(json.loads((payload / "config/git/identity.json").read_text()), {"name": "Controller", "email": "controller@example.test"})
            with patch("common.agent_steps.REMOTE_AGENT_PAYLOAD_DIR", str(payload)), patch("common.agent_steps._user_home", return_value=directory), patch("common.agent_steps.is_dry_run", return_value=False), patch("common.agent_steps._configure_git_identity") as configure:
                copy_agent_tooling_payload(config)
            configure.assert_called_once_with(config)
            self.assertFalse(payload.exists())

    def test_explicit_overrides_replace_existing_fields_without_github_lookup(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", git_author_name="VM Name", git_author_email="vm@example.test")
        with patch("common.agent_steps._configured_git_identity_value", side_effect=["Existing", "old@example.test"]), patch("common.agent_steps._git_identity_payload", return_value={"name": "Controller", "email": "controller@example.test"}), patch("common.agent_steps._github_git_identity", side_effect=AssertionError("No network lookup")), patch("common.agent_steps._set_git_identity_value") as write:
            _configure_git_identity(config)
        self.assertEqual([call.args[1:] for call in write.call_args_list], [("user.name", "VM Name"), ("user.email", "vm@example.test")])

    def test_partial_override_preserves_other_target_field(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", git_author_name="VM Name", git_identity_source="none")
        with patch("common.agent_steps._configured_git_identity_value", side_effect=["Existing", "old@example.test"]), patch("common.agent_steps._git_identity_payload", side_effect=AssertionError("Opted out")), patch("common.agent_steps._set_git_identity_value") as write:
            _configure_git_identity(config)
        write.assert_called_once_with(config, "user.name", "VM Name")

    def test_identity_opt_out_does_not_read_controller_or_target(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", agent_tools=["codex"], git_identity_source="none")
        with tempfile.TemporaryDirectory() as directory, patch("lib.setup_common._active_git_identity", side_effect=AssertionError("Opted out")):
            prepare_agent_payload(config, directory)
        with patch("common.agent_steps._configured_git_identity_value", side_effect=AssertionError("Opted out")):
            _configure_git_identity(config)
        self.assertFalse(config.git_identity_payload)

    def test_unavailable_github_identity_keeps_controller_field_and_explains_fix(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev")
        with patch("common.agent_steps._configured_git_identity_value", return_value=None), patch("common.agent_steps._git_identity_payload", return_value={"name": "Controller"}), patch("common.agent_steps._github_git_identity", side_effect=RuntimeError("private provider error")), patch("common.agent_steps._set_git_identity_value") as write, patch("builtins.print") as output:
            _configure_git_identity(config)
        write.assert_called_once_with(config, "user.name", "Controller")
        message = str(output.call_args)
        self.assertIn("--git-email", message)
        self.assertNotIn("private provider error", message)

    def test_git_installed_before_payload_and_t3(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", agent_tools=["gh", "codex"], agent_payload=True, web_interfaces=["t3code"])
        steps = []
        extend_agent_steps(config, steps)
        functions = [function.__name__ for _, function in steps]
        self.assertLess(functions.index("install_git_for_agent_repositories"), functions.index("copy_agent_tooling_payload"))
        self.assertLess(functions.index("copy_agent_tooling_payload"), functions.index("install_t3code_web"))

    def test_cli_remote_and_saved_identity_round_trip(self):
        parser = create_setup_argument_parser("setup")
        args = parser.parse_args(["host", "agent", "--git-name", "VM Name", "--git-email", "vm@example.test", "--git-identity", "none"])
        config = SetupConfig.from_args(args, "server_dev")
        restored = SetupConfig.from_dict("host", "server_dev", config.to_dict())
        for tokens in (restored.to_remote_args(), restored.to_setup_command()):
            self.assertIn("--git-name 'VM Name'", tokens)
            self.assertIn("--git-email vm@example.test", tokens)
            self.assertIn("--git-identity none", tokens)
        self.assertTrue(restored.has_agent_features())

    def test_target_overrides_work_without_a_payload_and_respect_dry_run(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", git_author_name="VM Name", git_author_email="vm@example.test")
        steps = []
        extend_agent_steps(config, steps)
        functions = [function.__name__ for _, function in steps]
        self.assertLess(functions.index("install_git_for_agent_repositories"), functions.index("configure_git_commit_identity"))
        with patch("common.agent_steps.os.path.isdir", return_value=False), patch("common.agent_steps.is_dry_run", return_value=False), patch("common.agent_steps._configure_git_identity") as configure:
            copy_agent_tooling_payload(config)
        configure.assert_called_once_with(config)
        with patch("common.agent_steps.is_dry_run", return_value=True), patch("common.agent_steps._configure_git_identity") as configure:
            configure_git_commit_identity(config)
        configure.assert_not_called()

    def test_leading_dash_identity_survives_generated_commands(self):
        config = SetupConfig(host="host", username="agent", system_type="server_dev", git_author_name="-Fixture", git_author_email="-fixture@example.test")
        parser = create_setup_argument_parser("setup")
        for parts in (config.to_remote_args(), config.to_setup_command()):
            # Parse the actual serialized identity flags in either generated command.
            flags = [part for part in parts if part.startswith(("--git-name", "--git-email"))]
            args = parser.parse_args(["host", "agent", *shlex.split(" ".join(flags))])
            self.assertEqual(args.git_author_name, "-Fixture")
            self.assertEqual(args.git_author_email, "-fixture@example.test")

    def test_invalid_identity_rejected_before_staging(self):
        for field, value in (("git_author_name", "name\ncommand"), ("git_author_email", "invalid"), ("git_identity_source", "all-config")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                SetupConfig(host="host", username="agent", system_type="server_dev", **{field: value})
