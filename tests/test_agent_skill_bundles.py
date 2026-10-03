"""Exercise bundle delivery and personal-file preservation across providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.agent_steps import install_managed_agent_skills
from common.t3code_steps import _ensure_t3_agent_skill
from lib.agent_cli import _t3_agent_skills_ready
from lib.agent_skill_bundles import install_skill_bundle, remove_skill_bundle
from lib.config import SetupConfig


class AgentSkillBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "destination"
        self.uid, self.gid = os.getuid(), os.getgid()
        self.addCleanup(patch.stopall)
        patch("lib.agent_skill_bundles.os.chown").start()
        patch("lib.atomic_io.os.fchown").start()
        self.entrypoint = b"---\nmetadata:\n  managed-by: basaltwater\n---\nOriginal\n"
        (self.source / "SKILL.md").write_bytes(self.entrypoint)

    def resource(self, name: str, content: bytes, mode: int = 0o644) -> Path:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        return path

    def install(self) -> bool:
        return install_skill_bundle(self.source, self.destination, self.uid, self.gid)

    def test_binary_nested_resources_executable_scripts_and_idempotence(self) -> None:
        self.resource("references/nested/update.md", b"Migration details\n")
        self.resource("scripts/check.sh", b"#!/bin/sh\nexit 0\n", 0o755)
        self.resource("assets/sample.bin", bytes(range(256)))
        self.resource("agents/openai.yaml", b"interface: {}\n")
        self.assertTrue(self.install())
        for path in self.source.rglob("*"):
            if path.is_file():
                installed = self.destination / path.relative_to(self.source)
                self.assertEqual(installed.read_bytes(), path.read_bytes())
                expected_mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                self.assertEqual(installed.stat().st_mode & 0o777, expected_mode)
        self.assertEqual((self.destination / ".basaltwater-files.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.install())

    def test_upgrade_adopts_old_managed_entrypoint_and_refreshes_resources(self) -> None:
        self.destination.mkdir()
        (self.destination / "SKILL.md").write_bytes(self.entrypoint)
        reference = self.resource("references/update.md", b"v1")
        self.assertTrue(self.install())
        reference.write_bytes(b"v2")
        self.assertTrue(self.install())
        self.assertEqual((self.destination / "references/update.md").read_bytes(), b"v2")
        self.assertFalse(self.install())

    def test_collision_preflight_preserves_old_entrypoint(self) -> None:
        self.install()
        (self.source / "SKILL.md").write_bytes(self.entrypoint + b"New guidance\n")
        self.resource("references/update.md", b"managed")
        personal = self.destination / "references/update.md"
        personal.parent.mkdir()
        personal.write_bytes(b"personal")
        with self.assertRaisesRegex(RuntimeError, "personal or modified"):
            self.install()
        self.assertEqual((self.destination / "SKILL.md").read_bytes(), self.entrypoint)
        self.assertEqual(personal.read_bytes(), b"personal")

    def test_modified_tracked_resource_is_not_overwritten(self) -> None:
        self.resource("references/update.md", b"managed")
        self.install()
        personal = self.destination / "references/update.md"
        personal.write_bytes(b"personal edits")
        with self.assertRaisesRegex(RuntimeError, "personal or modified"):
            self.install()
        self.assertEqual(personal.read_bytes(), b"personal edits")

    def test_stale_resources_removed_but_edits_and_personal_files_preserved(self) -> None:
        obsolete = self.resource("references/old/update.md", b"old")
        edited = self.resource("references/edited.md", b"original")
        self.install()
        personal = self.destination / "references/personal.md"
        personal.write_bytes(b"personal")
        retained = self.destination / "references/edited.md"
        retained.write_bytes(b"edits")
        obsolete.unlink()
        edited.unlink()
        self.assertTrue(self.install())
        self.assertFalse((self.destination / "references/old").exists())
        self.assertEqual(retained.read_bytes(), b"edits")
        self.assertEqual(personal.read_bytes(), b"personal")
        self.assertFalse(self.install())

    def test_retirement_removes_only_unchanged_managed_files(self) -> None:
        self.resource("references/update.md", b"managed")
        self.install()
        personal = self.destination / "notes.md"
        personal.write_bytes(b"personal")
        self.assertTrue(remove_skill_bundle(self.destination, self.uid))
        self.assertEqual(personal.read_bytes(), b"personal")
        self.assertFalse((self.destination / "SKILL.md").exists())
        self.assertFalse((self.destination / "references").exists())
        self.assertFalse((self.destination / ".basaltwater-files.json").exists())
        self.assertFalse(remove_skill_bundle(self.destination, self.uid))

    def test_unmanaged_entrypoint_retirement_does_not_touch_resources(self) -> None:
        self.resource("references/update.md", b"managed")
        self.install()
        (self.destination / "SKILL.md").write_bytes(b"Personal skill\n")
        self.assertFalse(remove_skill_bundle(self.destination, self.uid))
        self.assertEqual((self.destination / "references/update.md").read_bytes(), b"managed")

    def test_retirement_preserves_modified_resources(self) -> None:
        self.resource("references/update.md", b"managed")
        self.install()
        reference = self.destination / "references/update.md"
        reference.write_bytes(b"personal edits")
        self.assertTrue(remove_skill_bundle(self.destination, self.uid))
        self.assertEqual(reference.read_bytes(), b"personal edits")

    def test_source_symlink_and_special_file_are_rejected(self) -> None:
        source = self.resource("assets/link", b"original")
        source.unlink()
        source.symlink_to(self.source / "SKILL.md")
        with self.assertRaisesRegex(RuntimeError, "symlinked"):
            self.install()
        source.unlink()
        os.mkfifo(source)
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_destination_symlink_ancestor_cannot_write_outside_catalog(self) -> None:
        self.resource("references/update.md", b"managed")
        self.destination.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.destination / "references").symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.destination / "SKILL.md").exists())

    def test_invalid_inventory_cannot_delete_unrelated_files(self) -> None:
        self.install()
        outside = self.root / "outside.md"
        outside.write_bytes(b"private")
        (self.destination / ".basaltwater-files.json").write_text(json.dumps({
            "version": 1, "files": {"../outside.md": "0" * 64},
        }))
        with self.assertRaisesRegex(RuntimeError, "Invalid.*inventory"):
            remove_skill_bundle(self.destination, self.uid)
        self.assertEqual(outside.read_bytes(), b"private")
        self.assertTrue((self.destination / "SKILL.md").exists())

    def test_symlinked_stale_resource_is_rejected_before_refresh(self) -> None:
        reference = self.resource("references/update.md", b"managed")
        self.install()
        reference.unlink()
        installed = self.destination / "references/update.md"
        installed.unlink()
        outside = self.root / "outside.md"
        outside.write_bytes(b"private")
        installed.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.install()
        self.assertEqual(outside.read_bytes(), b"private")

    def test_wrong_owner_is_rejected(self) -> None:
        self.install()
        with self.assertRaisesRegex(RuntimeError, "another user"):
            install_skill_bundle(self.source, self.destination, self.uid + 1, self.gid)

    def test_selected_provider_catalogs_and_claude_t3_readiness(self) -> None:
        home = self.root / "home"
        home.mkdir()
        account = SimpleNamespace(pw_dir=str(home), pw_uid=self.uid, pw_gid=self.gid)
        with patch("common.agent_steps.pwd.getpwnam", return_value=account):
            config = SetupConfig(host="vm", username="agent", system_type="agent_vm",
                                 agent_tools=["claude"], web_interfaces=["t3code"])
            self.assertTrue(_ensure_t3_agent_skill(config))
            self.assertFalse((home / ".agents").exists())
            self.assertTrue(_t3_agent_skills_ready(str(home), (".claude",)))
            reference = home / ".claude/skills/basaltwater-t3code/references/updates.md"
            self.assertTrue(reference.is_file())
            self.assertFalse(_ensure_t3_agent_skill(config))
            reference.unlink()
            self.assertFalse(_t3_agent_skills_ready(str(home), (".claude",)))
            self.assertTrue(_ensure_t3_agent_skill(config))
            config.install_codex = True
            self.assertTrue(_ensure_t3_agent_skill(config))
            self.assertTrue(_t3_agent_skills_ready(str(home), (".agents", ".claude")))
            (home / ".claude/skills/basaltwater-t3code/SKILL.md").unlink()
            self.assertFalse(_t3_agent_skills_ready(str(home), (".agents", ".claude")))

    def test_reconciliation_runs_in_each_selected_provider_catalog(self) -> None:
        home = self.root / "home"
        home.mkdir()
        source_root = self.root / "catalog"
        source_root.mkdir()
        self.source.rename(source_root / "example")
        account = SimpleNamespace(pw_dir=str(home), pw_uid=self.uid, pw_gid=self.gid)
        with patch("common.agent_steps.pwd.getpwnam", return_value=account):
            self.assertTrue(install_managed_agent_skills(
                "agent", ["codex", "opencode", "claude"], ("example",), source_root=str(source_root),
            ))
            self.assertTrue(install_managed_agent_skills(
                "agent", ["codex", "claude"], (), source_root=str(source_root), reconcile_skill_names=("example",),
            ))
        for catalog in (".agents", ".claude"):
            self.assertFalse((home / catalog / "skills/example").exists())


if __name__ == "__main__":
    unittest.main()
