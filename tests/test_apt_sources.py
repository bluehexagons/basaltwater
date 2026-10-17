"""Tests for Debian APT source detection and offline-install repair."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.apt_sources import (
    MANAGED_SOURCE_FILENAME,
    ensure_debian_package_sources,
    inspect_apt_sources,
    parse_apt_sources,
    XRDP_SID_SOURCE,
    XRDP_SID_PREFERENCE,
    _disable_stale_official_sources,
)


class TestAptSources(unittest.TestCase):
    def test_component_case_does_not_cover_main_or_poison_managed_repair(self):
        for filename, content in (
            (
                "debian.list",
                "deb https://deb.debian.org/debian trixie MAIN\n"
                "deb https://security.debian.org/debian-security trixie-security MAIN\n",
            ),
            (
                "debian.sources",
                "Types: deb\nURIs: https://deb.debian.org/debian\n"
                "Suites: trixie\nComponents: MAIN\n\n"
                "Types: deb\nURIs: https://security.debian.org/debian-security\n"
                "Suites: trixie-security\nComponents: MAIN\n",
            ),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                apt = root / "apt"
                sources = apt / "sources.list.d"
                sources.mkdir(parents=True)
                (sources / filename).write_text(content)
                release = root / "os-release"
                release.write_text("ID=debian\nVERSION_CODENAME=trixie\n")
                before = inspect_apt_sources("trixie", str(apt))
                self.assertFalse(before.has_official_base)
                self.assertFalse(before.has_official_security)
                with patch("lib.apt_sources._find_debian_archive_keyring", return_value=str(root / "keyring")):
                    status = ensure_debian_package_sources(str(apt), str(release))
                    self.assertTrue(status.has_official_base)
                    self.assertTrue(status.has_official_security)
                    managed = sources / MANAGED_SOURCE_FILENAME
                    self.assertIn("Components: main MAIN", managed.read_text())
                    managed_before = managed.read_bytes()
                    ensure_debian_package_sources(str(apt), str(release))
                    self.assertEqual(managed.read_bytes(), managed_before)

    def test_source_only_cdrom_cleanup_preserves_other_stanzas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apt = root / "apt"
            sources = apt / "sources.list.d"
            sources.mkdir(parents=True)
            source = sources / "mixed.sources"
            retained = (
                "Types: deb-src\nURIs: cdrom:[Disabled]/\nSuites: trixie\n"
                "Components: main\nEnabled: no\n\n"
                "Types: deb\nURIs: https://packages.example.test/repo\nSuites: stable\nComponents: main\n"
            )
            original = (
                "Types: deb-src\nURIs: cdrom:[Disc]/\nSuites: trixie\nComponents: main\n\n"
                + retained
            )
            source.write_text(original)
            release = root / "os-release"
            release.write_text("ID=debian\nVERSION_CODENAME=trixie\n")
            with patch("lib.apt_sources._find_debian_archive_keyring", return_value=str(root / "keyring")):
                ensure_debian_package_sources(str(apt), str(release))
            self.assertIn("# Disabled by basaltwater: Types: deb-src", source.read_text())
            self.assertIn(retained, source.read_text())
            self.assertEqual(Path(str(source) + ".basaltwater.bak").read_text(), original)

    def test_preserves_pinned_desktop_source_and_repairs_prior_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sources.list.d/basaltwater-sid.sources"
            pin = root / "preferences.d/basaltwater-sid.pref"
            source.parent.mkdir()
            pin.parent.mkdir()
            pin.write_text(XRDP_SID_PREFERENCE)
            for content in (XRDP_SID_SOURCE, "".join(
                    "# Disabled by basaltwater: " + line
                    for line in XRDP_SID_SOURCE.splitlines(keepends=True)) + "\n"):
                source.write_text(content)
                _disable_stale_official_sources(directory, "trixie")
                self.assertEqual(source.read_text(), XRDP_SID_SOURCE)
                _disable_stale_official_sources(directory, "trixie")
                self.assertEqual(source.read_text(), XRDP_SID_SOURCE)

    def test_desktop_filename_does_not_exempt_unpinned_or_altered_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sources.list.d/basaltwater-sid.sources"
            pin = root / "preferences.d/basaltwater-sid.pref"
            source.parent.mkdir()
            pin.parent.mkdir()
            for preference, content in (
                ("", XRDP_SID_SOURCE),
                (XRDP_SID_PREFERENCE.replace("50", "990"), XRDP_SID_SOURCE),
                (XRDP_SID_PREFERENCE, XRDP_SID_SOURCE.replace("Suites: sid", "Suites: sid testing")),
            ):
                pin.write_text(preference)
                source.write_text(content)
                _disable_stale_official_sources(directory, "trixie")
                self.assertFalse(parse_apt_sources(directory))

    def _layout(self, *, os_release: str, sources_list: str = "") -> tuple[str, str]:
        root = tempfile.mkdtemp()
        apt_dir = os.path.join(root, "etc", "apt")
        os.makedirs(os.path.join(apt_dir, "sources.list.d"))
        os.makedirs(os.path.join(root, "usr", "share", "keyrings"))
        release_path = os.path.join(root, "etc", "os-release")
        os.makedirs(os.path.dirname(release_path), exist_ok=True)
        with open(release_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(os_release)
        if sources_list:
            with open(os.path.join(apt_dir, "sources.list"), "w", encoding="utf-8") as file_obj:
                file_obj.write(sources_list)
        return root, apt_dir

    def test_repairs_cdrom_only_classic_sources(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
            sources_list="deb cdrom:[Debian GNU/Linux 13.0.0 _Trixie_ - Official amd64 DVD]/ trixie main\n",
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(apt_dir, os.path.join(root, "etc", "os-release"))
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            assert status is not None
            self.assertTrue(status.has_official_base)
            self.assertTrue(status.has_official_security)
            self.assertFalse(status.cdrom_sources)
            with open(os.path.join(apt_dir, "sources.list"), encoding="utf-8") as file_obj:
                self.assertTrue(file_obj.read().startswith("# Disabled by basaltwater:"))
            managed_path = os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)
            self.assertTrue(os.path.isfile(managed_path))
            self.assertTrue(os.path.isfile(os.path.join(apt_dir, "sources.list.basaltwater.bak")))
            self.assertEqual(len(parse_apt_sources(apt_dir)), 3)
        finally:
            shutil.rmtree(root)

    def test_accepts_existing_official_deb822_sources_and_disables_cdrom_stanza(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            sources_path = os.path.join(apt_dir, "sources.list.d", "debian.sources")
            with open(sources_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "Types: deb\nURIs: cdrom:[Debian]/\nSuites: trixie\n"
                    "Components: main\n\n"
                    "Types: deb\nURIs: http://deb.debian.org/debian\n"
                    "Suites: trixie trixie-updates\nComponents: main\n\n"
                    "Types: deb\nURIs: http://security.debian.org\n"
                    "Suites: trixie-security\nComponents: main\n"
                )
            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(apt_dir, os.path.join(root, "etc", "os-release"))
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            assert status is not None
            self.assertTrue(status.has_official_base)
            self.assertTrue(status.has_official_security)
            self.assertFalse(status.cdrom_sources)
            with open(sources_path, encoding="utf-8") as file_obj:
                self.assertIn("# Disabled by basaltwater: URIs: cdrom:[Debian]/", file_obj.read())
            self.assertFalse(os.path.exists(os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)))
        finally:
            shutil.rmtree(root)

    def test_disables_stale_official_suites_before_adding_current_sources(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
            sources_list=(
                "deb https://deb.debian.org/debian bookworm main non-free-firmware\n"
                "deb https://security.debian.org/debian-security bookworm-security "
                "main non-free-firmware\n"
            ),
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(apt_dir, os.path.join(root, "etc", "os-release"))
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            assert status is not None
            self.assertTrue(status.has_official_base)
            self.assertTrue(status.has_official_security)
            self.assertEqual(
                {entry.suite for entry in status.entries},
                {"trixie", "trixie-updates", "trixie-security"},
            )
            with open(os.path.join(apt_dir, "sources.list"), encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn("# Disabled by basaltwater: deb https://deb.debian.org/debian bookworm", content)
            self.assertTrue(os.path.isfile(os.path.join(apt_dir, "sources.list.basaltwater.bak")))
            managed_path = os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)
            with open(managed_path, encoding="utf-8") as file_obj:
                managed_content = file_obj.read()
            self.assertIn("Components: main non-free-firmware", managed_content)
        finally:
            shutil.rmtree(root)

    def test_disables_cdrom_uri_on_deb822_continuation_line(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
            sources_list=(
                "deb https://deb.debian.org/debian trixie main\n"
                "deb https://security.debian.org/debian-security trixie-security main\n"
            ),
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            sources_path = os.path.join(apt_dir, "sources.list.d", "offline.sources")
            with open(sources_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "Types: deb\n"
                    "URIs: https://deb.debian.org/debian\n"
                    " cdrom:[Debian]/\n"
                    "Suites: trixie\n"
                    "Components: main\n"
                )
            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(apt_dir, os.path.join(root, "etc", "os-release"))
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            assert status is not None
            self.assertFalse(status.cdrom_sources)
            with open(sources_path, encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn("# Disabled by basaltwater:  cdrom:[Debian]/", content)
        finally:
            shutil.rmtree(root)

    def test_removes_redundant_managed_source_when_official_sources_exist(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
            sources_list=(
                "deb https://deb.debian.org/debian trixie main\n"
                "deb https://security.debian.org/debian-security trixie-security main\n"
            ),
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            managed_path = os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)
            with open(managed_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "# Managed by basaltwater. Do not edit\n"
                    "Types: deb\nURIs: https://deb.debian.org/debian\n"
                    "Suites: trixie\nComponents: main\n"
                )

            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(
                    apt_dir,
                    os.path.join(root, "etc", "os-release"),
                )
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            self.assertFalse(os.path.exists(managed_path))
            self.assertTrue(os.path.isfile(f"{managed_path}.basaltwater.bak"))
        finally:
            shutil.rmtree(root)

    def test_managed_source_only_contains_missing_official_suite(self):
        root, apt_dir = self._layout(
            os_release="ID=debian\nVERSION_CODENAME=trixie\n",
            sources_list="deb https://deb.debian.org/debian trixie main\n",
        )
        try:
            import lib.apt_sources as apt_sources

            keyring = os.path.join(root, "usr", "share", "keyrings", "debian-archive-keyring.gpg")
            with open(keyring, "wb") as file_obj:
                file_obj.write(b"test keyring")
            original_keyring = apt_sources.DEBIAN_ARCHIVE_KEYRING
            apt_sources.DEBIAN_ARCHIVE_KEYRING = keyring
            try:
                status = ensure_debian_package_sources(
                    apt_dir,
                    os.path.join(root, "etc", "os-release"),
                )
            finally:
                apt_sources.DEBIAN_ARCHIVE_KEYRING = original_keyring

            self.assertIsNotNone(status)
            managed_path = os.path.join(apt_dir, "sources.list.d", MANAGED_SOURCE_FILENAME)
            with open(managed_path, encoding="utf-8") as file_obj:
                managed_content = file_obj.read()
            self.assertNotIn("Suites: trixie trixie-updates", managed_content)
            self.assertIn("Suites: trixie-security", managed_content)
        finally:
            shutil.rmtree(root)

    def test_rejects_non_debian_source_repair(self):
        root, apt_dir = self._layout(os_release="ID=ubuntu\nVERSION_CODENAME=noble\n")
        try:
            self.assertIsNone(
                ensure_debian_package_sources(apt_dir, os.path.join(root, "etc", "os-release"))
            )
            self.assertEqual(inspect_apt_sources("noble", apt_dir).entries, ())
        finally:
            shutil.rmtree(root)

    def test_repair_retains_managed_suites_after_other_sources_are_removed(self):
        for source in (
            "deb https://deb.debian.org/debian trixie main\n",
            "deb https://security.debian.org/debian-security trixie-security main\n",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                apt_dir = root / "apt"
                (apt_dir / "sources.list.d").mkdir(parents=True)
                external = apt_dir / "sources.list"
                external.write_text(source, encoding="utf-8")
                release = root / "os-release"
                release.write_text("ID=debian\nVERSION_CODENAME=trixie\n", encoding="utf-8")
                keyring = root / "keyring.gpg"
                keyring.write_bytes(b"test keyring")

                with patch("lib.apt_sources._find_debian_archive_keyring", return_value=str(keyring)):
                    ensure_debian_package_sources(str(apt_dir), str(release))
                    external.unlink()
                    status = ensure_debian_package_sources(str(apt_dir), str(release))
                    self.assertTrue(status.has_official_base)
                    self.assertTrue(status.has_official_security)
                    managed = apt_dir / "sources.list.d" / MANAGED_SOURCE_FILENAME
                    before_rerun = managed.read_bytes()
                    ensure_debian_package_sources(str(apt_dir), str(release))
                    self.assertEqual(managed.read_bytes(), before_rerun)


if __name__ == "__main__":
    unittest.main()
