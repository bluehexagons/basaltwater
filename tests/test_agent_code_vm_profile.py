"""Regression coverage for the brokered graphical agent provisioning profile."""

from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import io
from pathlib import Path
import shlex
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from common import common_steps
from lib import setup_common
from lib.arg_parser import add_setup_arguments
from lib.config import SetupConfig
from lib.notifications import _webhook_request_target
from plugins.workstation import build_workstation_steps


TOKEN = "0123456789abcdef0123456789abcdef"
PROFILE = [
    "setup", "agent_code_vm", "192.168.0.42", "agent",
    "--provision-on", "192.168.0.2", "--name", "agent",
    "--password", "test-only user password",
    "--device-pairing-password", "test-only pairing password",
    "--privilege-broker", "--privilege-broker-password", "test-only broker password",
    "--web-panel", "--ssl", "--web-panel-password", "test-only panel password",
    "--notify", "webhook", "https://logging.example.invalid/events",
    "--notify", "webhook", f"https://panel.example.invalid/api/v1/notifications#{TOKEN}",
    "--image-storage", "local", "--memory", "1.5G", "--balloon-min", "1.5G",
    "--cores", "2", "--storage", "root", "local-lvm", "32G",
    "--no-disk-ssd", "root", "--no-agent-tool", "gh", "--av-tools",
    "--browser-automation", "playwright", "--node", "--lan-access",
]


def profile() -> SetupConfig:
    parser, _, _ = create_basaltwater_parser()
    return SetupConfig.from_args(parser.parse_args(PROFILE), "agent_code_vm")


class AgentCodeVMProfileTests(unittest.TestCase):
    def test_profile_survives_remote_argument_roundtrip_and_step_composition(self):
        config = profile()
        self.assertEqual(config.selected_agent_tools(), ["codex"])
        self.assertIsNone(config.git_auth_source)
        parser = argparse.ArgumentParser()
        add_setup_arguments(parser, for_remote=True)
        args = parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
        args.host = "localhost"
        received = SetupConfig.from_args(args, "agent_code_vm")
        self.assertEqual(received.selected_agent_tools(), ["codex"])
        self.assertEqual(received.privilege_broker_auth, config.privilege_broker_auth)
        self.assertEqual(received.privilege_broker_host, config.host)
        self.assertEqual(received.notify_specs, config.notify_specs)
        self.assertEqual(received.web_panel_port, 443)
        self.assertFalse(received.web_panel_notification_ingest)
        self.assertEqual(received.browser_automation, "playwright")
        names = [step.__name__ for _, step in build_workstation_steps(received)]
        self.assertNotIn("install_github_cli", names)
        for name in ("install_codex", "install_t3code_web", "configure_privilege_broker",
                     "configure_web_panel", "install_node", "install_browser_automation", "install_av_tools"):
            self.assertIn(name, names)
        self.assertLess(names.index("configure_agent_user_security"), names.index("install_t3code_web"))
        self.assertLess(names.index("configure_privilege_broker"), names.index("configure_web_panel"))

    def test_existing_panel_bearer_token_is_removed_from_request_url(self):
        config = profile()
        url, token = _webhook_request_target(config.notify_specs[1][1])
        self.assertEqual(url, "https://panel.example.invalid/api/v1/notifications")
        self.assertEqual(token, TOKEN)

    def test_brokered_user_setup_never_regrants_sudo_on_rerun(self):
        config = profile()
        with patch.object(common_steps, "run", return_value=SimpleNamespace(returncode=0)) as run, \
             patch.object(common_steps, "set_user_password", return_value=True), \
             patch.object(common_steps, "_ensure_vm_setup_user_sudoers"), redirect_stdout(io.StringIO()):
            common_steps.setup_user(config)
            common_steps.setup_user(config)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertNotIn("usermod -aG sudo agent", commands)
        self.assertEqual(commands.count("gpasswd -d agent sudo"), 2)

    def test_group_writable_checkout_stages_safe_runtime_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            (source / "lib").mkdir(parents=True)
            (source / "lib").chmod(0o775)
            module = source / "lib/module.py"
            module.write_text("# fixture\n")
            module.chmod(0o664)
            entry = source / "basaltwater.py"
            entry.write_text("#!/usr/bin/env python3\n")
            entry.chmod(0o775)
            target.mkdir(mode=0o700)
            with patch.object(setup_common, "SCRIPT_DIR", str(source / "lib")), \
                 patch.object(setup_common, "write_setup_snapshot_metadata"):
                setup_common.copy_project_files(str(target))
            self.assertEqual(stat.S_IMODE((target / "lib").stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((target / "lib/module.py").stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE((target / "basaltwater.py").stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(module.stat().st_mode), 0o664)
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o775)
            with tarfile.open(fileobj=io.BytesIO(setup_common.create_tar_from_dir(str(target))), mode="r:gz") as archive:
                self.assertFalse(any(member.mode & 0o022 for member in archive))

    def test_remote_transport_disables_bytecode_without_putting_secrets_in_ssh(self):
        config = profile()
        with patch.object(setup_common, "resource_lock", return_value=nullcontext()), \
             patch.object(setup_common, "get_ssh_control_path", return_value=None), \
             patch.object(setup_common, "ensure_remote_sudo", return_value=True), \
             patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "prepare_agent_payload"), \
             patch.object(setup_common, "prepare_device_pairing_payload"), \
             patch.object(setup_common, "prepare_web_panel_payload"), \
             patch.object(setup_common, "build_ssh_command", return_value=["ssh"]) as ssh, \
             patch.object(setup_common, "run_streamed", return_value=0), redirect_stdout(io.StringIO()):
            self.assertEqual(setup_common.run_remote_setup(config), 0)
        command = ssh.call_args.kwargs["remote_command"]
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", command)
        self.assertNotIn(TOKEN, command)
        self.assertNotIn("test-only", command)


if __name__ == "__main__":
    unittest.main()
