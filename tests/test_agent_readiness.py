"""Tests for durable redacted agent readiness evidence."""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from lib.agent_cli import (
    DEFAULT_DOCTOR_TOOLS,
    _record_post_update_readiness,
    add_agent_subparser,
    run_agent_command,
)
from lib.agent_readiness import (
    build_agent_readiness_record,
    format_agent_readiness_record,
    load_agent_readiness_record,
    record_agent_readiness,
)


_BOOT_ONE = "11111111-1111-4111-8111-111111111111"
_BOOT_TWO = "22222222-2222-4222-8222-222222222222"


def _write_boot_id(directory: str, value: str) -> str:
    path = os.path.join(directory, "boot-id")
    with open(path, "w", encoding="ascii") as file_obj:
        file_obj.write(value + "\n")
    return path


def _host_result() -> dict[str, object]:
    return {
        "capability": "host",
        "healthy": True,
        "status": "warning",
        "memory": {"total_bytes": 4},
        "disk": {"path": "/secret/home", "free_bytes": 5},
        "agent_storage": {
            "paths": {"npm_cache": "/secret/home/.npm"},
            "size_bytes": {"npm_cache": 6},
            "codex_release_count": 2,
        },
        "t3_service": {"memory_current_bytes": 7},
        "maintenance": {},
        "maintenance_hold": {"status": "inactive", "active": False},
        "reboot_pending": False,
        "warnings": ["capacity warning"],
        "errors": [],
    }


class TestAgentReadinessState(unittest.TestCase):
    def test_record_omits_paths_identity_and_credential_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            boot_id = _write_boot_id(temp_dir, _BOOT_ONE)
            secret = "never-print-readiness-secret"
            record = build_agent_readiness_record(
                [
                    {
                        "tool": "codex",
                        "installed": True,
                        "path": "/secret/home/.local/bin/codex",
                        "version": "codex 1.2.3",
                        "credential": True,
                        "credential_healthy": True,
                        "credential_status": {
                            "status": "current",
                            "auth_mode": "chatgpt",
                            "access_token": secret,
                        },
                    }
                ],
                [
                    _host_result(),
                    {
                        "capability": "t3code",
                        "healthy": True,
                        "checks": {"service_active": True},
                        "runtime": "/secret/home/.t3/bin.mjs",
                        "version": "t3 1.2.3",
                        "git_identity": {"name": secret, "email": secret},
                        "service_log": "/secret/home/t3.log",
                        "fixes": [],
                    },
                    {
                        "capability": "browser",
                        "healthy": True,
                        "installed": True,
                        "launchers_secure": True,
                        "launcher_features": {
                            "browser_selection": True,
                            "private_evidence": True,
                            "bounded_evidence": True,
                            "coordinate_input": True,
                            "webgl_settle_delay": True,
                            "private_path": "/secret/browser-output",
                        },
                        "managed_defaults": True,
                        "running_processes": {
                            "total": 1,
                            "stale": 0,
                            "inspected": True,
                            "private_pid": 123,
                        },
                        "registrations": {"codex": True},
                        "workflow_skills": [
                            "infra-tools-playwright-testing",
                            "/secret/browser-skill",
                        ],
                        "workflow_skill_ready": True,
                        "configured": True,
                        "smoke_test": True,
                        "issues": [],
                        "remediation": None,
                        "path": "/secret/browser",
                    },
                ],
                trigger="manual",
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                boot_id_path=boot_id,
            )

        rendered = json.dumps(record)
        self.assertTrue(record["healthy"])
        self.assertEqual(record["boot_id"], _BOOT_ONE)
        self.assertNotIn("/secret", rendered)
        self.assertNotIn(secret, rendered)
        self.assertFalse(record["privacy"]["paths_included"])
        self.assertFalse(record["privacy"]["user_identity_included"])
        browser = next(
            capability
            for capability in record["capabilities"]
            if capability["capability"] == "browser"
        )
        self.assertEqual(
            browser["launcher_features"],
            {
                "browser_selection": True,
                "private_evidence": True,
                "bounded_evidence": True,
                "coordinate_input": True,
                "webgl_settle_delay": True,
            },
        )
        self.assertTrue(browser["managed_defaults"])
        self.assertTrue(browser["launchers_secure"])
        self.assertEqual(
            browser["running_processes"],
            {"total": 1, "stale": 0, "inspected": True},
        )
        self.assertEqual(
            browser["workflow_skills"],
            ["infra-tools-playwright-testing"],
        )
        self.assertTrue(browser["workflow_skill_ready"])
        self.assertEqual(browser["issues"], [])
        self.assertIsNone(browser["remediation"])

    def test_browser_remediation_is_retained_without_arbitrary_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            boot_id = _write_boot_id(temp_dir, _BOOT_ONE)
            record = build_agent_readiness_record(
                [],
                [
                    {
                        "capability": "browser",
                        "healthy": False,
                        "issues": [
                            "mcp_browser_selection_missing",
                            "workflow_skill_missing_or_stale",
                            "/secret/browser",
                        ],
                        "remediation": "rerun_saved_setup",
                    }
                ],
                trigger="manual",
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                boot_id_path=boot_id,
            )

        browser = record["capabilities"][0]
        self.assertFalse(record["healthy"])
        self.assertEqual(
            browser["issues"],
            [
                "mcp_browser_selection_missing",
                "workflow_skill_missing_or_stale",
            ],
        )
        self.assertEqual(browser["remediation"], "rerun_saved_setup")
        self.assertNotIn("/secret", json.dumps(record))

    def test_optional_inventory_tool_does_not_fail_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            boot_id = _write_boot_id(temp_dir, _BOOT_ONE)
            record = build_agent_readiness_record(
                [
                    {"tool": "codex", "installed": True, "required": True},
                    {"tool": "claude", "installed": False, "required": False},
                ],
                [_host_result()],
                trigger="manual",
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                boot_id_path=boot_id,
            )

        self.assertTrue(record["healthy"])
        self.assertEqual(
            [(tool["tool"], tool["required"]) for tool in record["tools"]],
            [("codex", True), ("claude", False)],
        )

    def test_development_record_retains_only_stable_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            boot_id = _write_boot_id(temp_dir, _BOOT_ONE)
            record = build_agent_readiness_record(
                [],
                [
                    {
                        "capability": "development",
                        "installed": True,
                        "healthy": False,
                        "issues": ["node_pnpm_missing", "/secret/node"],
                        "toolchains": {
                            "node": {
                                "installed": True,
                                "healthy": False,
                                "version": "v24.20.0",
                                "npm": "11.19.0",
                                "pnpm": None,
                                "path": "/secret/node",
                            }
                        },
                    }
                ],
                trigger="manual",
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                boot_id_path=boot_id,
            )

        development = record["capabilities"][0]
        self.assertFalse(record["healthy"])
        self.assertEqual(development["issues"], ["node_pnpm_missing"])
        self.assertNotIn("path", development["toolchains"]["node"])
        self.assertNotIn("/secret", json.dumps(record))

    def test_private_record_detects_current_and_previous_boots(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            boot_id = _write_boot_id(home, _BOOT_ONE)
            saved = record_agent_readiness(
                [],
                [_host_result()],
                trigger="manual",
                home=home,
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
                boot_id_path=boot_id,
            )
            state_path = os.path.join(
                home,
                ".local",
                "state",
                "infra_tools",
                "agent-readiness.json",
            )
            current = load_agent_readiness_record(
                home=home,
                boot_id_path=boot_id,
            )
            _write_boot_id(home, _BOOT_TWO)
            previous = load_agent_readiness_record(
                home=home,
                boot_id_path=boot_id,
            )
            state_mode = stat.S_IMODE(os.stat(state_path).st_mode)

        self.assertTrue(saved["healthy"])
        self.assertEqual(state_mode, 0o600)
        self.assertIsNotNone(current)
        self.assertTrue(current["current_boot"])
        self.assertIsNotNone(previous)
        self.assertFalse(previous["current_boot"])
        self.assertIn("boot: previous", format_agent_readiness_record(previous))

    def test_symlinked_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            target = os.path.join(home, "target")
            with open(target, "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            state_dir = os.path.join(home, ".local", "state", "infra_tools")
            os.makedirs(state_dir)
            os.symlink(target, os.path.join(state_dir, "agent-readiness.json"))

            with self.assertRaisesRegex(RuntimeError, "safely read"):
                load_agent_readiness_record(home=home)


class TestAgentReadinessCLI(unittest.TestCase):
    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)
        return parser

    def test_remote_doctor_forwards_record_selection(self) -> None:
        args = self._parser().parse_args(
            [
                "agent",
                "doctor",
                "vm.example",
                "agent",
                "--capability",
                "host",
                "--record",
                "--json",
            ]
        )
        with patch(
            "lib.agent_cli._run_remote_agent_lifecycle",
            return_value=0,
        ) as remote:
            self.assertEqual(run_agent_command(args), 0)
        remote.assert_called_once_with(
            ("vm.example", "agent", None),
            "doctor",
            ["--capability", "host", "--record", "--json"],
            timeout=300,
        )

    def test_manual_record_uses_existing_doctor_results(self) -> None:
        args = self._parser().parse_args(
            ["agent", "doctor", "--capability", "host", "--record", "--json"]
        )
        host = _host_result()
        with (
            patch("lib.agent_cli.inspect_host_readiness", return_value=host),
            patch(
                "lib.agent_readiness.record_agent_readiness",
                return_value={"healthy": True},
            ) as recorder,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_agent_command(args), 0)
        recorder.assert_called_once_with([], [host], trigger="manual")

    def test_bare_record_adds_host_and_managed_t3_to_default_tools(self) -> None:
        args = self._parser().parse_args(["agent", "doctor", "--record"])
        host = _host_result()
        t3 = {"capability": "t3code", "healthy": True}
        with (
            patch("lib.agent_cli.inspect_agent_tools", return_value=[]) as tools,
            patch("lib.agent_cli.inspect_host_readiness", return_value=host),
            patch("lib.agent_cli._t3_readiness_expected", return_value=True),
            patch("lib.agent_cli.inspect_t3code", return_value=t3),
            patch(
                "lib.agent_readiness.record_agent_readiness",
                return_value={"healthy": True},
            ) as recorder,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(run_agent_command(args), 0)
        tools.assert_called_once_with(list(DEFAULT_DOCTOR_TOOLS))
        recorder.assert_called_once_with([], [host, t3], trigger="manual")

    def test_last_record_is_read_only_and_requires_current_boot(self) -> None:
        args = self._parser().parse_args(
            ["agent", "doctor", "--last-record", "--json"]
        )
        record = {
            "healthy": True,
            "current_boot": False,
            "tools": [],
            "capabilities": [],
        }
        output = io.StringIO()
        with (
            patch(
                "lib.agent_readiness.load_agent_readiness_record",
                return_value=record,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(run_agent_command(args), 1)
        self.assertFalse(json.loads(output.getvalue())["current_boot"])

    def test_post_update_record_includes_t3_when_present(self) -> None:
        tools = [{"tool": "codex", "installed": True}]
        host = _host_result()
        t3 = {"capability": "t3code", "healthy": True}
        with (
            patch("lib.agent_cli.inspect_agent_tools", return_value=tools),
            patch("lib.agent_cli.inspect_host_readiness", return_value=host),
            patch("lib.agent_cli._t3_readiness_expected", return_value=True),
            patch("lib.agent_cli.inspect_t3code", return_value=t3),
            patch(
                "lib.agent_readiness.record_agent_readiness",
                return_value={"healthy": True},
            ) as recorder,
        ):
            result = _record_post_update_readiness(["codex"])

        self.assertTrue(result["healthy"])
        recorder.assert_called_once()
        self.assertEqual(recorder.call_args.args, (tools, [host, t3]))
        self.assertEqual(recorder.call_args.kwargs["trigger"], "agent_update")

    def test_update_requires_healthy_post_update_record(self) -> None:
        args = self._parser().parse_args(
            ["agent", "update", "--tool", "codex", "--json"]
        )
        updates = [
            {
                "tool": "codex",
                "status": "current",
                "before_version": "codex-cli 0.154.0",
                "after_version": "codex-cli 0.154.0",
                "method": "codex installer",
                "path": "/home/agent/.local/bin/codex",
            }
        ]
        errors = io.StringIO()
        with (
            patch("lib.agent_cli.update_agent_tools", return_value=updates),
            patch(
                "lib.agent_cli._record_post_update_readiness",
                return_value={"healthy": False},
            ) as recorder,
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(errors),
        ):
            self.assertEqual(run_agent_command(args), 1)
        recorder.assert_called_once_with(["codex"])
        self.assertEqual(json.loads(output.getvalue()), updates)
        self.assertIn("post-update readiness is unhealthy", errors.getvalue())

    def test_update_explains_unhealthy_post_update_checks(self) -> None:
        args = self._parser().parse_args(["agent", "update", "--tool", "codex"])
        updates = [
            {
                "tool": "codex",
                "status": "current",
                "before_version": "codex-cli 0.154.0",
                "after_version": "codex-cli 0.154.0",
                "method": "codex installer",
                "path": "/home/agent/.local/bin/codex",
            }
        ]
        readiness = {
            "healthy": False,
            "tools": [
                {
                    "tool": "codex",
                    "installed": True,
                    "credential_healthy": False,
                    "credential_status": {"status": "refresh_required"},
                }
            ],
            "capabilities": [
                {
                    "capability": "t3code",
                    "healthy": False,
                    "checks": {"service_active": False, "endpoint": True},
                }
            ],
        }
        output = io.StringIO()
        with (
            patch("lib.agent_cli.update_agent_tools", return_value=updates),
            patch("lib.agent_cli._record_post_update_readiness", return_value=readiness),
            redirect_stdout(output),
        ):
            self.assertEqual(run_agent_command(args), 1)

        rendered = output.getvalue()
        self.assertIn("codex: credentials need attention (refresh_required)", rendered)
        self.assertIn("t3code: failed checks: service_active", rendered)


if __name__ == "__main__":
    unittest.main()
