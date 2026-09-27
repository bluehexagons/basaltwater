"""Selection, privacy, and host-health observations without real host probes."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import cachyos_doctor as doctor
from lib import cachyos_health as health
from lib.config import SetupConfig


class HealthTests(unittest.TestCase):
    def test_listeners_and_saved_rules_are_advisory_and_redacted(self):
        output = "LISTEN 0 511 0.0.0.0:3773 0.0.0.0:*\nLISTEN 0 511 127.0.0.1:47990 0.0.0.0:*\n"
        rules = "-A ufw-user-input -p tcp --dport 3773 -j ACCEPT\n# private-personal-data\n"
        with patch.object(Path, "read_text", return_value=rules):
            result = {name: (state, reason) for name, state, reason in
                      health.collect_network_health(lambda *_: ("ok", output), 1000)}
        self.assertEqual(result["network.t3"][0], "deferred")
        self.assertEqual(result["network.sunshine"][0], "available")
        self.assertIn("Unrestricted", result["network.firewall"][1])
        self.assertNotIn("private-personal", str(result))
        self.assertNotIn("0.0.0.0", str(result))

    def test_unavailable_listener_probe_never_claims_no_exposure(self):
        with patch.object(Path, "read_text", side_effect=PermissionError):
            result = health.collect_network_health(lambda *_: ("error", "private"), 1000)
        self.assertTrue(all(state == "deferred" for _, state, _ in result))
        self.assertIn("unknown", result[-1][2])

    def test_host_failures_are_bounded_summaries_not_raw_names(self):
        def probe(command, uid):
            if "--failed" in command:
                return "ok", "private-service.service loaded failed failed secret\n"
            if "-Qu" in command:
                return "ok", "package 1 -> 2\n"
            return "ok", "/dev/nvme0n1p2\n"
        with patch.object(health.shutil, "disk_usage", return_value=SimpleNamespace(free=1024)), \
                patch.object(Path, "is_dir", return_value=False), \
                patch.object(health.platform, "release", return_value="7.2-test"):
            result = {name: (state, reason) for name, state, reason in health.collect_host_health(probe, 1000)}
        self.assertEqual(result["health.system-units"][0], "failed")
        self.assertEqual(result["health.capacity"][0], "failed")
        self.assertIn("stale", result["health.updates"][1])
        self.assertIn("without a dm-crypt", result["health.encryption"][1])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("nvme", str(result))

    def test_selected_missing_tools_packages_and_service_fail(self):
        config = SetupConfig(system_type="agent_cachyos", host="localhost", username="human",
                             web_interfaces=["t3code"], install_sunshine=True, agent_tools=["codex"])
        with ExitStack() as stack:
            stack.enter_context(patch.object(doctor.os, "getuid", return_value=1000))
            stack.enter_context(patch.object(doctor.os, "geteuid", return_value=1000))
            stack.enter_context(patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="human")))
            stack.enter_context(patch.object(doctor, "is_cachyos", return_value=True))
            stack.enter_context(patch.object(doctor.platform, "machine", return_value="x86_64"))
            stack.enter_context(patch.object(doctor, "_owned_socket", return_value=True))
            stack.enter_context(patch.object(doctor, "_probe", return_value=("error", "private")))
            stack.enter_context(patch.object(doctor.shutil, "which", return_value=None))
            stack.enter_context(patch.object(health, "collect_host_health", return_value=[]))
            stack.enter_context(patch.object(health, "collect_network_health", return_value=[]))
            records = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
            with patch.object(doctor.shutil, "which", return_value="/fixture/node"), \
                    patch.object(doctor, "_probe", return_value=("ok", "v26.10.0\n")):
                config.install_node = True
                versions = {item["name"]: item for item in doctor.collect_cachyos_doctor(config=config)["capabilities"]}
                self.assertEqual(versions["tool.node"]["version"], "26.10.0")
        self.assertEqual(records["service.t3code"]["state"], "failed")
        self.assertEqual(records["tool.git"]["state"], "failed")
        self.assertEqual(records["package.sunshine"]["state"], "failed")
        self.assertFalse(records["package.firefox"]["selected"])
        self.assertEqual(records["package.firefox"]["state"], "deferred")


if __name__ == "__main__":
    unittest.main()
