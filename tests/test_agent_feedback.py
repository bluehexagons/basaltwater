"""Regression tests for agent-session workflow feedback."""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from lib import agent_cli


class AgentCapabilityFeedbackTests(unittest.TestCase):
    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        agent_cli.add_agent_subparser(subparsers)
        return parser

    def test_all_capabilities_includes_default_tools_and_each_capability(self) -> None:
        args = self._parser().parse_args(
            ["agent", "doctor", "--all-capabilities", "--json"]
        )
        output = io.StringIO()
        capability_results = {
            "browser": {"capability": "browser", "healthy": True},
            "development": {"capability": "development", "healthy": True},
            "host": {"capability": "host", "healthy": True},
            "t3code": {"capability": "t3code", "healthy": True},
        }

        with (
            patch.object(
                agent_cli,
                "inspect_agent_tools",
                return_value=[
                    {"tool": "codex", "installed": True},
                    {"tool": "claude", "installed": False},
                ],
            ) as inspect_tools,
            patch.object(
                agent_cli,
                "inspect_browser_automation",
                return_value=capability_results["browser"],
            ),
            patch.object(
                agent_cli,
                "inspect_development_readiness",
                return_value=capability_results["development"],
            ),
            patch.object(
                agent_cli,
                "inspect_host_readiness",
                return_value=capability_results["host"],
            ),
            patch.object(
                agent_cli,
                "inspect_t3code",
                return_value=capability_results["t3code"],
            ),
            redirect_stdout(output),
        ):
            result = agent_cli.run_agent_command(args)

        self.assertEqual(result, 0)
        inspect_tools.assert_called_once_with(list(agent_cli.DEFAULT_DOCTOR_TOOLS))
        payload = json.loads(output.getvalue())
        tools = payload["tools"]
        self.assertEqual(
            [(entry["tool"], entry["required"]) for entry in tools],
            [("codex", True), ("claude", False)],
        )
        self.assertEqual(
            [entry["capability"] for entry in payload["capabilities"]],
            list(agent_cli.AGENT_DOCTOR_CAPABILITIES),
        )

    def test_all_capabilities_keeps_explicit_missing_tools_required(self) -> None:
        args = self._parser().parse_args(
            [
                "agent",
                "doctor",
                "--tool",
                "claude",
                "--all-capabilities",
                "--json",
            ]
        )
        output = io.StringIO()
        capability_results = {
            "browser": {"capability": "browser", "healthy": True},
            "development": {"capability": "development", "healthy": True},
            "host": {"capability": "host", "healthy": True},
            "t3code": {"capability": "t3code", "healthy": True},
        }

        with (
            patch.object(
                agent_cli,
                "inspect_agent_tools",
                return_value=[{"tool": "claude", "installed": False}],
            ),
            patch.object(
                agent_cli,
                "inspect_browser_automation",
                return_value=capability_results["browser"],
            ),
            patch.object(
                agent_cli,
                "inspect_development_readiness",
                return_value=capability_results["development"],
            ),
            patch.object(
                agent_cli,
                "inspect_host_readiness",
                return_value=capability_results["host"],
            ),
            patch.object(
                agent_cli,
                "inspect_t3code",
                return_value=capability_results["t3code"],
            ),
            redirect_stdout(output),
        ):
            result = agent_cli.run_agent_command(args)

        self.assertEqual(result, 1)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["tools"][0]["required"])
        self.assertEqual(payload["issue"], "readiness_failed")

    def test_all_capabilities_forwards_one_remote_flag(self) -> None:
        args = self._parser().parse_args(
            [
                "agent",
                "doctor",
                "example.test",
                "agent",
                "--all-capabilities",
                "--json",
            ]
        )

        with patch.object(
            agent_cli,
            "_run_remote_agent_lifecycle",
            return_value=0,
        ) as run_remote:
            result = agent_cli.run_agent_command(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            run_remote.call_args.args[2],
            ["--all-capabilities", "--json"],
        )


if __name__ == "__main__":
    unittest.main()
