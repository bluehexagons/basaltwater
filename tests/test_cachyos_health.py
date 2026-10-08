"""Selection, privacy, and host-health observations without real host probes."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from lib import cachyos_doctor as doctor
from lib import cachyos_health as health
from lib.config import SetupConfig


class HealthTests(unittest.TestCase):
    def test_sunshine_failed_service_is_reported_without_launching_it(self):
        calls = []

        def probe(command, uid):
            calls.append(command)
            return "ok", "ActiveState=failed\nResult=core-dump\nprivate=not-published\n"

        with patch.object(health.shutil, "which", return_value=None):
            # Unexpected fields cannot be interpreted as successful observations.
            result = health.collect_sunshine_health(probe, 1000, bus_ready=True)
            self.assertEqual(result[0][1], "deferred")
            result = health.collect_sunshine_health(
                lambda *_: ("ok", "ActiveState=failed\nResult=core-dump\n"), 1000, bus_ready=True)
            self.assertEqual(result[0][1], "failed")
            self.assertEqual(result[2][1], "deferred")
        self.assertEqual(calls[0], ["/usr/bin/systemctl", "--user", "show",
                                   "app-dev.lizardbyte.app.Sunshine.service",
                                   "--property=ActiveState", "--property=Result"])
        self.assertNotIn("not-published", str(result))

    def test_sunshine_inactive_or_unknown_is_expected_without_bus_activation(self):
        with patch.object(health.shutil, "which", return_value=None):
            for status, output in (("ok", "ActiveState=inactive\nResult=success\n"), ("error", "private")):
                result = health.collect_sunshine_health(lambda *_: (status, output), 1000, bus_ready=True)
                self.assertEqual(result[0][1], "deferred")
            with patch("lib.cachyos_doctor._probe") as probe:
                health.collect_sunshine_health(probe, 1000, bus_ready=False)
                probe.assert_not_called()

    def test_vaapi_encoding_profile_is_observed_without_live_encoding(self):
        node = Path("/dev/dri/renderD128")
        calls = []

        def probe(command, uid):
            calls.append(command)
            if "vainfo" in command[0]:
                return "ok", "VAProfileH264High : VAEntrypointEncSliceLP\nprivate driver text\n"
            if "--property=UnitFileState" in command:
                return "ok", "UnitFileState=enabled\n"
            return "ok", "ActiveState=active\nResult=success\n"

        with patch.object(health.shutil, "which", return_value="/usr/bin/vainfo"), \
                patch.object(Path, "glob", return_value=[node]), \
                patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o660)):
            result = health.collect_sunshine_health(probe, 1000, bus_ready=True)
        self.assertEqual([state for _, state, _ in result], ["available", "available", "available"])
        self.assertIn(["/usr/bin/vainfo", "--display", "drm", "--device", str(node)], calls)
        self.assertNotIn("private driver", str(result))
        self.assertNotIn(str(node), str(result))

    def test_vaapi_decode_only_errors_and_symlinks_never_establish_encoding(self):
        node = Path("/dev/dri/renderD128")
        with patch.object(health.shutil, "which", return_value="/usr/bin/vainfo"), \
                patch.object(Path, "glob", return_value=[node]):
            for mode, output in ((stat.S_IFCHR, "VAProfileH264High : VAEntrypointVLD\n"),
                                 (stat.S_IFLNK, "VAProfileH264High : VAEntrypointEncSlice\n")):
                with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode)):
                    result = health.collect_sunshine_health(lambda *_: ("ok", output), 1000, bus_ready=False)
                    self.assertEqual(result[2][1], "deferred")

    def test_sunshine_login_enablement_is_separate_from_session_activity(self):
        cases = [("enabled", "available"), ("disabled", "failed"), ("masked", "failed"),
                 ("enabled-runtime", "failed"), ("", "deferred"), ("private-secret", "deferred")]
        with patch.object(health.shutil, "which", return_value=None):
            for value, expected in cases:
                def probe(command, uid):
                    return ("ok", f"UnitFileState={value}\n") if "--property=UnitFileState" in command else (
                        "ok", "ActiveState=inactive\nResult=success\n")
                result = {name: (state, reason) for name, state, reason in
                          health.collect_sunshine_health(probe, 1000, bus_ready=True)}
                self.assertEqual(result["service.sunshine"][0], "deferred")
                self.assertEqual(result["startup.sunshine"][0], expected)
                self.assertNotIn("private-secret", str(result))

    def test_duplicate_sunshine_properties_cannot_establish_service_health(self):
        with patch.object(health.shutil, "which", return_value=None):
            result = health.collect_sunshine_health(lambda *_: (
                "ok", "ActiveState=failed\nActiveState=active\nResult=success\n"), 1000, bus_ready=True)
        self.assertEqual(result[0][1], "deferred")

    def test_t3_permissions_are_checked_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(health.collect_t3_storage_health(home, os.getuid())[0][1], "deferred")
            root = home / ".t3"
            root.mkdir(mode=0o700)
            userdata = root / "userdata"
            userdata.mkdir(mode=0o700)
            token = userdata / "clerk-tokens.json"
            with patch.object(Path, "read_text", side_effect=AssertionError("Must not open data")):
                self.assertEqual(health.collect_t3_storage_health(home, os.getuid())[0][1], "available")
                token.write_text("private token fixture")
                token.chmod(0o666)
                advisory = health.collect_t3_storage_health(home, os.getuid())
                self.assertEqual(advisory[0][1], "deferred")
                self.assertIn("private parent directories", advisory[0][2])
                token.chmod(0o600)
                self.assertEqual(health.collect_t3_storage_health(home, os.getuid())[0][1], "available")
                root.chmod(0o755)
                result = health.collect_t3_storage_health(home, os.getuid())
                self.assertEqual(result[0][1], "failed")
                self.assertNotIn(temporary, str(result))
                self.assertNotIn("private token fixture", str(result))

    def test_t3_symlink_and_foreign_ownership_are_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / ".t3"
            outside = home / "outside"
            outside.mkdir(mode=0o700)
            root.symlink_to(outside)
            self.assertEqual(health.collect_t3_storage_health(home, os.getuid())[0][1], "failed")
            root.unlink()
            root.mkdir(mode=0o700)
            self.assertEqual(health.collect_t3_storage_health(home, os.getuid() + 1)[0][1], "failed")

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

    def test_broad_allows_behind_full_protocol_guards_are_distinguished(self):
        rules = "\n".join([
            "-A ufw-user-input -p tcp --dport 3773 -s 192.168.68.0/22 -j ACCEPT",
            "-A ufw-user-input -p tcp --dport 3773 -j DROP",
            "-A ufw-user-input -p udp --dport 3773 -j DROP",
            "-A ufw-user-input -p tcp --dport 3773 -j ACCEPT",
            "-A ufw-user-input -p udp --dport 3773 -j ACCEPT",
        ])
        with patch.object(Path, "read_text", return_value=rules):
            result = health.collect_network_health(lambda *_: ("ok", ""), 1000)[-1]
        self.assertEqual(result[1], "deferred")
        self.assertIn("follow covering deny", result[2])
        self.assertIn("privileged verification", result[2])
        self.assertEqual(health._saved_remote_allows(rules.replace("ufw-user", "ufw6-user"),
                                                 "ufw6-user-input", 3773), (False, True))
        for broken in (rules.replace("-p udp --dport 3773 -j DROP", "-p udp --dport 3389 -j DROP"),
                       "-A ufw-user-input -j ACCEPT\n" + rules,
                       rules.replace("--dport 3773 -j DROP", "--dport 3773 -d 192.168.68.57 -j DROP"),
                       rules.replace("--dport 3773 -j DROP", "--dport 3773 -i wlan0 -j DROP")):
            self.assertTrue(health._saved_remote_allows(broken, "ufw-user-input", 3773)[0])

    def test_firewall_range_all_port_and_udp_allows_are_checked_only_in_input_chains(self):
        for rule in ("-p tcp -m multiport --dports 3000:5000", "", "-p udp --dport 47999",
                     "-p tcp --dport 47989", "-p tcp --dport 4001"):
            self.assertTrue(health._saved_remote_allows(
                f"-A ufw-user-input {rule} -j ACCEPT", "ufw-user-input", 4001)[0])
        for rule in ("-A ufw-user-output -p tcp --dport 3773 -j ACCEPT",
                     "-A ufw-user-input -p tcp --dport 1716 -j ACCEPT",
                     "-A ufw-user-input -p tcp --dport 3773 -s 192.168.68.0/22 -j ACCEPT"):
            self.assertEqual(health._saved_remote_allows(rule, "ufw-user-input", 3773), (False, False))

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

    def test_mirror_refresh_observations_are_read_only_and_do_not_claim_freshness(self):
        cases = [
            ("ok", "LoadState=loaded\nActiveState=failed\nResult=exit-code\n", "failed"),
            ("ok", "LoadState=loaded\nActiveState=inactive\nResult=timeout\n", "failed"),
            ("ok", "LoadState=loaded\nActiveState=inactive\nResult=success\n", "available"),
            ("ok", "LoadState=loaded\nActiveState=activating\nResult=success\n", "deferred"),
            ("ok", "LoadState=not-found\nActiveState=inactive\nResult=success\n", "deferred"),
            ("ok", "LoadState=masked\nActiveState=inactive\nResult=success\n", "deferred"),
            ("ok", "LoadState=loaded\nActiveState=inactive\nResult=private-result\n", "deferred"),
            ("ok", "LoadState=loaded\nActiveState=inactive\nResult=success\nprivate=secret\n", "deferred"),
            ("ok", "LoadState=loaded\nActiveState=inactive\nResult=success\nResult=success\n", "deferred"),
            ("ok", "private=secret\n", "deferred"),
            ("error", "private=secret\n", "deferred"),
        ]
        for status, output, expected in cases:
            calls = []

            def probe(command, uid):
                calls.append(command)
                self.assertEqual(uid, 1000)
                return (status, output) if "show" in command else ("ok", "")

            with self.subTest(output=output), \
                    patch.object(health.shutil, "disk_usage", return_value=SimpleNamespace(free=10 * 1024 ** 3)), \
                    patch.object(Path, "is_dir", return_value=True), \
                    patch.object(health.platform, "release", return_value="7.2-test"):
                result = {name: (state, reason) for name, state, reason in health.collect_host_health(probe, 1000)}
            self.assertEqual(result["health.mirrors"][0], expected)
            self.assertIn(["/usr/bin/systemctl", "show", "cachyos-rate-mirrors.service",
                           "--property=LoadState", "--property=ActiveState", "--property=Result"], calls)
            self.assertFalse(any(set(command) & {"sudo", "start", "restart", "reset-failed", "journalctl"}
                                 for command in calls))
            self.assertNotIn("secret", str(result))
            self.assertNotIn("private-result", str(result))
            if expected == "available":
                self.assertIn("freshness and connectivity are not verified", result["health.mirrors"][1])
            elif expected == "failed":
                self.assertIn("DNS/connectivity", result["health.mirrors"][1])

    def test_capacity_checks_home_separately_and_redacts_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            def usage(path):
                return SimpleNamespace(free=(20 if path == "/" else 1) * 1024 ** 3)
            with patch.object(health.shutil, "disk_usage", side_effect=usage) as check:
                result = {name: (state, reason) for name, state, reason in
                          health.collect_host_health(lambda *_: ("error", ""), 1000, home=home)}
            self.assertEqual(result["health.capacity"][0], "available")
            self.assertEqual(result["health.home-capacity"][0], "failed")
            check.assert_any_call(str(home))
            self.assertNotIn(temporary, str(result))
            with patch.object(health.shutil, "disk_usage", side_effect=PermissionError):
                result = {name: state for name, state, _ in
                          health.collect_host_health(lambda *_: ("error", ""), 1000, home=home)}
            self.assertEqual(result["health.home-capacity"], "deferred")

    def test_selected_missing_tools_packages_and_service_fail(self):
        config = SetupConfig(system_type="agent_cachyos", host="localhost", username="human",
                             web_interfaces=["t3code"], install_sunshine=True, agent_tools=["codex"])
        with ExitStack() as stack:
            home = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(doctor.os, "getuid", return_value=1000))
            stack.enter_context(patch.object(doctor.os, "geteuid", return_value=1000))
            stack.enter_context(patch.object(doctor.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="human", pw_dir=home)))
            stack.enter_context(patch.object(doctor, "is_cachyos", return_value=True))
            stack.enter_context(patch.object(doctor.platform, "machine", return_value="x86_64"))
            stack.enter_context(patch.object(doctor, "_owned_socket", return_value=True))
            stack.enter_context(patch.object(doctor, "_probe", return_value=("error", "private")))
            stack.enter_context(patch.object(doctor.shutil, "which", return_value=None))
            stack.enter_context(patch.object(health, "collect_host_health", return_value=[]))
            stack.enter_context(patch.object(health, "collect_network_health", return_value=[]))
            stack.enter_context(patch.object(health, "collect_sunshine_health", return_value=[]))
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
