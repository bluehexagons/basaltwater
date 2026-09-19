"""Tests for managed Syncthing service composition and policy."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type
from sync.syncthing_steps import (
    DEFAULT_SYNCTHING_ROOT,
    SYNCTHING_HOME,
    _configure_syncthing_https,
    _preflight_existing_folders,
    _prepare_share_root,
    _render_service,
    _put_config,
    build_syncthing_policy_config,
    setup_syncthing,
)


LOCAL_DEVICE_ID = (
    "BJ5ID3D-3BL2IM7-KPTHNB3-LI3SO5N-KDCFYJN-Z4HKBUQ-AIANLCB-LJOJXAT"
)
REMOTE_DEVICE_ID = (
    "S7UKX27-GI7ZTXS-GC6RKUA-7AJGZ44-C6NAYEB-HSKTJQK-KJHU2NO-CWV7EQW"
)


def _config(system_type: str = "server_lite", **overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "fileserver",
        "username": "agent",
        "system_type": system_type,
        "enable_syncthing": True,
        "share_credentials": [["syncthing-admin", "correct horse battery staple"]],
    }
    values.update(overrides)
    return SetupConfig(**values)  # type: ignore[arg-type]


def _current_config() -> dict[str, object]:
    return {
        "version": 37,
        "folders": [
            {
                "id": "shared-work",
                "path": "/srv/syncthing/shared-work",
                "devices": [
                    {"deviceID": LOCAL_DEVICE_ID},
                    {"deviceID": REMOTE_DEVICE_ID},
                ],
                "versioning": {
                    "type": "trashcan",
                    "params": {"cleanoutDays": "30"},
                },
            }
        ],
        "devices": [
            {"deviceID": LOCAL_DEVICE_ID, "name": "fileserver"},
            {"deviceID": REMOTE_DEVICE_ID, "name": "alice-laptop"},
        ],
        "gui": {
            "enabled": True,
            "address": "0.0.0.0:8384",
            "apiKey": "test-api-key",
            "user": "syncthing-admin",
            "password": "$2a$10$hashed",
            "useTLS": True,
        },
        "options": {
            "startBrowser": True,
            "relaysEnabled": False,
            "crashReportingEnabled": True,
        },
        "defaults": {
            "folder": {
                "path": "~",
                "versioning": {
                    "type": "",
                    "params": {},
                    "cleanupIntervalS": 3600,
                    "fsPath": "",
                    "fsType": "basic",
                },
            },
        },
    }


class SyncthingDesiredConfigTest(unittest.TestCase):
    def test_policy_preserves_gui_devices_folders_and_versioning(self) -> None:
        current = _current_config()

        desired = build_syncthing_policy_config(current, DEFAULT_SYNCTHING_ROOT)

        self.assertEqual(desired["devices"], current["devices"])
        self.assertEqual(desired["folders"], current["folders"])
        self.assertEqual(desired["folders"][0]["versioning"]["type"], "trashcan")
        self.assertEqual(desired["gui"]["address"], "127.0.0.1:8384")
        self.assertFalse(desired["gui"]["useTLS"])
        self.assertFalse(desired["gui"]["insecureSkipHostcheck"])
        self.assertTrue(desired["options"]["relaysEnabled"])
        self.assertFalse(desired["options"]["natEnabled"])
        self.assertEqual(
            desired["options"]["defaultFolderPath"],
            DEFAULT_SYNCTHING_ROOT,
        )
        self.assertEqual(
            desired["defaults"]["folder"]["path"],
            DEFAULT_SYNCTHING_ROOT,
        )
        self.assertEqual(
            desired["defaults"]["folder"]["versioning"]["type"],
            "staggered",
        )

    def test_policy_requires_offline_admin_configuration(self) -> None:
        current = _current_config()
        current["gui"].pop("password")

        with self.assertRaisesRegex(RuntimeError, "password was not configured"):
            build_syncthing_policy_config(current, DEFAULT_SYNCTHING_ROOT)

    def test_policy_rejects_gui_folder_outside_storage_root(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outside the configured"):
            build_syncthing_policy_config(
                _current_config(),
                "/mnt/team-files",
            )

    def test_policy_rejects_folder_path_traversal(self) -> None:
        current = _current_config()
        current["folders"][0]["path"] = "/srv/syncthing/../../etc"

        with self.assertRaisesRegex(RuntimeError, "outside the configured"):
            build_syncthing_policy_config(current, DEFAULT_SYNCTHING_ROOT)

    def test_policy_rejects_folder_root_symlink_escape(self) -> None:
        current = _current_config()
        with tempfile.TemporaryDirectory() as temporary_dir:
            share_root = os.path.join(temporary_dir, "shares")
            outside = os.path.join(temporary_dir, "outside")
            os.mkdir(share_root)
            os.mkdir(outside)
            os.symlink(outside, os.path.join(share_root, "linked"))
            current["folders"][0]["path"] = os.path.join(share_root, "linked")

            with self.assertRaisesRegex(RuntimeError, "outside the configured"):
                build_syncthing_policy_config(current, share_root)

    def test_service_is_unprivileged_hardened_and_confined(self) -> None:
        service = _render_service(_config(), "agent")
        self.assertIn("User=agent", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectHome=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("CapabilityBoundingSet=", service)
        self.assertIn("--gui-address=http://127.0.0.1:8384", service)
        self.assertIn(f'ReadWritePaths="{SYNCTHING_HOME}"', service)
        self.assertIn(f'ReadWritePaths="{DEFAULT_SYNCTHING_ROOT}"', service)
        self.assertIn(
            f'RequiresMountsFor="{DEFAULT_SYNCTHING_ROOT}"',
            service,
        )

    def test_service_uses_custom_storage_root(self) -> None:
        service = _render_service(
            _config(syncthing_root="/mnt/team-files"),
            "agent",
        )

        self.assertIn('ReadWritePaths="/mnt/team-files"', service)
        self.assertIn('RequiresMountsFor="/mnt/team-files"', service)
        self.assertNotIn(f'ReadWritePaths="{DEFAULT_SYNCTHING_ROOT}"', service)


class SyncthingCompositionTest(unittest.TestCase):
    def test_put_config_uses_the_literal_loopback_client(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 204
        desired = _current_config()
        with patch("sync.syncthing_steps.open_loopback", return_value=response) as open_client:
            _put_config(desired)

        request = open_client.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8384/rest/config")
        self.assertEqual(open_client.call_args.kwargs, {"timeout": 20})

    def test_share_root_rejects_symbolic_link_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            target = os.path.join(temporary_dir, "target")
            link = os.path.join(temporary_dir, "link")
            os.mkdir(target)
            os.symlink(target, link)
            config = SimpleNamespace(
                username="agent",
                syncthing_root=os.path.join(link, "shares"),
                storage_mounts=None,
            )
            with (
                patch("sync.syncthing_steps.assert_declared_storage_mount"),
                patch("sync.syncthing_steps.run") as run_command,
            ):
                with self.assertRaisesRegex(RuntimeError, "symbolic-link traversal"):
                    _prepare_share_root(config, "agent")  # type: ignore[arg-type]
            run_command.assert_not_called()

    def test_share_root_verifies_declared_mount_before_install(self) -> None:
        config = SimpleNamespace(
            username="agent",
            syncthing_root="/srv/syncthing",
            storage_mounts=[
                ["syncthing-data", "/srv/syncthing", "ext4", "empty"]
            ],
        )
        with (
            patch(
                "sync.syncthing_steps.assert_declared_storage_mount",
                side_effect=RuntimeError("Required VM data mount is not active"),
            ) as assert_mount,
            patch("sync.syncthing_steps.run") as run_command,
        ):
            with self.assertRaisesRegex(RuntimeError, "mount is not active"):
                _prepare_share_root(config, "agent")  # type: ignore[arg-type]

        assert_mount.assert_called_once_with(config, "/srv/syncthing")
        run_command.assert_not_called()

    def test_running_service_preflights_folder_paths_before_root_change(self) -> None:
        active = subprocess.CompletedProcess(args=[], returncode=0)
        with (
            patch("sync.syncthing_steps.run", return_value=active),
            patch("sync.syncthing_steps._wait_for_api") as wait_for_api,
            patch(
                "sync.syncthing_steps._load_current_config",
                return_value=_current_config(),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "outside the configured"):
                _preflight_existing_folders("agent", "/mnt/team-files")

        wait_for_api.assert_called_once_with("agent")

    def test_server_and_workstation_compositions_include_syncthing(self) -> None:
        for system_type in ("server_lite", "workstation_desktop"):
            with self.subTest(system_type=system_type):
                step_names = [
                    name
                    for name, _step in get_steps_for_system_type(
                        _config(system_type)
                    )
                ]
                self.assertIn("Configuring managed Syncthing endpoint", step_names)

    def test_disable_removes_gateway_and_service_but_preserves_state(self) -> None:
        config = _config(
            enable_syncthing=False,
            disable_syncthing=True,
            share_credentials=None,
        )
        with (
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps._remove_syncthing_https") as remove_https,
            patch("sync.syncthing_steps.os.path.lexists", return_value=True),
            patch("sync.syncthing_steps.os.path.islink", return_value=False),
            patch("sync.syncthing_steps.os.path.isfile", return_value=True),
            patch("sync.syncthing_steps.os.unlink") as unlink,
            patch("sync.syncthing_steps.run") as run_command,
        ):
            run_command.return_value = subprocess.CompletedProcess(args=[], returncode=3)
            setup_syncthing(config)

        remove_https.assert_called_once_with(config)
        unlink.assert_called_once()
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertIn(
            ["systemctl", "disable", "--now", "basaltwater-syncthing.service"],
            commands,
        )
        self.assertIn(
            ["systemctl", "is-active", "--quiet", "basaltwater-syncthing.service"],
            commands,
        )

    def test_disable_refuses_to_remove_unit_while_service_is_active(self) -> None:
        config = _config(
            enable_syncthing=False,
            disable_syncthing=True,
            share_credentials=None,
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with (
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps._remove_syncthing_https"),
            patch("sync.syncthing_steps.os.path.lexists", return_value=True),
            patch("sync.syncthing_steps.os.path.islink", return_value=False),
            patch("sync.syncthing_steps.os.path.isfile", return_value=True),
            patch("sync.syncthing_steps.os.unlink") as unlink,
            patch("sync.syncthing_steps.run", return_value=completed),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                setup_syncthing(config)
        unlink.assert_not_called()

    def test_setup_hashes_admin_from_stdin_and_preserves_gui_shares(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=LOCAL_DEVICE_ID + "\n",
            stderr="",
        )
        with (
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps.is_package_installed", return_value=True),
            patch("sync.syncthing_steps._account", return_value=(1000, 1000, "agent")),
            patch("sync.syncthing_steps.os.makedirs"),
            patch("sync.syncthing_steps.os.chmod"),
            patch("sync.syncthing_steps.os.chown"),
            patch("sync.syncthing_steps._prepare_share_root"),
            patch("sync.syncthing_steps.write_text_atomic"),
            patch("sync.syncthing_steps.run") as run_command,
            patch("sync.syncthing_steps._wait_for_api"),
            patch(
                "sync.syncthing_steps._run_as_user",
                return_value=completed,
            ) as run_as_user,
            patch(
                "sync.syncthing_steps._load_current_config",
                return_value=_current_config(),
            ),
            patch("sync.syncthing_steps._put_config") as put_config,
            patch(
                "sync.syncthing_steps._configure_syncthing_https",
                return_value="https://fileserver:8444/",
            ),
        ):
            run_command.return_value = completed
            setup_syncthing(_config())

        generate_call = run_as_user.call_args_list[0]
        self.assertIn("--gui-password=-", generate_call.args[1])
        self.assertEqual(
            generate_call.kwargs["input_data"],
            "correct horse battery staple\n",
        )
        desired = put_config.call_args.args[0]
        self.assertEqual(desired["folders"][0]["id"], "shared-work")
        self.assertEqual(desired["devices"][1]["name"], "alice-laptop")

    def test_setup_disables_service_when_post_start_policy_is_unsafe(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=LOCAL_DEVICE_ID + "\n",
            stderr="",
        )
        with (
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps.is_package_installed", return_value=True),
            patch("sync.syncthing_steps._preflight_existing_folders"),
            patch("sync.syncthing_steps._account", return_value=(1000, 1000, "agent")),
            patch("sync.syncthing_steps.os.makedirs"),
            patch("sync.syncthing_steps.os.chmod"),
            patch("sync.syncthing_steps.os.chown"),
            patch("sync.syncthing_steps._prepare_share_root"),
            patch("sync.syncthing_steps.write_text_atomic"),
            patch("sync.syncthing_steps.run", return_value=completed) as run_command,
            patch("sync.syncthing_steps._wait_for_api"),
            patch("sync.syncthing_steps._run_as_user", return_value=completed),
            patch(
                "sync.syncthing_steps._load_current_config",
                return_value=_current_config(),
            ),
            patch("sync.syncthing_steps._put_config") as put_config,
        ):
            with self.assertRaisesRegex(RuntimeError, "outside the configured"):
                setup_syncthing(_config(syncthing_root="/mnt/team-files"))

        put_config.assert_not_called()
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertIn(
            ["systemctl", "disable", "--now", "basaltwater-syncthing.service"],
            commands,
        )

    def test_https_route_uses_shared_gateway_and_syncthing_profile(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"listen": 8444, "url": "https://fileserver:8444/"}',
            stderr="",
        )
        config = _config(access_sources=["192.0.2.0/24"])
        with (
            patch("sync.syncthing_steps.os.geteuid", return_value=0),
            patch("sync.syncthing_steps.os.path.isfile", return_value=True),
            patch(
                "common.godot_web_steps.identities_for_config",
                return_value=["fileserver"],
            ),
            patch(
                "common.godot_web_steps.configure_internal_web_host"
            ) as configure_host,
            patch("sync.syncthing_steps.run", return_value=completed) as run_command,
        ):
            url = _configure_syncthing_https(config)

        self.assertEqual(url, "https://fileserver:8444/")
        configure_host.assert_called_once_with(
            ["fileserver"],
            ["agent"],
            ["192.0.2.0/24"],
            configure_static_site=True,
            install_utility=True,
        )
        command = run_command.call_args.args[0]
        self.assertIn("--to 127.0.0.1:8384", command)
        self.assertIn("--profile syncthing", command)


if __name__ == "__main__":
    unittest.main()
