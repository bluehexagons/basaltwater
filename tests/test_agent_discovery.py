"""Host discovery stays read-only and separate from project/session readiness."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from lib.agent_discovery import inspect_context


class AgentDiscoveryTests(unittest.TestCase):
    def inspect(self, *, distro="cachyos", release="linux", system="Linux"):
        catalog = {"catalogs": [{"skills": [
            {"name": "basaltwater-subagents", "path": "/skills/subagents/SKILL.md", "status": "current"},
            {"name": "broken", "path": "/skills/broken/SKILL.md", "status": "unsafe"},
            {"name": "missing-reference", "path": "/skills/missing/SKILL.md", "status": "incomplete"},
        ]}]}
        with (patch("lib.agent_discovery.platform.system", return_value=system),
              patch("lib.agent_discovery.platform.release", return_value=release),
              patch("lib.agent_discovery.platform.freedesktop_os_release", return_value={"ID": distro}),
              patch("lib.agent_discovery.is_cachyos", return_value=distro == "cachyos"),
              patch("lib.agent_discovery.inspect_skill_catalogs", return_value=catalog) as inventory,
              patch("lib.agent_workspace._effective_home", return_value="/home/example"),
              patch("lib.agent_discovery.shutil.which", side_effect=lambda name: "/usr/bin/" + name if name in {"codex", "t3code", "blender", "shotcut", "lmms", "obs", "musescore4"} else None),
              patch("subprocess.Popen", side_effect=AssertionError("Discovery must not execute tools"))):
            result = inspect_context()
            inventory.assert_called_once_with("/home/example")
            return result

    def test_guidance_uses_effective_account_home_when_home_is_overridden(self):
        with patch.dict("os.environ", {"HOME": "/home/other-account"}):
            self.inspect()

    def test_presence_never_promotes_readiness_and_bad_guidance_is_omitted(self):
        result = self.inspect()
        self.assertEqual(result["readiness"], "unverified")
        self.assertEqual(result["tools_on_path"]["agents"], ["codex", "t3code"])
        self.assertEqual(result["tools_on_path"]["assets"], ["blender", "shotcut", "lmms", "obs", "musescore4"])
        self.assertEqual(result["tools_on_path"]["containers"], [])
        self.assertEqual(set(result["installed_guidance"]), {"basaltwater-subagents"})
        self.assertEqual(result["unusable_guidance_count"], 2)

    def test_service_routes_follow_platform_without_assuming_debian(self):
        for distro, release, system, host, expected, absent in (
            ("cachyos", "linux", "Linux", "cachyos-workstation", "basaltw local cachyos-doctor --help", "basaltw agent doctor --help"),
            ("debian", "linux", "Linux", "debian", "basaltw agent doctor --help", "basaltw local cachyos-doctor --help"),
            ("fedora", "linux", "Linux", "fedora", "basaltw agent memory --help", "basaltw agent doctor --help"),
            ("debian", "microsoft-wsl2", "Linux", "debian-wsl", "basaltw agent memory --help", "basaltw agent doctor --help"),
            ("unused", "windows", "Windows", "windows", "basaltw agent memory --help", "basaltw desktop --help"),
        ):
            with self.subTest(host=host):
                result = self.inspect(distro=distro, release=release, system=system)
                self.assertEqual(result["host_profile"], host)
                self.assertIn(expected, result["help_routes"].values())
                self.assertNotIn(absent, result["help_routes"].values())

    def test_cli_dispatch_works_without_git_or_project_inspection(self):
        from basaltwater import create_basaltwater_parser
        from lib.agent_cli import run_agent_command

        parser, _, _ = create_basaltwater_parser()
        context = self.inspect()
        with (patch("lib.agent_discovery.inspect_context", return_value=context),
              patch("lib.agent_workspace._repository_root", side_effect=AssertionError("No Git required")),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            self.assertEqual(run_agent_command(parser.parse_args(["agent", "context", "--json"])), 0)
            self.assertEqual(json.loads(output.getvalue()), context)
            output.seek(0)
            output.truncate()
            self.assertEqual(run_agent_command(parser.parse_args(["agent", "context"])), 0)
            self.assertLess(len(output.getvalue()), 6000)
            self.assertNotIn("/skills/", output.getvalue())

    def test_discovery_errors_remain_failures_in_json(self):
        from lib.agent_discovery import run_context_command
        from argparse import Namespace

        with (patch("lib.agent_discovery.inspect_context", side_effect=OSError("unreadable catalog")),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            self.assertEqual(run_context_command(Namespace(json=True)), 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_text_exposes_differing_and_personal_copies_without_duplicate_names(self):
        from lib.agent_discovery import run_context_command
        from argparse import Namespace

        context = self.inspect()
        context["installed_guidance"] = {
            "basaltwater-subagents": [
                {"path": "/codex/SKILL.md", "status": "current"},
                {"path": "/claude/SKILL.md", "status": "different"}],
            "basaltwater-desktop": [{"path": "/personal/SKILL.md", "status": "personal"}],
        }
        with (patch("lib.agent_discovery.inspect_context", return_value=context),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            self.assertEqual(run_context_command(Namespace(json=False)), 0)
        text = output.getvalue()
        self.assertIn("basaltwater-subagents (current/different)", text)
        self.assertEqual(text.count("basaltwater-subagents"), 1)
        self.assertIn("basaltwater-desktop (personal)", text)
        self.assertIn("basaltw agent context --json", text)
        self.assertNotIn("/personal/", text)


if __name__ == "__main__":
    unittest.main()
