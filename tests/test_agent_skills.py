"""Tests for base workflow skills installed on agent VMs."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.agent_steps import (
    AGENT_SKILLS_ROOT,
    BASE_AGENT_SKILL_NAMES,
    BROWSER_AGENT_SKILL_NAMES,
    agent_workflow_skill_names,
    browser_agent_skill_name,
    install_managed_agent_skills,
)
from common.godot_web_steps import GODOT_AGENT_SKILLS
from common.cachyos_steps import CACHYOS_SKILLS, CACHYOS_T3_SKILL
from common.t3code_steps import T3_AGENT_SKILL_NAMES
from lib.config import SetupConfig
from lib.types import StepFunc
from plugins.common import extend_agent_steps


class ManagedAgentSkillTests(unittest.TestCase):
    def _account(self, home: str) -> SimpleNamespace:
        return SimpleNamespace(
            pw_dir=home,
            pw_uid=os.getuid(),
            pw_gid=os.getgid(),
        )

    def test_installs_and_refreshes_the_base_managed_skill_set(self) -> None:
        expected = {
            "infra-tools-agent-operations",
            "infra-tools-agent-workspace",
            "infra-tools-deploy-smoke",
            "infra-tools-shared-assets",
            "infra-tools-vm-triage",
        }
        self.assertEqual(set(BASE_AGENT_SKILL_NAMES), expected)

        with tempfile.TemporaryDirectory() as home:
            with (
                patch(
                    "common.agent_steps.pwd.getpwnam",
                    return_value=self._account(home),
                ),
                patch("common.agent_steps.os.chown"),
            ):
                self.assertTrue(
                    install_managed_agent_skills(
                        "agent",
                        ["codex"],
                        source_root=AGENT_SKILLS_ROOT,
                    )
                )
                self.assertFalse(
                    install_managed_agent_skills(
                        "agent",
                        ["codex"],
                        source_root=AGENT_SKILLS_ROOT,
                    )
                )

            for skill_name in BASE_AGENT_SKILL_NAMES:
                path = os.path.join(
                    home,
                    ".agents",
                    "skills",
                    skill_name,
                    "SKILL.md",
                )
                with open(path, encoding="utf-8") as file_obj:
                    content = file_obj.read()
                self.assertIn(f"name: {skill_name}", content)
                self.assertIn("managed-by: infra_tools", content)

    def test_every_managed_skill_belongs_to_an_installer_catalog(self) -> None:
        source_names = {
            entry.name
            for entry in os.scandir(AGENT_SKILLS_ROOT)
            if entry.is_dir(follow_symlinks=False)
        }
        installed_names = {
            *CACHYOS_SKILLS,
            CACHYOS_T3_SKILL,
            *BASE_AGENT_SKILL_NAMES,
            *BROWSER_AGENT_SKILL_NAMES,
            *T3_AGENT_SKILL_NAMES,
            *GODOT_AGENT_SKILLS,
            "infra-tools-desktop",
        }

        self.assertEqual(source_names, installed_names)

    def test_selects_browser_skill_for_the_provisioned_capabilities(self) -> None:
        cases = (
            (False, None, None),
            (False, "playwright", "infra-tools-playwright-testing"),
            (True, None, "infra-tools-t3-preview-testing"),
            (True, "playwright", "infra-tools-browser-testing"),
        )
        for t3_preview, browser_automation, expected in cases:
            with self.subTest(
                t3_preview=t3_preview,
                browser_automation=browser_automation,
            ):
                config = SetupConfig(
                    host="host",
                    username="agent",
                    system_type="agent_vm",
                    agent_tools=["codex"],
                    web_interfaces=["t3code"] if t3_preview else None,
                    browser_automation=browser_automation,
                )

                self.assertEqual(browser_agent_skill_name(config), expected)
                expected_names = set(BASE_AGENT_SKILL_NAMES)
                if expected is not None:
                    expected_names.add(expected)
                self.assertEqual(
                    set(agent_workflow_skill_names(config)),
                    expected_names,
                )

    def test_desktop_skill_is_conditional_and_keeps_browser_selection(self) -> None:
        config = SetupConfig(host="vm", username="agent", system_type="agent_vm",
                             include_desktop=True, agent_tools=["codex"],
                             browser_automation="playwright")
        self.assertIn("infra-tools-desktop", agent_workflow_skill_names(config))
        self.assertIn("infra-tools-playwright-testing", agent_workflow_skill_names(config))
        config.include_desktop = False
        self.assertNotIn("infra-tools-desktop", agent_workflow_skill_names(config))

    def test_reconciles_obsolete_managed_browser_skill_variants(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            account = self._account(home)
            combined = "infra-tools-browser-testing"
            playwright = "infra-tools-playwright-testing"
            with (
                patch("common.agent_steps.pwd.getpwnam", return_value=account),
                patch("common.agent_steps.os.chown"),
            ):
                install_managed_agent_skills(
                    "agent",
                    ["codex"],
                    (*BASE_AGENT_SKILL_NAMES, combined),
                    source_root=AGENT_SKILLS_ROOT,
                    reconcile_skill_names=BROWSER_AGENT_SKILL_NAMES,
                )
                self.assertTrue(
                    install_managed_agent_skills(
                        "agent",
                        ["codex"],
                        (*BASE_AGENT_SKILL_NAMES, playwright),
                        source_root=AGENT_SKILLS_ROOT,
                        reconcile_skill_names=BROWSER_AGENT_SKILL_NAMES,
                    )
                )

            skill_root = os.path.join(home, ".agents", "skills")
            self.assertFalse(
                os.path.exists(os.path.join(skill_root, combined, "SKILL.md"))
            )
            self.assertTrue(
                os.path.isfile(os.path.join(skill_root, playwright, "SKILL.md"))
            )

    def test_reconciliation_preserves_an_unmanaged_browser_skill(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            combined = "infra-tools-browser-testing"
            skill_dir = os.path.join(home, ".agents", "skills", combined)
            os.makedirs(skill_dir)
            destination = os.path.join(skill_dir, "SKILL.md")
            with open(destination, "w", encoding="utf-8") as file_obj:
                file_obj.write("name: user-owned\n")

            with (
                patch(
                    "common.agent_steps.pwd.getpwnam",
                    return_value=self._account(home),
                ),
                patch("common.agent_steps.os.chown"),
            ):
                install_managed_agent_skills(
                    "agent",
                    ["codex"],
                    (*BASE_AGENT_SKILL_NAMES, "infra-tools-playwright-testing"),
                    source_root=AGENT_SKILLS_ROOT,
                    reconcile_skill_names=BROWSER_AGENT_SKILL_NAMES,
                )

            with open(destination, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "name: user-owned\n")

    def test_skips_shared_skills_for_an_unsupported_agent(self) -> None:
        with patch("common.agent_steps.pwd.getpwnam") as getpwnam:
            self.assertFalse(install_managed_agent_skills("agent", ["claude"]))

        getpwnam.assert_not_called()

    def test_refuses_to_replace_an_unmanaged_skill(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            skill_name = BASE_AGENT_SKILL_NAMES[0]
            skill_dir = os.path.join(home, ".agents", "skills", skill_name)
            os.makedirs(skill_dir)
            with open(
                os.path.join(skill_dir, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write("name: user-owned\n")

            with (
                patch(
                    "common.agent_steps.pwd.getpwnam",
                    return_value=self._account(home),
                ),
                patch("common.agent_steps.os.chown"),
            ):
                with self.assertRaisesRegex(RuntimeError, "unmanaged agent skill"):
                    install_managed_agent_skills(
                        "agent",
                        ["opencode"],
                        (skill_name,),
                        source_root=AGENT_SKILLS_ROOT,
                    )

    def test_standard_agent_steps_install_base_skills_for_compatible_tools(
        self,
    ) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="agent_vm",
            agent_tools=["codex"],
            install_codex=True,
        )
        steps: list[tuple[str, StepFunc]] = []

        extend_agent_steps(config, steps)

        names = [name for name, _step in steps]
        self.assertLess(
            names.index("Installing agent VM management command"),
            names.index("Installing managed agent workflow skills"),
        )

    def test_standard_agent_steps_skip_shared_skills_for_claude_only(self) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="agent_vm",
            agent_tools=["claude"],
            install_claude=True,
        )
        steps: list[tuple[str, StepFunc]] = []

        extend_agent_steps(config, steps)

        names = [name for name, _step in steps]
        self.assertIn("Installing agent VM management command", names)
        self.assertNotIn("Installing managed agent workflow skills", names)


if __name__ == "__main__":
    unittest.main()
