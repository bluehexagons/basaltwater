"""Tests for the managed workflow skills installed with T3 Code."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.agent_steps import BASE_AGENT_SKILL_NAMES
from common.t3code_steps import (
    T3_AGENT_SKILL_NAMES,
    _ensure_t3_agent_skill,
)
from lib.agent_cli import _t3_agent_skills_ready
from lib.config import SetupConfig


class ManagedT3AgentSkillTests(unittest.TestCase):
    def _config(
        self,
        *tools: str,
        browser_automation: str | None = None,
    ) -> SetupConfig:
        return SetupConfig(
            host="host",
            username="agent",
            system_type="agent_vm",
            agent_tools=list(tools),
            web_interfaces=["t3code"],
            browser_automation=browser_automation,
        )

    def test_installs_all_self_contained_managed_skills(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            account = SimpleNamespace(
                pw_dir=home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            with (
                patch("common.agent_steps.pwd.getpwnam", return_value=account),
                patch("common.agent_steps.os.chown"),
            ):
                self.assertTrue(_ensure_t3_agent_skill(self._config("codex")))

            expected = {
                *T3_AGENT_SKILL_NAMES,
                "basaltwater-t3-preview-testing",
            }
            self.assertEqual(
                expected,
                {
                    *BASE_AGENT_SKILL_NAMES,
                    "basaltwater-t3code",
                    "basaltwater-t3-preview-testing",
                    "basaltwater-web-gateway",
                },
            )
            for skill_name in expected:
                path = os.path.join(home, ".agents", "skills", skill_name, "SKILL.md")
                with open(path, encoding="utf-8") as file_obj:
                    content = file_obj.read()
                self.assertIn(f"name: {skill_name}", content)
                self.assertIn("managed-by: basaltwater", content)

    def test_t3_with_playwright_installs_only_the_combined_browser_skill(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            account = SimpleNamespace(
                pw_dir=home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            with (
                patch("common.agent_steps.pwd.getpwnam", return_value=account),
                patch("common.agent_steps.os.chown"),
            ):
                self.assertTrue(
                    _ensure_t3_agent_skill(
                        self._config("codex", browser_automation="playwright")
                    )
                )

            skills_root = os.path.join(home, ".agents", "skills")
            self.assertTrue(
                os.path.isfile(
                    os.path.join(
                        skills_root,
                        "basaltwater-browser-testing",
                        "SKILL.md",
                    )
                )
            )
            for absent in (
                "basaltwater-playwright-testing",
                "basaltwater-t3-preview-testing",
            ):
                self.assertFalse(
                    os.path.exists(os.path.join(skills_root, absent, "SKILL.md"))
                )

    def test_skips_skill_install_without_a_supported_agent(self) -> None:
        config = self._config("codex")
        config.install_codex = False
        config.install_gh = True
        with patch("common.agent_steps.pwd.getpwnam") as account:
            self.assertFalse(_ensure_t3_agent_skill(config))
        account.assert_not_called()

    def test_doctor_requires_the_complete_managed_skill_set(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            for skill_name in T3_AGENT_SKILL_NAMES:
                directory = os.path.join(home, ".agents", "skills", skill_name)
                os.makedirs(directory)
                with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as file_obj:
                    file_obj.write("metadata:\n  managed-by: basaltwater\n")
                os.chmod(os.path.join(directory, "SKILL.md"), 0o644)

            self.assertFalse(_t3_agent_skills_ready(home))
            browser_directory = os.path.join(
                home,
                ".agents",
                "skills",
                "basaltwater-t3-preview-testing",
            )
            os.makedirs(browser_directory)
            with open(
                os.path.join(browser_directory, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write("metadata:\n  managed-by: basaltwater\n")
            os.chmod(os.path.join(browser_directory, "SKILL.md"), 0o644)
            self.assertTrue(_t3_agent_skills_ready(home))
            stale_directory = os.path.join(
                home,
                ".agents",
                "skills",
                "basaltwater-playwright-testing",
            )
            os.makedirs(stale_directory)
            stale_skill = os.path.join(stale_directory, "SKILL.md")
            with open(stale_skill, "w", encoding="utf-8") as file_obj:
                file_obj.write("metadata:\n  managed-by: basaltwater\n")
            os.chmod(stale_skill, 0o644)
            self.assertFalse(_t3_agent_skills_ready(home))
            os.unlink(stale_skill)
            self.assertTrue(_t3_agent_skills_ready(home))
            browser_skill = os.path.join(browser_directory, "SKILL.md")
            os.chmod(browser_skill, 0o666)
            self.assertFalse(_t3_agent_skills_ready(home))
            os.chmod(browser_skill, 0o644)
            self.assertTrue(_t3_agent_skills_ready(home))
            os.unlink(
                os.path.join(
                    home,
                    ".agents",
                    "skills",
                    T3_AGENT_SKILL_NAMES[0],
                    "SKILL.md",
                )
            )
            self.assertFalse(_t3_agent_skills_ready(home))


if __name__ == "__main__":
    unittest.main()
