"""Browser context separation and existing-version-only provisioning checks."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_browser, agent_cli
from lib.agent_readiness import build_agent_readiness_record


class TestAgentBrowser(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "project"
        self.project.mkdir()
        self.managed = self.root / "managed"
        self.enterContext(patch.object(agent_browser, "PLAYWRIGHT_ROOT", str(self.managed)))
        self.node = self.root / "node"
        self.node.write_text("node placeholder")
        self.node.chmod(0o755)
        self.which = self.enterContext(patch.object(agent_browser.shutil, "which", side_effect=lambda name: str(self.node) if name == "node" else None))
        self.execute = self.enterContext(patch.object(agent_browser, "run_command"))
        self.enterContext(patch.object(agent_browser, "browser_registrations", return_value={"codex": False, "opencode": False}))

    def package(self, root: Path, version: str, name: str = "playwright") -> Path:
        module = root / "node_modules" / name
        module.mkdir(parents=True)
        (module / "package.json").write_text(json.dumps({"name": name, "version": version}))
        (module / "cli.js").write_text("existing CLI")
        return module

    def smoke(self, engine: str = "firefox") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 0, json.dumps({"healthy": True, "engine": engine, "browser_version": "140.0"}), "")

    def test_discovery_distinguishes_all_contexts_without_browser_execution(self) -> None:
        self.package(self.managed, "1.60.0")
        self.package(self.project, "1.57.2")
        self.which.side_effect = lambda name: "/usr/bin/firefox-esr" if name == "firefox-esr" else None
        self.execute.return_value = subprocess.CompletedProcess([], 0, "Mozilla Firefox 140.0esr", "")
        result = agent_browser.browser_context_inventory(str(self.project))
        contexts = {(item["context"], item["engine"]): item for item in result}
        self.assertEqual(contexts[("managed", "chromium")]["playwright_version"], "1.60.0")
        self.assertEqual(contexts[("project", "firefox")]["playwright_version"], "1.57.2")
        self.assertEqual(contexts[("interactive-desktop", "firefox")]["readiness"], "unverified")
        self.assertEqual(contexts[("collaborative-preview", "unknown")]["network_origin"], "connected-client")
        self.assertEqual(contexts[("interactive-desktop", "firefox")]["browser_version"], "Mozilla Firefox 140.0esr")
        self.assertIsNone(contexts[("managed", "firefox")]["launchable"])
        self.assertFalse(contexts[("managed", "firefox")]["mcp_registered"])
        self.assertFalse(contexts[("managed", "firefox")]["application_tested"])
        self.assertEqual(self.execute.call_args.args[0], ["/usr/bin/firefox-esr", "--version"])

    def test_flatpak_only_firefox_discovery_does_not_claim_launch_or_esr(self) -> None:
        self.which.side_effect = lambda name: "/usr/bin/flatpak" if name == "flatpak" else None
        self.execute.return_value = subprocess.CompletedProcess([], 0, "144.0", "")
        result = agent_browser.desktop_firefox_inventory()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["installation"], "flatpak")
        self.assertEqual(result[0]["browser_version"], "144.0")
        self.assertEqual(result[0]["channel"], "unknown")
        self.assertTrue(result[0]["discovered"])
        self.assertIsNone(result[0]["launchable"])
        self.assertFalse(result[0]["application_tested"])
        self.assertEqual(self.execute.call_args.args[0], ["/usr/bin/flatpak", "info", "--show-version", "org.mozilla.firefox"])

    def test_failed_flatpak_probe_never_claims_firefox_discovery(self) -> None:
        self.which.side_effect = lambda name: "/usr/bin/flatpak" if name == "flatpak" else None
        for failure in (subprocess.CompletedProcess([], 1, "", "not installed"), OSError("failed"), agent_browser.CommandTimeoutError("flatpak", 5)):
            with self.subTest(failure=failure):
                self.execute.side_effect = failure if isinstance(failure, Exception) else None
                self.execute.return_value = failure
                self.assertFalse(agent_browser.desktop_firefox_inventory()[0]["discovered"])

    def test_legacy_cache_directory_does_not_establish_runtime_readiness(self) -> None:
        (self.project / ".cache/ms-playwright/firefox-1234").mkdir(parents=True)
        result = agent_browser.browser_context_inventory(str(self.project))
        self.assertTrue(all(item["readiness"] == "runtime-missing" for item in result if item.get("headless")))
        self.execute.assert_not_called()

    def test_project_doctor_uses_selected_installed_version_and_cache(self) -> None:
        self.package(self.managed, "1.60.0")
        module = self.package(self.project, "1.57.2")
        self.execute.return_value = self.smoke()
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "0", "NODE_OPTIONS": "--require=preload", "NODE_PATH": "/global/modules", "NODE_EXTRA_CA_CERTS": "/trusted/ca.pem"}):
            result = agent_browser.check_browser("firefox", "project", str(self.project))
        self.assertTrue(result["healthy"])
        self.assertTrue(result["rendering_verified"])
        self.assertFalse(result["application_verified"])
        self.assertEqual(result["playwright_version"], "1.57.2")
        command = self.execute.call_args.args[0]
        self.assertEqual(command[0], str(self.node))
        self.assertEqual(command[-2:], [str(module), "firefox"])
        environment = self.execute.call_args.kwargs["env"]
        self.assertEqual(environment["PLAYWRIGHT_BROWSERS_PATH"], "0")
        self.assertEqual(environment["NODE_EXTRA_CA_CERTS"], "/trusted/ca.pem")
        self.assertNotIn("NODE_OPTIONS", environment)
        self.assertNotIn("NODE_PATH", environment)

    def test_optional_install_only_downloads_selected_engine_then_verifies(self) -> None:
        module = self.package(self.project, "1.57.2", "@playwright/test")
        source_files = [self.project / "package.json", self.project / "pnpm-lock.yaml"]
        for source in source_files:
            source.write_text("unchanged source")
        self.execute.side_effect = [subprocess.CompletedProcess([], 0, "download complete", ""), self.smoke()]
        result = agent_browser.check_browser("firefox", "project", str(self.project), install=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["payload_install_completed"])
        calls = self.execute.call_args_list
        self.assertEqual(calls[0].args[0], [str(self.node), str(module / "cli.js"), "install", "firefox"])
        self.assertEqual(calls[0].kwargs["env"]["PLAYWRIGHT_SKIP_BROWSER_GC"], "1")
        self.assertEqual(calls[1].args[0][-1], "firefox")
        self.assertTrue(all(source.read_text() == "unchanged source" for source in source_files))

    def test_managed_environment_uses_fixed_cache_without_changing_project_cache(self) -> None:
        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/project/cache"}):
            result = agent_browser._environment("managed")
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "/project/cache")
        self.assertEqual(result["PLAYWRIGHT_BROWSERS_PATH"], os.path.expanduser("~/.cache/ms-playwright"))

    def test_failed_install_returns_failure_without_attempting_smoke(self) -> None:
        self.package(self.project, "1.57.2")
        self.execute.return_value = subprocess.CompletedProcess([], 1, "", "Missing libraries")
        result = agent_browser.check_browser("firefox", "project", str(self.project), install=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["issue"], "browser_install_failed")
        self.assertEqual(self.execute.call_count, 1)

    def test_missing_payload_is_actionable_and_not_automatically_installed(self) -> None:
        self.package(self.project, "1.57.2")
        self.execute.return_value = subprocess.CompletedProcess([], 1, "", "Executable doesn't exist at /cache/firefox")
        result = agent_browser.check_browser("firefox", "project", str(self.project))
        self.assertEqual(result["issue"], "browser_payload_missing")
        self.assertEqual(result["remediation"], "install_selected_browser_payload")
        self.assertFalse(result["rendering_verified"])
        self.assertEqual(self.execute.call_count, 1)

    def test_missing_runtime_does_not_fall_back_to_managed_or_global_modules(self) -> None:
        self.package(self.managed, "1.60.0")
        result = agent_browser.check_browser("firefox", "project", str(self.project), install=True)
        self.assertEqual(result["issue"], "runtime_missing")
        self.execute.assert_not_called()

    def test_pnpm_package_link_keeps_project_runtime(self) -> None:
        actual = self.package(self.root / "pnpm", "1.57.2")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules/playwright").symlink_to(actual)
        self.execute.return_value = self.smoke()
        result = agent_browser.check_browser("firefox", "project", str(self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(self.execute.call_args.args[0][-2], str(actual))

    def test_wrong_engine_or_invalid_smoke_output_never_reports_healthy(self) -> None:
        self.package(self.project, "1.57.2")
        for response in (self.smoke("chromium"), subprocess.CompletedProcess([], 0, "not JSON", "")):
            with self.subTest(response=response.stdout):
                self.execute.return_value = response
                self.assertFalse(agent_browser.check_browser("firefox", "project", str(self.project))["healthy"])

    def test_timeout_and_os_failure_return_actionable_failure(self) -> None:
        self.package(self.project, "1.57.2")
        self.execute.side_effect = agent_browser.CommandTimeoutError("node", 180)
        result = agent_browser.check_browser("firefox", "project", str(self.project))
        self.assertEqual(result["issue"], "browser_timeout")
        self.execute.side_effect = OSError("Cannot launch Node")
        self.assertEqual(agent_browser.check_browser("firefox", "project", str(self.project))["issue"], "browser_check_failed")

    def test_command_dispatch_emits_json_and_nonzero_for_invalid_context_inputs(self) -> None:
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest="command"))
        for arguments in (["--context", "project"], ["--context", "managed", "--project", str(self.project)]):
            args = parser.parse_args(["agent", "browser", "doctor", *arguments, "--json"])
            with redirect_stdout(output := StringIO()):
                self.assertEqual(agent_cli.run_agent_command(args), 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result["issue"], "invalid_browser_request")
            self.assertEqual(result["remediation"], "check_context_and_project_path")
        self.execute.assert_not_called()

    def test_readiness_retains_context_without_arbitrary_private_values(self) -> None:
        boot = self.root / "boot-id"
        boot.write_text("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        for values, expected in (
            ({"engine": "chromium", "context": "managed", "network_origin": "host"},
             {"engine": "chromium", "context": "managed", "network_origin": "host"}),
            ({"engine": "/private/source", "context": "secret context", "network_origin": "https://private/"},
             {"engine": None, "context": None, "network_origin": None}),
            ({}, {"engine": None, "context": None, "network_origin": None}),
        ):
            with self.subTest(values=values):
                record = build_agent_readiness_record([], [{"capability": "browser", "healthy": True, **values}],
                                                       trigger="manual", boot_id_path=str(boot))
                context = record["capabilities"][0]
                self.assertEqual({key: context[key] for key in expected}, expected)

    def test_verbose_execution_preserves_json_stdout(self) -> None:
        self.package(self.project, "1.57.2")
        def verbose_run(*arguments, **kwargs):
            print("Running browser diagnostic")
            return self.smoke()
        self.execute.side_effect = verbose_run
        args = argparse.Namespace(engine="firefox", context="project", project=str(self.project), browser_command="doctor", json=True)
        with redirect_stdout(output := StringIO()), patch.object(agent_browser.sys, "stderr", StringIO()) as errors:
            self.assertEqual(agent_browser.run_browser_command(args), 0)
        self.assertTrue(json.loads(output.getvalue())["healthy"])
        self.assertIn("Running browser diagnostic", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
