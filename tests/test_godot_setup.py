"""Tests for verified Godot installation, configuration, and maintenance."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import call, patch

from common import godot_steps
from common.service_tools import auto_update_godot
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


class TestGodotSetup(unittest.TestCase):
    def test_parser_and_config_commands_preserve_godot_selection(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["example.com", "--godot"])
        self.assertTrue(args.install_godot)

        config = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            install_godot=True,
        )
        self.assertIn("--godot", config.to_remote_args())
        self.assertIn("--godot", config.to_setup_command())
        self.assertTrue(config.to_dict()["install_godot"])
        restored = SetupConfig.from_dict(
            "example.com",
            "agent_vm",
            config.to_dict(),
        )
        self.assertTrue(restored.install_godot)

    def test_repeatable_bundle_selection_enables_godot_and_round_trips(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "example.com",
                "--godot-bundle",
                "web",
                "--godot-bundle",
                "publishing",
            ]
        )
        self.assertEqual(args.godot_bundles, ["web", "publishing"])
        with (
            patch("lib.system_utils.get_current_username", return_value="agent"),
            patch("lib.system_utils.get_local_timezone", return_value="UTC"),
        ):
            parsed_config = SetupConfig.from_args(args, "agent_vm")
        self.assertTrue(parsed_config.install_godot)
        self.assertEqual(parsed_config.godot_bundles, ["web", "publishing"])

        config = SetupConfig(
            host="example.com",
            username="agent",
            system_type="agent_vm",
            godot_bundles=["web", "publishing", "web"],
        )
        self.assertTrue(config.install_godot)
        self.assertEqual(config.godot_bundles, ["web", "publishing"])
        self.assertIn("--godot-bundle web", config.to_remote_args())
        self.assertIn("--godot-bundle publishing", config.to_setup_command())
        restored = SetupConfig.from_dict(
            "example.com",
            "agent_vm",
            config.to_dict(),
        )
        self.assertEqual(restored.godot_bundles, ["web", "publishing"])
        self.assertTrue(restored.install_godot)

    def test_unsupported_bundle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "--godot-bundle must be one of"):
            SetupConfig(
                host="example.com",
                username="agent",
                system_type="agent_vm",
                godot_bundles=["android"],
            )

    def test_publishing_bundle_rejects_root_account(self):
        with self.assertRaisesRegex(ValueError, "requires a non-root setup user"):
            SetupConfig(
                host="example.com",
                username="root",
                system_type="agent_vm",
                godot_bundles=["publishing"],
            )

    def test_registered_bundle_state_validates_https_identities(self):
        with patch.object(
            godot_steps,
            "read_godot_bundle_state",
            return_value={
                "bundles": ["web"],
                "users": ["agent"],
                "web_identities": ["Games.Example", "192.0.2.10"],
            },
        ):
            bundles, users, identities = godot_steps._validated_registered_bundles()

        self.assertEqual(bundles, ["web"])
        self.assertEqual(users, ["agent"])
        self.assertEqual(identities, ["games.example", "192.0.2.10"])

    def test_legacy_web_bundle_registration_discovers_https_identity(self):
        with (
            patch.object(
                godot_steps,
                "_validated_registered_bundles",
                return_value=(["web"], ["agent"], []),
            ),
            patch.object(
                godot_steps,
                "_validated_registered_web_access_sources",
                return_value=["192.0.2.0/24"],
            ),
            patch(
                "common.godot_web_steps.discover_local_web_identities",
                return_value=["godot-vm", "127.0.0.1"],
            ),
            patch.object(godot_steps, "write_godot_bundle_state") as write_state,
            patch.object(
                godot_steps,
                "_install_selected_godot_bundles",
                return_value=True,
            ) as install_bundles,
        ):
            changed = godot_steps.update_registered_godot_bundles()

        self.assertTrue(changed)
        write_state.assert_called_once_with(
            ["web"],
            ["agent"],
            ["godot-vm", "127.0.0.1"],
            ["192.0.2.0/24"],
        )
        install_bundles.assert_called_once_with(
            ["web"],
            ["agent"],
            ["godot-vm", "127.0.0.1"],
            ["192.0.2.0/24"],
        )

    def test_agent_profile_can_add_graphical_and_headless_godot_steps(self):
        for system_type in ("agent_vm", "agent_workstation"):
            with self.subTest(system_type=system_type):
                config = SetupConfig(
                    host="host",
                    username="agent",
                    system_type=system_type,
                    install_godot=True,
                )
                step_names = [
                    name for name, _step in get_steps_for_system_type(config)
                ]
                self.assertIn("Installing Godot Engine (latest stable)", step_names)
                self.assertIn("Configuring Godot auto-update", step_names)

    def test_bundle_step_runs_after_engine_and_before_auto_update(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="agent_vm",
            godot_bundles=["web", "publishing"],
        )
        step_names = [name for name, _step in get_steps_for_system_type(config)]
        engine_index = step_names.index("Installing Godot Engine (latest stable)")
        bundle_index = step_names.index(
            "Installing Godot bundles (web, publishing)"
        )
        updater_index = step_names.index("Configuring Godot auto-update")
        self.assertLess(engine_index, bundle_index)
        self.assertLess(bundle_index, updater_index)

    @patch("common.godot_steps.fetch_latest_verified_github_release_asset")
    def test_release_selector_matches_official_linux_asset(self, mock_fetch):
        mock_fetch.return_value = (
            "4.7.2-stable",
            "https://github.com/godotengine/godot/releases/download/4.7.2-stable/godot.zip",
            "a" * 64,
        )

        self.assertEqual(
            godot_steps.fetch_latest_godot_release("x86_64"),
            mock_fetch.return_value,
        )
        matcher = mock_fetch.call_args.kwargs["asset_matches"]
        self.assertTrue(
            matcher(
                "4.7.2-stable",
                "Godot_v4.7.2-stable_linux.x86_64.zip",
            )
        )
        self.assertFalse(
            matcher(
                "4.7.2-stable",
                "Godot_v4.7.2-stable_linux.x86_64_mono.zip",
            )
        )

    @patch("common.godot_steps.fetch_latest_verified_github_release_asset")
    def test_web_template_selector_matches_exact_engine_release(self, mock_fetch):
        mock_fetch.return_value = (
            "4.7.2-stable",
            "https://github.com/godotengine/godot/releases/download/templates.tpz",
            "b" * 64,
        )

        self.assertEqual(
            godot_steps.fetch_godot_export_templates("4.7.2-stable"),
            mock_fetch.return_value,
        )
        matcher = mock_fetch.call_args.kwargs["asset_matches"]
        self.assertTrue(
            matcher(
                "4.7.2-stable",
                "Godot_v4.7.2-stable_export_templates.tpz",
            )
        )
        self.assertFalse(
            matcher(
                "4.7.1-stable",
                "Godot_v4.7.1-stable_export_templates.tpz",
            )
        )

    def test_verified_archive_extracts_only_expected_binary(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = os.path.join(temporary_dir, "godot.zip")
            release_dir = os.path.join(temporary_dir, "release")
            member_name = "Godot_v4.7.2-stable_linux.x86_64"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(member_name, b"godot-binary")
                archive.writestr("unrelated", b"not-installed")
            with open(archive_path, "rb") as archive_file:
                expected_sha256 = hashlib.sha256(archive_file.read()).hexdigest()

            binary_path = godot_steps._extract_verified_binary(
                archive_path,
                expected_sha256=expected_sha256,
                binary_member=member_name,
                release_dir=release_dir,
            )

            with open(binary_path, "rb") as binary_file:
                self.assertEqual(binary_file.read(), b"godot-binary")
            self.assertTrue(os.stat(binary_path).st_mode & 0o111)
            self.assertFalse(os.path.exists(os.path.join(release_dir, "unrelated")))

    def test_web_templates_are_range_downloaded_without_full_archive(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = os.path.join(temporary_dir, "templates.tpz")
            releases_dir = os.path.join(temporary_dir, "releases")
            release_dir = os.path.join(releases_dir, "4.7.2.stable-digest")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("templates/version.txt", "4.7.2.stable\n")
                for file_name in godot_steps._GODOT_WEB_TEMPLATE_FILES:
                    archive.writestr(f"templates/{file_name}", file_name.encode())
                archive.writestr(
                    "templates/linux_release.x86_64",
                    b"not-installed"
                    * (godot_steps._REMOTE_ZIP_TAIL_SIZE // len(b"not-installed") + 1),
                )
            archive_size = os.path.getsize(archive_path)
            requested_ranges: list[tuple[int, int]] = []

            def copy_range(
                _download_url: str,
                start: int,
                end: int,
                destination_path: str,
                *,
                label: str,
            ) -> None:
                del label
                requested_ranges.append((start, end))
                with open(archive_path, "rb") as source:
                    source.seek(start)
                    payload = source.read(end - start + 1)
                with open(destination_path, "wb") as destination:
                    destination.write(payload)

            with (
                patch.object(
                    godot_steps,
                    "GODOT_EXPORT_TEMPLATE_RELEASES_DIR",
                    releases_dir,
                ),
                patch.object(
                    godot_steps,
                    "_remote_https_content_length",
                    return_value=archive_size,
                ),
                patch.object(
                    godot_steps,
                    "_download_https_range",
                    side_effect=copy_range,
                ),
            ):
                godot_steps._download_remote_web_templates(
                    "https://example.test/templates.tpz",
                    expected_version="4.7.2.stable",
                    release_dir=release_dir,
                )

            self.assertTrue(os.path.isfile(os.path.join(release_dir, "web_release.zip")))
            self.assertFalse(
                os.path.exists(os.path.join(release_dir, "linux_release.x86_64"))
            )
            self.assertTrue(requested_ranges)
            self.assertTrue(
                all(end - start + 1 < archive_size for start, end in requested_ranges)
            )

    @patch("common.godot_steps.run")
    def test_template_content_length_uses_final_redirect_header(self, mock_run):
        mock_run.return_value.stdout = (
            "HTTP/2 302\r\ncontent-length: 0\r\n\r\n"
            "HTTP/2 200\r\ncontent-length: 1281349702\r\n\r\n"
        )

        self.assertEqual(
            godot_steps._remote_https_content_length(
                "https://example.test/templates.tpz"
            ),
            1281349702,
        )

    def test_template_range_rejects_server_ignoring_range(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            destination_path = os.path.join(temporary_dir, "range.bin")

            def write_oversized_response(*_args, **_kwargs):
                with open(destination_path, "wb") as destination:
                    destination.write(b"not one byte")

            with patch("common.godot_steps.run", side_effect=write_oversized_response):
                with self.assertRaisesRegex(RuntimeError, "did not honor"):
                    godot_steps._download_https_range(
                        "https://example.test/templates.tpz",
                        0,
                        0,
                        destination_path,
                        label="test range",
                    )

    @patch("common.godot_steps.fetch_latest_verified_github_release_asset")
    def test_butler_selector_matches_linux_architecture(self, mock_fetch):
        mock_fetch.return_value = (
            "v15.30.0",
            "https://github.com/itchio/butler/releases/download/butler.zip",
            "c" * 64,
        )
        self.assertEqual(
            godot_steps.fetch_latest_butler_release("arm64"),
            mock_fetch.return_value,
        )
        matcher = mock_fetch.call_args.kwargs["asset_matches"]
        self.assertTrue(matcher("v15.30.0", "butler-linux-arm64.zip"))
        self.assertFalse(matcher("v15.30.0", "butler-linux-amd64.zip"))

    def test_verified_butler_archive_extracts_only_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = os.path.join(temporary_dir, "butler.zip")
            releases_dir = os.path.join(temporary_dir, "releases")
            release_dir = os.path.join(releases_dir, "v15.30.0-digest")
            with zipfile.ZipFile(archive_path, "w") as archive:
                for file_name in ("butler", "7z.so", "libc7zip.so"):
                    archive.writestr(f"linux-amd64/{file_name}", file_name.encode())
                archive.writestr("linux-amd64/unrelated", b"not-installed")
            with open(archive_path, "rb") as archive_file:
                expected_sha256 = hashlib.sha256(archive_file.read()).hexdigest()

            with patch.object(godot_steps, "BUTLER_RELEASES_DIR", releases_dir):
                godot_steps._extract_verified_butler_archive(
                    archive_path,
                    expected_sha256=expected_sha256,
                    asset_arch="amd64",
                    release_dir=release_dir,
                )

            self.assertTrue(os.access(os.path.join(release_dir, "butler"), os.X_OK))
            self.assertFalse(os.path.exists(os.path.join(release_dir, "unrelated")))

    def test_pinned_steamcmd_bootstrap_extracts_expected_files(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = os.path.join(temporary_dir, "steamcmd.tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                for member_name in godot_steps._STEAMCMD_BOOTSTRAP_FILES:
                    payload = member_name.encode()
                    member = tarfile.TarInfo(member_name)
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            with open(archive_path, "rb") as archive_file:
                archive_sha256 = hashlib.sha256(archive_file.read()).hexdigest()
            destination = os.path.join(temporary_dir, "destination")

            with patch.object(
                godot_steps,
                "STEAMCMD_BOOTSTRAP_SHA256",
                archive_sha256,
            ):
                godot_steps._extract_steamcmd_bootstrap(archive_path, destination)

            self.assertTrue(
                os.access(os.path.join(destination, "steamcmd.sh"), os.X_OK)
            )
            self.assertTrue(
                os.path.isfile(os.path.join(destination, "linux32", "steamcmd"))
            )

    def test_pinned_steamcmd_bootstrap_rejects_unexpected_members(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = os.path.join(temporary_dir, "steamcmd.tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                for member_name in godot_steps._STEAMCMD_BOOTSTRAP_FILES:
                    payload = member_name.encode()
                    member = tarfile.TarInfo(member_name)
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                payload = b"unexpected"
                member = tarfile.TarInfo("unexpected")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            with open(archive_path, "rb") as archive_file:
                archive_sha256 = hashlib.sha256(archive_file.read()).hexdigest()

            with patch.object(
                godot_steps,
                "STEAMCMD_BOOTSTRAP_SHA256",
                archive_sha256,
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected contents"):
                    godot_steps._extract_steamcmd_bootstrap(
                        archive_path,
                        os.path.join(temporary_dir, "destination"),
                    )

    @patch("common.godot_steps.configure_maintenance_timer", return_value=True)
    def test_auto_update_timer_tracks_non_apt_release(self, mock_configure):
        config = SetupConfig(host="host", username="agent", system_type="agent_vm")
        godot_steps.configure_auto_update_godot(config)
        mock_configure.assert_called_once_with(
            service_name="auto-update-godot",
            service_desc="Auto-update Godot Engine",
            timer_desc="Auto-update Godot Engine weekly",
            script_path="/opt/basaltwater/common/service_tools/auto_update_godot.py",
            schedule="Sun *-*-* 06:30:00",
            check_path="/usr/local/bin/godot",
            check_name="Godot",
            purpose="auto-update",
        )

    @patch("common.godot_steps.run")
    def test_systemwide_links_make_godot_available_to_agent_users(self, mock_run):
        godot_steps._install_godot_links("/opt/godot/releases/4.7.2-stable-digest")
        self.assertEqual(
            mock_run.call_args_list,
            [
                call(
                    "ln -sfn /opt/godot/releases/4.7.2-stable-digest /opt/godot/current",
                    check=True,
                ),
                call(
                    "ln -sfn /opt/godot/current/godot /usr/local/bin/godot",
                    check=True,
                ),
                call(
                    "ln -sfn /opt/godot/current/godot /usr/local/bin/godot4",
                    check=True,
                ),
            ],
        )

    def test_missing_state_reverifies_matching_active_release(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            releases_dir = os.path.join(temporary_dir, "releases")
            current_dir = os.path.join(temporary_dir, "current")
            release_dir = os.path.join(
                releases_dir,
                f"4.7.2-stable-{'a' * 12}",
            )
            os.makedirs(current_dir)
            current_binary = os.path.join(current_dir, "godot")
            with open(current_binary, "wb") as binary_file:
                binary_file.write(b"previously installed")

            with (
                patch.object(godot_steps, "GODOT_RELEASES_DIR", releases_dir),
                patch.object(godot_steps, "GODOT_CURRENT_DIR", current_dir),
                patch.object(godot_steps, "detect_release_arch", return_value="amd64"),
                patch.object(
                    godot_steps,
                    "fetch_latest_godot_release",
                    return_value=(
                        "4.7.2-stable",
                        "https://example.test/godot.zip",
                        "a" * 64,
                    ),
                ),
                patch.object(godot_steps, "read_godot_state", return_value={}),
                patch.object(
                    godot_steps,
                    "_managed_current_release",
                    return_value=os.path.realpath(release_dir),
                ),
                patch.object(
                    godot_steps,
                    "_extract_verified_binary",
                    return_value=current_binary,
                ) as extract_binary,
                patch.object(godot_steps, "_install_godot_links"),
                patch.object(godot_steps, "_install_godot_desktop_entry"),
                patch.object(godot_steps, "write_godot_state") as write_state,
                patch.object(godot_steps, "run"),
            ):
                result = godot_steps.install_or_update_godot_release()

            self.assertEqual(result, ("4.7.2-stable", True, "a" * 64))
            extract_binary.assert_called_once()
            write_state.assert_called_once_with("4.7.2-stable", "a" * 64)


class TestGodotAutoUpdater(unittest.TestCase):
    @patch(
        "common.service_tools.auto_update_godot.update_registered_godot_bundles",
        return_value=False,
    )
    @patch("common.service_tools.auto_update_godot.send_notification_safe")
    @patch("common.service_tools.auto_update_godot.log_event")
    @patch(
        "common.service_tools.auto_update_godot.load_notification_configs_from_state",
        return_value=[],
    )
    @patch("common.service_tools.auto_update_godot.os.path.exists", return_value=True)
    @patch(
        "common.service_tools.auto_update_godot.install_or_update_godot_release",
        return_value=("4.7.2-stable", True, "a" * 64),
    )
    def test_successful_update_notifies(
        self,
        _install_release,
        _exists,
        _load_notifications,
        _log_event,
        mock_notify,
        _update_bundles,
    ):
        self.assertEqual(auto_update_godot.main(), 0)
        self.assertEqual(mock_notify.call_args.kwargs["status"], "good")

    @patch(
        "common.service_tools.auto_update_godot.update_registered_godot_bundles",
        return_value=True,
    )
    @patch("common.service_tools.auto_update_godot.send_notification_safe")
    @patch("common.service_tools.auto_update_godot.log_event")
    @patch(
        "common.service_tools.auto_update_godot.load_notification_configs_from_state",
        return_value=[],
    )
    @patch("common.service_tools.auto_update_godot.os.path.exists", return_value=True)
    @patch(
        "common.service_tools.auto_update_godot.install_or_update_godot_release",
        return_value=("4.7.2-stable", False, "a" * 64),
    )
    def test_bundle_only_update_notifies(
        self,
        _install_release,
        _exists,
        _load_notifications,
        _log_event,
        mock_notify,
        _update_bundles,
    ):
        self.assertEqual(auto_update_godot.main(), 0)
        self.assertEqual(mock_notify.call_args.kwargs["status"], "good")

    @patch("common.service_tools.auto_update_godot.send_notification_safe")
    @patch("common.service_tools.auto_update_godot.log_event")
    @patch(
        "common.service_tools.auto_update_godot.load_notification_configs_from_state",
        return_value=["notification"],
    )
    @patch("common.service_tools.auto_update_godot.os.path.exists", return_value=True)
    @patch(
        "common.service_tools.auto_update_godot.install_or_update_godot_release",
        side_effect=RuntimeError("download failed"),
    )
    def test_failed_update_notifies_and_returns_failure(
        self,
        _install_release,
        _exists,
        _load_notifications,
        _log_event,
        mock_notify,
    ):
        self.assertEqual(auto_update_godot.main(), 1)
        self.assertEqual(mock_notify.call_args.kwargs["status"], "error")


if __name__ == "__main__":
    unittest.main()
