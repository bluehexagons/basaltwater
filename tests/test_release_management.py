"""Security tests for shared release installation helpers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.release_management import (
    fetch_latest_verified_github_release_asset,
    install_binary_release,
    load_json_state,
    validate_release_download_url,
    validate_release_sha256_digest,
    validate_release_tag,
)
from lib.state_read import StateReadError


class TestReleaseValidation(unittest.TestCase):
    def test_missing_state_is_fresh_but_invalid_state_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'release.json')
            def load():
                return load_json_state(str(path), read_error_label='release metadata',
                                       invalid_state_message='invalid release metadata')
            self.assertEqual(load(), {})
            for content in ('{broken', '[]', '{}', '\ufffd', ' ' * (1024 * 1024 + 1)):
                with self.subTest(content=content[:30]):
                    path.write_text(content)
                    with self.assertRaisesRegex(StateReadError, 'File retained'):
                        load()
                    self.assertEqual(path.read_text(), content)
            path.write_text('{"tag_name": "v1.0.0"}')
            self.assertEqual(load(), {'tag_name': 'v1.0.0'})

    def test_release_state_refuses_symlinks_and_special_files(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root, 'target.json')
            target.write_text('{"tag_name": "v1.0.0"}')
            link, fifo = Path(root, 'link'), Path(root, 'fifo')
            link.symlink_to(target)
            os.mkfifo(fifo)
            for path in (link, fifo, Path(root)):
                with self.subTest(path=path), self.assertRaises(StateReadError):
                    load_json_state(str(path), read_error_label='metadata',
                                    invalid_state_message='invalid metadata')
            self.assertEqual(target.read_text(), '{"tag_name": "v1.0.0"}')

    def test_antistatic_invalid_metadata_does_not_replace_binary(self):
        from game import antistatic_steps

        cases = (
            ('ANTISTATIC_RELEASE_STATE_FILE', antistatic_steps._download_antistatic_binary,
             '_fetch_latest_antistatic_release'),
            ('ANTISTATIC_DB_RELEASE_STATE_FILE', antistatic_steps._download_antistatic_db_binary,
             '_fetch_latest_antistatic_db_release'),
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'release.json')
            for constant, install, fetch in cases:
                for state in ({'tag_name': ''}, {'other': 'value'}, {'tag_name': '../escape'}):
                    content = json.dumps(state)
                    path.write_text(content)
                    with (self.subTest(constant=constant, state=state),
                          patch.object(antistatic_steps, constant, str(path)),
                          patch.object(antistatic_steps, fetch, return_value=(
                              'v0.10.0', 'https://example.test/release')),
                          patch.object(antistatic_steps, 'install_binary_release') as replace):
                        with self.assertRaises(StateReadError):
                            install('amd64')
                        replace.assert_not_called()
                        self.assertEqual(path.read_text(), content)

    @patch('lib.release_management.run')
    def test_binary_staging_shares_destination_filesystem(self, mock_run):
        mock_run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as root:
            persist = MagicMock()
            install_binary_release(
                binary_name='example', binary_path=os.path.join(root, 'example'),
                tag_name='v1.0.0', download_url='https://example.test/release',
                installed_tag=None, persist_installed_tag=persist,
            )
            download = mock_run.call_args_list[0].args[0]
            self.assertIn(os.path.join(root, '.basaltwater-release-'), download)
            self.assertTrue(mock_run.call_args_list[-1].args[0].startswith('mv -T -- '))
            persist.assert_called_once_with('v1.0.0')

    @patch("lib.release_management.fetch_github_releases")
    def test_latest_verified_asset_skips_prereleases_and_requires_digest(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "tag_name": "4.8-beta1",
                "prerelease": True,
                "assets": [],
            },
            {
                "tag_name": "4.7.2-stable",
                "prerelease": False,
                "assets": [
                    {
                        "name": "Godot_v4.7.2-stable_linux.x86_64.zip",
                        "browser_download_url": "https://example.test/godot.zip",
                        "digest": f"sha256:{'a' * 64}",
                    }
                ],
            },
        ]

        self.assertEqual(
            fetch_latest_verified_github_release_asset(
                "godotengine/godot",
                asset_matches=lambda _tag, name: name.endswith("linux.x86_64.zip"),
                missing_asset_description="missing Godot asset",
            ),
            ("4.7.2-stable", "https://example.test/godot.zip", "a" * 64),
        )

    def test_release_tag_is_safe_as_path_component(self):
        self.assertEqual(validate_release_tag("v1.2.3-rc.1"), "v1.2.3-rc.1")
        for invalid in ("../../etc", "release/name", "v1\nnext", "", "."):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_release_tag(invalid)

    def test_release_download_requires_credential_free_https(self):
        self.assertEqual(
            validate_release_download_url("https://example.test/release"),
            "https://example.test/release",
        )
        for invalid in (
            "http://example.test/release",
            "https://token@example.test/release",
            "file:///tmp/release",
            "",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_release_download_url(invalid)

    def test_release_sha256_digest_is_strict_and_normalized(self):
        self.assertEqual(
            validate_release_sha256_digest(f"sha256:{'A' * 64}"),
            "a" * 64,
        )
        for invalid in ("a" * 64, "sha512:" + "a" * 64, "sha256:bad", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_release_sha256_digest(invalid)

    @patch("lib.release_management.run")
    def test_binary_name_cannot_escape_private_download_directory(self, mock_run):
        with self.assertRaisesRegex(ValueError, "Invalid release binary name"):
            install_binary_release(
                binary_name="../escape",
                binary_path="/usr/local/bin/example",
                tag_name="v1.0.0",
                download_url="https://example.test/release",
                installed_tag=None,
                persist_installed_tag=MagicMock(),
            )

        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
