"""Read-only catalog status without provider launch, config reads, or unsafe traversal."""

from __future__ import annotations

import json
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

    def footprint(self) -> dict[str, object]:
        return catalog.inspect_skill_catalogs(str(self.home))["catalogs"][0]["skills"][0]["instruction_footprint"]

    def assert_measurement(self, value: dict[str, object], status: str, files: int,
                           size: int | None, points: int | None) -> None:
        self.assertEqual(value, {"status": status, "measured_files": files,
                                 "utf8_bytes": size, "code_points": points})

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

    def test_obsolete_managed_resources_are_reported_until_reconciled(self) -> None:
        destination = self.install()
        (self.source / "references/context.md").unlink()
        self.assertEqual(self.status(), "different")
        self.assertTrue((destination / "references/context.md").exists())
        with patch("lib.agent_skill_bundles.os.chown"):
            install_skill_bundle(self.source, destination, os.getuid(), os.getgid())
        self.assertEqual(self.status(), "current")
        self.assertFalse((destination / "references/context.md").exists())

    def test_existing_inventory_is_validated_without_rewriting_it(self) -> None:
        destination = self.install()
        inventory = destination / ".basaltwater-files.json"
        for body in ("{invalid", json.dumps({"version": 1, "files": {"../outside": "0" * 64}})):
            with self.subTest(body=body):
                inventory.write_text(body)
                self.assertEqual(self.status(), "unsafe")
                self.assertEqual(inventory.read_text(), body)

    def test_inventory_digests_and_permissions_are_checked(self) -> None:
        destination = self.install()
        inventory = destination / ".basaltwater-files.json"
        value = json.loads(inventory.read_text())
        value["files"]["references/context.md"] = "0" * 64
        inventory.write_text(json.dumps(value))
        self.assertEqual(self.status(), "different")
        inventory.chmod(0o666)
        self.assertEqual(self.status(), "unsafe")
        inventory.unlink()
        self.assertEqual(self.status(), "current")  # Legacy bundles have no inventory.

    def test_script_execute_permissions_are_part_of_source_freshness(self) -> None:
        script = self.source / "scripts/check.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        destination = self.install()
        installed = destination / "scripts/check.sh"
        installed.chmod(0o644)
        self.assertEqual(self.status(), "different")
        self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
        with patch("lib.agent_skill_bundles.os.chown"):
            install_skill_bundle(self.source, destination, os.getuid(), os.getgid())
        self.assertEqual(self.status(), "current")

    def test_unfinished_refresh_is_incomplete_even_when_all_files_match(self) -> None:
        destination = self.install()
        inventory = destination / ".basaltwater-files.json"
        value = json.loads(inventory.read_text())
        value["complete"] = False
        inventory.write_text(json.dumps(value))
        self.assertEqual(self.status(), "incomplete")
        self.assertEqual(self.footprint()["status"], "complete")
        value["complete"] = "false"
        inventory.write_text(json.dumps(value))
        self.assertEqual(self.status(), "unsafe")
        value.pop("complete")
        inventory.write_text(json.dumps(value))
        self.assertEqual(self.status(), "current")  # Older inventories lack this field.

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

    def test_unicode_footprint_counts_installed_bytes_and_code_points_without_normalization(self) -> None:
        (self.source / "SKILL.md").write_bytes("managed-by: basaltwater\r\né😀e\u0301\n".encode("utf-8"))
        (self.source / "references/context.md").write_bytes("# Café\r\n😀 e\u0301\n".encode("utf-8"))
        nested = self.source / "references/nested"
        nested.mkdir()
        (nested / "extra.MD").write_bytes(b"abc\n")
        # These are bundle resources, but not Markdown reference instructions.
        (nested / "data.json").write_text("{}")
        for directory in ("scripts", "assets", "agents"):
            (self.source / directory).mkdir()
            (self.source / directory / "other.md").write_text("Excluded\n")
        destination = self.install()
        # Unknown personal files and links are never traversed for measurement.
        (destination / "references/private.md").symlink_to(self.home / "credentials")
        with patch.object(catalog, "_read", wraps=catalog._read) as read:
            result = catalog.inspect_skill_catalogs(str(self.home))
        footprint = result["catalogs"][0]["skills"][0]["instruction_footprint"]
        self.assertEqual(footprint["status"], "complete")
        self.assert_measurement(footprint["entrypoint"], "complete", 1, 35, 30)
        self.assert_measurement(footprint["references"], "complete", 2, 22, 17)
        self.assertEqual(result["catalogs"][0]["instruction_footprint"], footprint)
        self.assertEqual(result["instruction_footprint"], footprint)
        paths = [call.args[0] for call in read.call_args_list]
        self.assertNotIn(destination / "references/private.md", paths)
        self.assertEqual(paths.count(destination / "SKILL.md"), 1)
        payload = json.dumps(footprint, ensure_ascii=False)
        self.assertNotIn("Café", payload)
        self.assertNotIn("context.md", payload)

    def test_footprint_uses_local_contents_even_when_bundle_differs(self) -> None:
        destination = self.install()
        (destination / "references/context.md").write_bytes("é\n".encode("utf-8"))
        self.assertEqual(self.status(), "different")
        footprint = self.footprint()
        self.assertEqual(footprint["status"], "complete")
        self.assert_measurement(footprint["references"], "complete", 1, 3, 2)

    def test_missing_reference_keeps_measured_subset_and_marks_it_incomplete(self) -> None:
        (self.source / "references/second.md").write_bytes(b"abc")
        destination = self.install()
        (destination / "references/context.md").unlink()
        footprint = self.footprint()
        self.assertEqual(footprint["status"], "incomplete")
        self.assert_measurement(footprint["references"], "incomplete", 1, 3, 3)
        (destination / "references/second.md").unlink()
        self.assert_measurement(self.footprint()["references"], "incomplete", 0, None, None)

    def test_obsolete_recorded_reference_is_unmeasured_without_an_extra_read(self) -> None:
        destination = self.install()
        (self.source / "references/context.md").unlink()
        resource = destination / "references/context.md"
        resource.unlink()
        resource.symlink_to(self.home / "credentials")
        with patch.object(catalog, "_read", wraps=catalog._read) as read:
            footprint = self.footprint()
        self.assertEqual(footprint["status"], "incomplete")
        self.assert_measurement(footprint["references"], "incomplete", 0, None, None)
        self.assertNotIn(resource, [call.args[0] for call in read.call_args_list])

    def test_invalid_utf8_is_not_replaced_or_counted_as_text(self) -> None:
        destination = self.install()
        for relative, content in (("references/context.md", b"\xff"),
                                  ("SKILL.md", b"managed-by: basaltwater\n\xff")):
            with self.subTest(relative=relative):
                path = destination / relative
                previous = path.read_bytes()
                path.write_bytes(content)
                footprint = self.footprint()
                key = "entrypoint" if relative == "SKILL.md" else "references"
                self.assertEqual(self.status(), "different")
                self.assertEqual(footprint["status"], "incomplete")
                self.assert_measurement(footprint[key], "incomplete", 0, None, None)
                path.write_bytes(previous)

    def test_personal_and_unsafe_bundles_have_no_measured_footprint(self) -> None:
        destination = self.install()
        original = (destination / "SKILL.md").read_bytes()
        for condition in ("personal", "permissions", "symlink", "fifo", "inventory"):
            with self.subTest(condition=condition):
                entrypoint = destination / "SKILL.md"
                resource = destination / "references/context.md"
                inventory = destination / ".basaltwater-files.json"
                if condition == "personal":
                    entrypoint.write_bytes(b"Private instructions\n")
                elif condition == "permissions":
                    resource.chmod(0o666)
                elif condition == "inventory":
                    inventory.write_text("{invalid")
                else:
                    resource.unlink()
                    if condition == "symlink":
                        resource.symlink_to(self.home / "credentials")
                    else:
                        os.mkfifo(resource)
                with patch.object(catalog, "_read", wraps=catalog._read) as read:
                    footprint = self.footprint()
                self.assertEqual(footprint["status"], "unmeasured")
                for key in ("entrypoint", "references"):
                    self.assert_measurement(footprint[key], "unmeasured", 0, None, None)
                if condition == "personal":
                    self.assertNotIn(resource, [call.args[0] for call in read.call_args_list])
                entrypoint.write_bytes(original)
                resource.unlink()
                resource.write_bytes(b"Local context\n")

    def test_missing_entrypoint_and_empty_catalog_do_not_imply_complete_zero(self) -> None:
        empty = catalog.inspect_skill_catalogs(str(self.home))
        self.assertEqual(empty["instruction_footprint"]["status"], "unmeasured")
        for group in ("entrypoint", "references"):
            self.assert_measurement(empty["instruction_footprint"][group], "unmeasured", 0, None, None)
        destination = self.install()
        (destination / "SKILL.md").unlink()
        footprint = self.footprint()
        self.assertEqual(footprint["status"], "incomplete")
        self.assert_measurement(footprint["entrypoint"], "incomplete", 0, None, None)
        self.assert_measurement(footprint["references"], "unmeasured", 0, None, None)

    def test_entrypoint_only_legacy_bundle_has_a_complete_zero_reference_count(self) -> None:
        (self.source / "references/context.md").unlink()
        destination = self.install()
        inventory = destination / ".basaltwater-files.json"
        if inventory.exists():
            inventory.unlink()
        footprint = self.footprint()
        self.assertEqual(footprint["status"], "complete")
        self.assert_measurement(footprint["references"], "complete", 0, 0, 0)

    def test_catalog_and_overall_totals_sum_only_measured_files(self) -> None:
        (self.source / "SKILL.md").write_bytes(b"managed-by: basaltwater\n")
        destination = self.install()
        (destination / "references/context.md").unlink()
        self.install(".claude")
        second = self.sources / "basaltwater-second"
        second.mkdir()
        (second / "SKILL.md").write_bytes(b"managed-by: basaltwater\n")
        (second / "references").mkdir()
        (second / "references/extra.md").write_bytes(b"xy")
        with patch("lib.agent_skill_bundles.os.chown"):
            install_skill_bundle(second, self.home / ".agents/skills" / second.name, os.getuid(), os.getgid())
        personal = self.sources / "basaltwater-personal"
        personal.mkdir()
        (personal / "SKILL.md").write_bytes(b"managed-by: basaltwater\n")
        local = self.home / ".agents/skills" / personal.name
        local.mkdir()
        (local / "SKILL.md").write_text("Private\n")
        result = catalog.inspect_skill_catalogs(str(self.home))
        first = result["catalogs"][0]["instruction_footprint"]
        self.assertEqual(first["status"], "incomplete")
        self.assert_measurement(first["entrypoint"], "incomplete", 2, 48, 48)
        self.assert_measurement(first["references"], "incomplete", 1, 2, 2)
        total = result["instruction_footprint"]
        self.assertEqual(total["status"], "incomplete")
        self.assert_measurement(total["entrypoint"], "incomplete", 3, 72, 72)
        self.assert_measurement(total["references"], "incomplete", 2, 16, 16)
        self.execute.assert_not_called()

    def test_skills_json_capability_preserves_footprint(self) -> None:
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        from lib.agent_environment import run_manifest_command

        self.install()
        skills = catalog.inspect_skill_catalogs(str(self.home))
        output = StringIO()
        with patch("lib.agent_environment.inspect_environment", return_value={"skills": skills}), redirect_stdout(output):
            code = run_manifest_command(Namespace(repository=str(self.home), capability=["skills"], json=True))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"skills": skills})
