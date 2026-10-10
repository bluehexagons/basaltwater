"""Read-only catalog status without provider launch, config reads, or unsafe traversal."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_skill_catalog as catalog
from lib.agent_skill_bundles import install_skill_bundle


class SkillCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = root / "home"
        self.home.mkdir()
        self.sources = root / "sources"
        self.source = self.sources / "basaltwater-example"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("name: basaltwater-example\nmanaged-by: basaltwater\n")
        (self.source / "references").mkdir()
        (self.source / "references/context.md").write_text("Local context\n")
        self.enterContext(patch.object(catalog, "SOURCE_ROOT", self.sources))
        self.execute = self.enterContext(patch("subprocess.run", side_effect=AssertionError("Provider launch")))

    def install(self, directory: str = ".agents") -> Path:
        root = self.home / directory / "skills"
        root.mkdir(parents=True, exist_ok=True)
        destination = root / self.source.name
        with patch("lib.agent_skill_bundles.os.chown"):
            install_skill_bundle(self.source, destination, os.getuid(), os.getgid())
        return destination

    def status(self, index: int = 0) -> str:
        return catalog.inspect_skill_catalogs(str(self.home))["catalogs"][index]["skills"][0]["status"]

    def test_catalogs_share_provider_contract_and_do_not_claim_runtime_discovery(self) -> None:
        self.install()
        self.install(".claude")
        result = catalog.inspect_skill_catalogs(str(self.home))
        self.assertEqual([entry["providers"] for entry in result["catalogs"]], [["codex", "opencode"], ["claude"]])
        self.assertEqual(result["session_access"], "unverified")
        self.assertEqual(self.status(), "current")
        self.assertEqual(self.status(1), "current")
        self.execute.assert_not_called()

    def test_missing_catalogs_are_informational_and_personal_names_are_not_scanned(self) -> None:
        private = self.home / ".agents/skills/personal"
        private.mkdir(parents=True)
        (private / "SKILL.md").symlink_to("/unreadable")
        self.assertEqual([entry["skills"] for entry in catalog.inspect_skill_catalogs(str(self.home))["catalogs"]], [[], []])

    def test_missing_resources_are_detected_even_without_a_legacy_inventory(self) -> None:
        destination = self.install()
        (destination / ".basaltwater-files.json").unlink()
        (destination / "references/context.md").unlink()
        self.assertEqual(self.status(), "incomplete")

    def test_source_or_local_resource_changes_are_reported_without_refresh(self) -> None:
        destination = self.install()
        resource = destination / "references/context.md"
        resource.write_text("Personal edit\n")
        self.assertEqual(self.status(), "different")
        self.assertEqual(resource.read_text(), "Personal edit\n")
        resource.write_text((self.source / "references/context.md").read_text())
        (self.source / "SKILL.md").write_text("name: basaltwater-example\nmanaged-by: basaltwater\nNew version\n")
        self.assertEqual(self.status(), "different")

    def test_same_name_personal_override_is_preserved_and_reported(self) -> None:
        destination = self.install()
        (destination / "SKILL.md").write_text("Personal override\n")
        self.assertEqual(self.status(), "personal")
        self.assertEqual((destination / "SKILL.md").read_text(), "Personal override\n")

    def test_unsafe_files_and_symlink_ancestors_are_reported_without_following(self) -> None:
        destination = self.install()
        resource = destination / "references/context.md"
        resource.chmod(0o666)
        self.assertEqual(self.status(), "unsafe")
        resource.unlink()
        resource.symlink_to(self.source / "references/context.md")
        self.assertEqual(self.status(), "unsafe")
        resource.unlink()
        os.mkfifo(resource)
        self.assertEqual(self.status(), "unsafe")
        resource.unlink()
        (destination / "SKILL.md").unlink()
        (destination / ".basaltwater-files.json").unlink()
        (destination / "references").rmdir()
        destination.rmdir()
        destination.symlink_to(self.source, target_is_directory=True)
        self.assertEqual(self.status(), "unsafe")

    def test_missing_entrypoint_and_unreadable_entrypoint_are_reported(self) -> None:
        destination = self.install()
        (destination / "SKILL.md").unlink()
        self.assertEqual(self.status(), "incomplete")
        with patch.object(catalog, "_read", side_effect=PermissionError("Denied")):
            self.assertEqual(self.status(), "unsafe")
