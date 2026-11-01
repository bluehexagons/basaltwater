"""Tests for the managed workflow skills installed with T3 Code."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.t3code_steps import (
    T3_AGENT_SKILL_NAMES,
    _ensure_t3_agent_skill,
)
from lib.agent_cli import _t3_agent_skills_ready
from lib.config import SetupConfig
from lib.agent_skill_bundles import install_skill_bundle


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
            source = Path(__file__).resolve().parents[1] / "common/agent_skills"
            for skill_name in T3_AGENT_SKILL_NAMES:
                directory = os.path.join(home, ".agents", "skills", skill_name)
                os.makedirs(os.path.dirname(directory), exist_ok=True)
                install_skill_bundle(source / skill_name, Path(directory), os.getuid(), os.getgid())

            self.assertFalse(_t3_agent_skills_ready(home))
            browser_directory = os.path.join(
                home,
                ".agents",
                "skills",
                "basaltwater-t3-preview-testing",
            )
            install_skill_bundle(source / "basaltwater-t3-preview-testing", Path(browser_directory),
                                 os.getuid(), os.getgid())
            self.assertTrue(_t3_agent_skills_ready(home))
            reference = Path(browser_directory, "references/collaborative-preview.md")
            content = reference.read_bytes()
            inventory = Path(browser_directory, ".basaltwater-files.json")
            inventory.unlink()  # Legacy installs must still include known references.
            reference.unlink()
            self.assertFalse(_t3_agent_skills_ready(home))
            reference.write_bytes(content)
            reference.chmod(0o644)
            self.assertTrue(_t3_agent_skills_ready(home))
            stale_directory = os.path.join(
                home,
                ".agents",
                "skills",
                "basaltwater-playwright-testing",
            )
            install_skill_bundle(source / "basaltwater-playwright-testing", Path(stale_directory),
                                 os.getuid(), os.getgid())
            stale_skill = os.path.join(stale_directory, "SKILL.md")
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
