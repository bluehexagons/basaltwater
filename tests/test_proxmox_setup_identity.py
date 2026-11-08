"""Proxmox controller, saved configuration and target identity contracts."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from common import web_panel_steps
from lib.arg_parser import create_setup_argument_parser
from lib.config import SYSTEM_TYPES, SetupConfig
from lib.setup_common import prepare_web_panel_payload
from remote_setup import config_from_remote_args


class ProxmoxSetupIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parser, _, _ = create_basaltwater_parser()
        cls.remote_parser = create_setup_argument_parser(
            "Remote setup", for_remote=True, allow_steps=True,
        )

    def parse_config(self, *flags: str) -> SetupConfig:
        args = self.parser.parse_args([
            "setup", "server_proxmox", "192.0.2.10", *flags,
            "--timezone", "UTC",
        ])
        with patch("lib.system_utils.get_current_username", return_value="agent"):
            return SetupConfig.from_args(args, "server_proxmox")

    def receive_config(self, config: SetupConfig) -> SetupConfig:
        args = self.remote_parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
        with patch("remote_setup.resolve_machine_type", return_value="hardware"):
            return config_from_remote_args(args)

    def test_public_cli_normalizes_omitted_and_unused_username(self) -> None:
        for flags in ((), ("operator",), ("unused@controller",)):
            with self.subTest(flags=flags):
                config = self.parse_config(*flags)
                self.assertEqual(config.username, "root")
                self.assertEqual(config.to_dict()["username"], "root")
                self.assertIn("--username root", config.to_remote_args())
                self.assertEqual(self.receive_config(config).username, "root")
                self.assertEqual(shlex.split(" ".join(config.to_setup_command()))[4], "root")

    def test_saved_proxmox_identity_normalizes_before_defaults(self) -> None:
        for username in ("agent", "unused@controller", "root"):
            with self.subTest(username=username):
                saved = {"username": username, "device_pairing_providers": ["t3code"],
                         "device_pairing_auth_password": "test-password"}
                config = SetupConfig.from_dict("192.0.2.10", "server_proxmox", saved)
                self.assertEqual(config.username, "root")
                self.assertEqual(config.device_pairing_auth_username, "root")
                self.assertEqual(config.to_dict()["username"], "root")
                self.assertEqual(saved["username"], username)

    def test_direct_config_normalizes_before_pairing_defaults(self) -> None:
        config = SetupConfig(
            host="192.0.2.10", username="agent", system_type="server_proxmox",
            device_pairing_providers=["t3code"], device_pairing_auth_password="test-password",
        )
        self.assertEqual(config.device_pairing_auth_username, "root")

    def test_root_hardening_rejected_at_each_config_boundary(self) -> None:
        for flag in ("--harden-agent", "--harden-user"):
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(ValueError, "requires a non-root setup user"):
                    self.parse_config("agent", flag)
                with self.assertRaisesRegex(ValueError, "requires a non-root setup user"):
                    SetupConfig.from_dict("192.0.2.10", "server_proxmox", {
                        "username": "agent", flag[2:].replace("-", "_"): True,
                    })
                args = self.remote_parser.parse_args([
                    "--system-type", "server_proxmox", "--username", "agent", flag,
                    "--timezone", "UTC",
                ])
                with self.assertRaisesRegex(ValueError, "requires a non-root setup user"):
                    config_from_remote_args(args)

    def test_privilege_password_only_patch_uses_effective_identity(self) -> None:
        config = self.parse_config("agent", "--privilege-broker-password", "test-password-16")
        self.assertEqual(json.loads(config.privilege_broker_auth)["username"], "root")

    def test_other_profiles_preserve_supplied_identity(self) -> None:
        for profile in SYSTEM_TYPES:
            if profile == "server_proxmox":
                continue
            for username in ("agent", "root", None):
                with self.subTest(profile=profile, username=username):
                    flags = (
                        ["--ci-repository", "example/project", "--ci-allowed-user", "operator"]
                        if profile == "server_github_runner" else []
                    )
                    if profile.startswith("agent_"):
                        flags.extend(["--agent-tool", "codex"])
                    args = self.parser.parse_args([
                        "setup", profile, "192.0.2.10", *([username] if username else []),
                        "--timezone", "UTC", *flags,
                    ])
                    with patch("lib.system_utils.get_current_username", return_value="controller"):
                        config = SetupConfig.from_args(args, profile)
                    self.assertEqual(config.username, username or "controller")
                    restored = SetupConfig.from_dict(config.host, profile, config.to_dict())
                    self.assertEqual(restored.username, username or "controller")

    def test_password_payload_installs_for_target_root_and_keeps_exact_user_check(self) -> None:
        config = self.parse_config("--web-panel", "--web-panel-password", "test-password")
        with tempfile.TemporaryDirectory() as temporary:
            payload_dir = Path(temporary) / "payload"
            payload_dir.mkdir()
            config_dir = Path(temporary) / "config"
            auth_file = config_dir / "htpasswd"
            payload_file = payload_dir / "htpasswd"
            with patch("lib.setup_common.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout="$6$salt$hash\n",
            )) as generate:
                prepare_web_panel_payload(config, str(payload_dir))
            generate.assert_called_once_with(
                ["openssl", "passwd", "-6", "-stdin"], input="test-password\n",
                capture_output=True, text=True, timeout=20,
            )
            target = self.receive_config(config)
            self.assertNotIn("test-password", " ".join(config.to_remote_args()))
            self.assertNotIn("web_panel_auth_password", config.to_dict())
            with (
                patch.multiple(web_panel_steps, WEB_PANEL_CONFIG_DIR=str(config_dir),
                               WEB_PANEL_AUTH_FILE=str(auth_file),
                               WEB_PANEL_PAYLOAD_FILE=str(payload_file)),
                patch.object(web_panel_steps.pwd, "getpwnam", return_value=SimpleNamespace(pw_gid=os.getgid())),
                patch.object(web_panel_steps.os, "chown"),
            ):
                self.assertEqual(web_panel_steps._install_auth_file(target.username), (True, None))
                self.assertEqual(auth_file.read_text(), "root:$6$salt$hash\n")
                self.assertEqual(auth_file.stat().st_mode & 0o777, 0o640)
                for rejected in ("agent:$6$salt$hash\n", "root:$6$salt$hash\nagent:$6$salt$hash\n"):
                    payload_file.write_text(rejected)
                    with self.assertRaisesRegex(RuntimeError, "exactly the setup username"):
                        web_panel_steps._install_auth_file(target.username)
                    self.assertEqual(auth_file.read_text(), "root:$6$salt$hash\n")
