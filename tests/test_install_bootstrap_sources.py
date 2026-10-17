"""Exercise the pre-Python installer source repair inside a temporary APT tree."""

from __future__ import annotations

import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib.apt_sources import inspect_apt_sources


class InstallerBootstrapSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.apt = self.root / "apt"
        self.sources = self.apt / "sources.list.d"
        self.sources.mkdir(parents=True)
        self.keyring = self.root / "archive-keyring.pgp"
        self.keyring.touch()
        installer = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        marker = 'run_privileged sh -s -- "$distro_codename" <<\'EOF\'\n'
        body = installer.split(marker, 1)[1].split("\nEOF\n", 1)[0]
        self.assertEqual(body.count("apt_dir=/etc/apt\n"), 1)
        self.assertEqual(
            body.count("keyring=/usr/share/keyrings/debian-archive-keyring.pgp\n"), 1
        )
        self.body = body.replace("apt_dir=/etc/apt\n", f"apt_dir={shlex.quote(str(self.apt))}\n")
        self.body = self.body.replace(
            "keyring=/usr/share/keyrings/debian-archive-keyring.pgp\n",
            f"keyring={shlex.quote(str(self.keyring))}\n",
        )
        self.managed = self.sources / "basaltwater-debian.sources"

    def repair(self, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", "-s", "--", "trixie"],
            input=self.body,
            text=True,
            capture_output=True,
            check=check,
            timeout=10,
        )

    def assert_covered(self) -> None:
        status = inspect_apt_sources("trixie", str(self.apt))
        self.assertTrue(status.has_official_base)
        self.assertTrue(status.has_official_security)

    def test_inactive_or_incomplete_deb822_entries_do_not_cover_suites(self) -> None:
        for extra, source_type, components in (
            ("Enabled: no\n", "deb", "main"),
            ("", "deb-src", "main"),
            ("", "deb", "contrib"),
        ):
            with self.subTest(extra=extra, source_type=source_type, components=components):
                self.managed.unlink(missing_ok=True)
                (self.sources / "debian.sources").write_text(
                    f"Types: {source_type}\nURIs: https://deb.debian.org/debian\n"
                    f"Suites: trixie\nComponents: {components}\n{extra}\n"
                    f"Types: {source_type}\nURIs: https://security.debian.org/debian-security\n"
                    f"Suites: trixie-security\nComponents: {components}\n{extra}"
                )
                self.repair()
                self.assertTrue(self.managed.exists())
                self.assert_covered()

    def test_uri_and_suite_must_belong_to_same_entry(self) -> None:
        for filename, content in (
            (
                "debian.sources",
                "Types: deb\nURIs: https://deb.debian.org/debian\n"
                "Suites: bookworm\nComponents: main\n\n"
                "Types: deb\nURIs: https://security.debian.org/debian-security\n"
                "Suites: bookworm-security\nComponents: main\n\n"
                "Types: deb\nURIs: https://packages.example.test/repo\n"
                "Suites: trixie trixie-security\nComponents: main\n",
            ),
            (
                "debian.list",
                "deb https://deb.debian.org/debian bookworm main\n"
                "deb https://security.debian.org/debian-security bookworm-security main\n"
                "deb https://packages.example.test/repo trixie main\n"
                "deb https://packages.example.test/repo trixie-security main\n",
            ),
        ):
            with self.subTest(filename=filename):
                self.managed.unlink(missing_ok=True)
                source = self.sources / filename
                source.write_text(content)
                try:
                    self.repair()
                    self.assert_covered()
                    self.assertTrue(self.managed.exists())
                finally:
                    source.unlink()

    def test_folded_multi_value_deb822_entries_are_reused(self) -> None:
        (self.sources / "debian.sources").write_text(
            "Types: deb-src deb\nURIs: https://packages.example.test/repo\n"
            " https://deb.debian.org/debian/\nSuites: trixie-updates\n trixie\n"
            "Components: contrib\n main\nEnabled: YES\n\n"
            "Types: deb\nURIs: https://deb.debian.org/debian-security\n"
            "Suites: trixie-security\nComponents: main\n"
        )
        self.repair()
        self.assert_covered()
        self.assertFalse(self.managed.exists())

    def test_uppercase_uri_path_does_not_remove_needed_managed_coverage(self) -> None:
        for filename, content in (
            (
                "debian.list",
                "deb https://deb.debian.org/DEBIAN trixie main\n"
                "deb https://security.debian.org/debian-security trixie-security main\n",
            ),
            (
                "debian.sources",
                "Types: deb\nURIs: https://deb.debian.org/DEBIAN\n"
                "Suites: trixie\nComponents: main\n\n"
                "Types: deb\nURIs: https://security.debian.org/debian-security\n"
                "Suites: trixie-security\nComponents: main\n",
            ),
        ):
            with self.subTest(filename=filename):
                source = self.sources / filename
                source.write_text(content)
                self.managed.write_text(
                    "# Managed by basaltwater\nTypes: deb\nURIs: https://deb.debian.org/debian\n"
                    "Suites: trixie\nComponents: main\n"
                )
                try:
                    self.repair()
                    self.assertTrue(self.managed.exists())
                    self.assert_covered()
                    self.assertNotIn("trixie-security", self.managed.read_text())
                finally:
                    source.unlink()

    def test_classic_entries_with_options_are_reused(self) -> None:
        (self.apt / "sources.list").write_text(
            "deb [arch=amd64 signed-by=/archive.gpg] https://deb.debian.org/debian trixie main\n"
            "deb https://security.debian.org/ trixie-security main # security\n"
        )
        self.repair()
        self.assert_covered()
        self.assertFalse(self.managed.exists())

    def test_uppercase_components_do_not_cover_binary_main(self) -> None:
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
            with self.subTest(filename=filename):
                source = self.sources / filename
                source.write_text(content)
                self.managed.unlink(missing_ok=True)
                try:
                    self.repair()
                    self.assertTrue(self.managed.exists())
                    self.assertIn("Components: main non-free-firmware", self.managed.read_text())
                    self.assert_covered()
                finally:
                    source.unlink()

    def test_cdrom_cleanup_preserves_unrelated_stanzas_and_first_backup(self) -> None:
        source = self.sources / "mixed.sources"
        retained = (
            "# Third-party repository\nTypes: deb\nURIs: https://packages.example.test/repo\n"
            "Suites: stable\nComponents: main\n"
        )
        original = (
            "Types: deb\nURIs: cdrom:[Disc]/\nSuites: trixie\nComponents: main\n\n"
            + retained
        )
        source.write_text(original)
        self.repair()
        repaired = source.read_text()
        self.assertIn(retained, repaired)
        status = inspect_apt_sources("trixie", str(self.apt))
        self.assertFalse(status.cdrom_sources)
        self.assertTrue(any(entry.uri == "https://packages.example.test/repo" for entry in status.entries))
        self.assertEqual(Path(str(source) + ".basaltwater.bak").read_text(), original)
        self.assert_covered()
        self.repair()
        self.assertEqual(source.read_text(), repaired)
        self.assertEqual(Path(str(source) + ".basaltwater.bak").read_text(), original)

    def test_disabled_cdrom_stanza_is_untouched(self) -> None:
        source = self.sources / "offline.sources"
        original = (
            "Types: deb\nURIs: cdrom:[Disc]/\nSuites: trixie\nComponents: main\nEnabled: no\n"
        )
        source.write_text(original)
        self.repair()
        self.assertEqual(source.read_text(), original)
        self.assertFalse(Path(str(source) + ".basaltwater.bak").exists())
        self.assert_covered()

    def test_source_only_cdrom_entries_are_disabled(self) -> None:
        for filename, original in (
            (
                "offline.sources",
                "Types: deb-src\nURIs: cdrom:[Disc]/\nSuites: trixie\nComponents: main\n",
            ),
            ("offline.list", "deb-src [trusted=yes] cdrom:[Disc]/ trixie main\n"),
        ):
            with self.subTest(filename=filename):
                source = self.sources / filename
                source.write_text(original)
                self.repair()
                self.assertTrue(all(line.startswith("#") for line in source.read_text().splitlines()))
                self.assertEqual(Path(str(source) + ".basaltwater.bak").read_text(), original)
                self.assert_covered()

    def test_unrelated_file_without_final_newline_is_untouched(self) -> None:
        source = self.sources / "third-party.sources"
        original = (
            "Types: deb\nURIs: https://packages.example.test/repo\n"
            "Suites: stable\nComponents: main"
        )
        source.write_text(original)
        self.repair()
        self.assertEqual(source.read_text(), original)
        self.assertFalse(Path(str(source) + ".basaltwater.bak").exists())

    def test_unmanaged_destination_is_preserved(self) -> None:
        original = "# Configured by the operator\n"
        self.managed.write_text(original)
        result = self.repair(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to overwrite unmanaged", result.stderr)
        self.assertEqual(self.managed.read_text(), original)


if __name__ == "__main__":
    unittest.main()
