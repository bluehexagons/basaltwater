"""Tests for managed Syncthing service composition and policy."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib.atomic_io import write_bytes_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type
from sync.syncthing_steps import (
    DEFAULT_SYNCTHING_ROOT,
    SYNCTHING_HOME,
    _configure_syncthing_https,
    _disable_before_removal,
    _preflight_existing_folders,
    _prepare_share_root,
    _prepare_state_home,
    _render_service,
    _put_config,
    _validate_state_paths,
    _stop_before_configuration,
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
        self.assertIn("PrivateTmp=true", service)
        self.assertIn("PrivateDevices=true", service)
        self.assertEqual(
            [line for line in service.splitlines() if line.startswith("ReadOnlyPaths=")],
            ["ReadOnlyPaths=/tmp /var/tmp /dev/shm"],
        )
        self.assertEqual(
            [line for line in service.splitlines() if line.startswith("ReadWritePaths=")],
            [f'ReadWritePaths="{SYNCTHING_HOME}"', f'ReadWritePaths="{DEFAULT_SYNCTHING_ROOT}"'],
        )
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


class SyncthingShareRootTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = os.path.join(temporary.name, "private")
        self.root = os.path.join(self.parent, "sync")
        os.mkdir(self.parent, 0o700)
        self.config = SimpleNamespace(username="agent", syncthing_root=self.root, storage_mounts=None)
        # Only temporary test paths bypass the CLI prefix policy. Reserved
        # paths have separate tests that exercise the real validator first.
        self.enterContext(patch("sync.syncthing_steps._validate_syncthing_root"))
        self.enterContext(patch("sync.syncthing_steps._account", return_value=(os.getuid(), os.getgid(), "agent")))
        self.mount = self.enterContext(patch("sync.syncthing_steps.assert_declared_storage_mount"))
        self.chown = self.enterContext(patch("sync.syncthing_steps.os.fchown"))
        self.run_command = self.enterContext(patch("sync.syncthing_steps.run", return_value=SimpleNamespace(returncode=0)))

    def test_new_leaf_changes_only_leaf_and_owned_nonempty_rerun_preserves_acl_mode(self) -> None:
        _prepare_share_root(self.config, "agent")
        self.chown.assert_called_once()
        self.assertEqual(os.stat(self.parent).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o770)
        with open(os.path.join(self.root, "data"), "w") as stream:
            stream.write("preserved data")
        # A deliberately prepared root can retain a setgid/group ACL mask.
        os.chmod(self.root, 0o2750)
        self.chown.reset_mock()
        _prepare_share_root(self.config, "agent")
        self.chown.assert_not_called()
        self.assertEqual(os.stat(self.root).st_mode & 0o7777, 0o2750)
        self.assertEqual(os.stat(self.parent).st_mode & 0o777, 0o700)
        with open(os.path.join(self.root, "data")) as stream:
            self.assertEqual(stream.read(), "preserved data")

    def test_unmanaged_nonempty_root_requires_explicit_preparation(self) -> None:
        os.mkdir(self.root, 0o755)
        with open(os.path.join(self.root, ".hidden-data"), "w") as stream:
            stream.write("unmanaged data")
        for uid, gid, mode in (
            (os.getuid() + 1, os.getgid(), 0o40770),
            (os.getuid(), os.getgid() + 1, 0o40770),
            (os.getuid(), os.getgid(), 0o40777),
            (os.getuid(), os.getgid(), 0o40660),
        ):
            with self.subTest(uid=uid, gid=gid, mode=mode), patch(
                "sync.syncthing_steps.os.fstat",
                return_value=SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode),
            ):
                with self.assertRaisesRegex(RuntimeError, "explicitly prepare only that directory with chown agent:agent and chmod 0770"):
                    _prepare_share_root(self.config, "agent")
        self.chown.assert_not_called()
        self.run_command.assert_not_called()
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o755)

    def test_verified_fresh_mount_allows_only_metadata_and_empty_recovery_directory(self) -> None:
        os.mkdir(self.root, 0o755)
        with open(os.path.join(self.root, ".basaltwater-storage.json"), "w") as stream:
            stream.write("mount marker checked by the mocked mount verifier")
        os.mkdir(os.path.join(self.root, "lost+found"), 0o700)
        self.config.storage_mounts = [["data", self.root, "ext4", "empty"]]
        _prepare_share_root(self.config, "agent")
        self.chown.assert_called_once()
        self.mount.assert_called_once_with(self.config, self.root)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o770)

    def test_mount_declaration_does_not_authorize_unmanaged_data(self) -> None:
        os.mkdir(self.root, 0o755)
        self.config.storage_mounts = [["data", self.root, "ext4", "empty"]]
        for relative in ("foreign-data", "lost+found/recovered-data"):
            with self.subTest(relative=relative):
                path = os.path.join(self.root, relative)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as stream:
                    stream.write("do not adopt")
                with self.assertRaisesRegex(RuntimeError, "Refusing to adopt nonempty"):
                    _prepare_share_root(self.config, "agent")
                os.unlink(path)
        self.chown.assert_not_called()
        self.run_command.assert_not_called()

    def test_missing_parent_requires_operator_preparation(self) -> None:
        self.config.syncthing_root = os.path.join(self.parent, "missing", "sync")
        with self.assertRaisesRegex(RuntimeError, "prepare its parent directory or mount explicitly"):
            _prepare_share_root(self.config, "agent")
        self.assertFalse(os.path.exists(os.path.join(self.parent, "missing")))
        self.chown.assert_not_called()

    def test_inaccessible_ancestor_or_root_aborts_setup_before_stop_or_config_changes(self) -> None:
        os.mkdir(self.root, 0o770)
        for failed_probe in (0, 1, 2):
            with (
                self.subTest(failed_probe=failed_probe),
                patch("sync.syncthing_steps._validate_state_paths"),
                patch("sync.syncthing_steps._assert_no_syncthing_recovery"),
                patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
                patch("sync.syncthing_steps.is_dry_run", return_value=False),
                patch("sync.syncthing_steps.is_package_installed", return_value=True),
                patch("sync.syncthing_steps._prepare_state_home"),
                patch("sync.syncthing_steps._preflight_existing_folders"),
                patch("sync.syncthing_steps._stop_before_configuration") as stop,
                patch("sync.syncthing_steps._run_as_user") as generate,
                patch("sync.syncthing_steps.write_text_atomic") as write_unit,
            ):
                self.run_command.reset_mock()
                self.run_command.side_effect = [SimpleNamespace(returncode=0)] * failed_probe + [SimpleNamespace(returncode=1)]
                config = _config()
                config.syncthing_root = self.root
                with self.assertRaisesRegex(RuntimeError, "Explicitly grant traversal on its ancestors"):
                    setup_syncthing(config)
                stop.assert_not_called()
                generate.assert_not_called()
                write_unit.assert_not_called()
                self.assertEqual(self.run_command.call_args.args[0], [
                    "runuser", "-u", "agent", "--", "/usr/bin/test", ("-r", "-x", "-w")[failed_probe], self.root,
                ])
                self.assertEqual(os.stat(self.parent).st_mode & 0o777, 0o700)


class SyncthingCompositionTest(unittest.TestCase):
    def test_reserved_share_root_is_rejected_before_any_host_observation(self) -> None:
        for root in ("/var/lib/dpkg", "/var/lib/dpkg/updates", "/var/lib/apt/empty"):
            with (
                self.subTest(root=root),
                patch("sync.syncthing_steps.os.path.lexists") as exists,
                patch("sync.syncthing_steps.assert_declared_storage_mount") as mount,
                patch("sync.syncthing_steps.run") as run_command,
            ):
                with self.assertRaisesRegex(ValueError, "reserved system trees"):
                    _prepare_share_root(SimpleNamespace(syncthing_root=root), "agent")
                exists.assert_not_called()
                mount.assert_not_called()
                run_command.assert_not_called()

    def test_native_roots_fresh_mount_and_owned_rerun_do_not_change_parents(self) -> None:
        for root in ("/srv/lab-data/syncthing", "/srv/syncthing"):
            for owned in (False, True):
                with (
                    self.subTest(root=root, owned=owned),
                    patch("sync.syncthing_steps.os.path.lexists", return_value=True),
                    patch("sync.syncthing_steps.os.path.isdir", return_value=True),
                    patch("sync.syncthing_steps.os.path.islink", return_value=False),
                    patch("sync.syncthing_steps.os.path.realpath", side_effect=lambda path: path),
                    patch("sync.syncthing_steps.assert_declared_storage_mount") as mount,
                    patch("sync.syncthing_steps._account", return_value=(1000, 1000, "agent")),
                    patch("sync.syncthing_steps.directory_fd") as open_directory,
                    patch("sync.syncthing_steps.os.mkdir", side_effect=FileExistsError if owned else None) as mkdir,
                    patch("sync.syncthing_steps.os.fstat", return_value=SimpleNamespace(st_uid=1000 if owned else 0, st_gid=1000 if owned else 0, st_mode=0o40770 if owned else 0o40755)),
                    patch("sync.syncthing_steps.os.listdir", return_value=["team-data"] if owned else []),
                    patch("sync.syncthing_steps.os.fchown") as chown,
                    patch("sync.syncthing_steps.os.fchmod") as chmod,
                    patch("sync.syncthing_steps.run", return_value=SimpleNamespace(returncode=0)) as run_command,
                ):
                    config = SimpleNamespace(
                        username="agent", syncthing_root=root,
                        storage_mounts=[["data", root, "ext4", "empty"]],
                    )
                    _prepare_share_root(config, "agent")
                    mount.assert_called_once_with(config, root)
                    self.assertEqual(open_directory.call_args_list[0].args, (os.path.dirname(root),))
                    self.assertEqual(open_directory.call_args_list[0].kwargs, {})
                    self.assertEqual(open_directory.call_args_list[1].args, (root,))
                    self.assertEqual(open_directory.call_args_list[1].kwargs, {})
                    mkdir.assert_called_once_with(os.path.basename(root), mode=0o770, dir_fd=open_directory.return_value.__enter__.return_value)
                    if owned:
                        chown.assert_not_called()
                        chmod.assert_not_called()
                    else:
                        descriptor = open_directory.return_value.__enter__.return_value
                        chown.assert_called_once_with(descriptor, 1000, 1000)
                        chmod.assert_called_once_with(descriptor, 0o770)
                    self.assertEqual([call.args[0] for call in run_command.call_args_list], [
                        ["runuser", "-u", "agent", "--", "/usr/bin/test", permission, root]
                        for permission in ("-r", "-x", "-w")
                    ])

    def test_state_home_restores_traversal_without_replacing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            parent = os.path.join(root, "private")
            home = os.path.join(parent, "syncthing")
            os.makedirs(home)
            key = os.path.join(home, "key.pem")
            with open(key, "w") as stream:
                stream.write("existing identity")
            os.chmod(parent, 0o700)
            with patch("sync.syncthing_steps.SYNCTHING_HOME", home), patch("sync.syncthing_steps.is_package_installed", return_value=False), patch("sync.syncthing_steps.os.chown") as chown, patch("sync.syncthing_steps.run") as run_command:
                _prepare_state_home("gitadmin", 1001, 1001)
            commands = [call.args[0] for call in run_command.call_args_list]
            self.assertIn(["setfacl", "-m", "u:1001:--x", "--", parent], commands)
            self.assertIn(["runuser", "-u", "gitadmin", "--", "/usr/bin/test", "-w", home], commands)
            self.assertEqual(os.stat(parent).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(home).st_mode & 0o777, 0o700)
            chown.assert_called_once_with(home, 1001, 1001)
            with open(key) as stream:
                self.assertEqual(stream.read(), "existing identity")

    def test_state_symlinks_are_rejected_before_ownership_changes(self) -> None:
        for child in (None, "config.xml", "cert.pem", "key.pem"):
            with self.subTest(child=child), tempfile.TemporaryDirectory() as directory:
                home = os.path.join(directory, "state")
                target = os.path.join(directory, "target")
                os.mkdir(target)
                if child is None:
                    os.symlink(target, home)
                else:
                    os.mkdir(home)
                    os.symlink(target, os.path.join(home, child))
                with patch("sync.syncthing_steps.SYNCTHING_HOME", home):
                    with self.assertRaisesRegex(RuntimeError, "Refusing"):
                        _validate_state_paths()

    def test_stop_failure_and_transitional_states_refuse_reconfiguration(self) -> None:
        for result, state, loaded, allowed in ((0, "active", "loaded", False), (0, "deactivating", "loaded", False), (1, "inactive", "loaded", False), (1, "inactive", "not-found", True), (0, "inactive", "loaded", True)):
            with self.subTest(result=result, state=state), patch("sync.syncthing_steps.run", side_effect=[
                SimpleNamespace(returncode=result),
                SimpleNamespace(returncode=0 if state == "active" else 3, stdout=state),
                SimpleNamespace(returncode=0, stdout=loaded),
            ]):
                if allowed:
                    _stop_before_configuration()
                else:
                    with self.assertRaises(RuntimeError):
                        _stop_before_configuration()

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
                patch("sync.syncthing_steps._validate_syncthing_root"),
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
            patch("sync.syncthing_steps._assert_no_syncthing_recovery"),
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps._remove_syncthing_https") as remove_https,
            patch("sync.syncthing_steps.os.path.lexists", return_value=True),
            patch("sync.syncthing_steps.os.path.islink", return_value=False),
            patch("sync.syncthing_steps.os.path.isfile", return_value=True),
            patch("sync.syncthing_steps.os.unlink") as unlink,
            patch("sync.syncthing_steps.run") as run_command,
        ):
            run_command.side_effect = [
                SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=3, stdout="inactive\n"),
                SimpleNamespace(returncode=1, stdout="disabled\n"),
                SimpleNamespace(returncode=0),
            ]
            setup_syncthing(config)

        remove_https.assert_called_once_with(config)
        unlink.assert_called_once()
        commands = [call.args[0] for call in run_command.call_args_list]
        self.assertIn(
            ["systemctl", "disable", "--now", "basaltwater-syncthing.service"],
            commands,
        )
        self.assertIn(
            ["systemctl", "is-active", "basaltwater-syncthing.service"],
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
            patch("sync.syncthing_steps._assert_no_syncthing_recovery"),
            patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
            patch("sync.syncthing_steps.is_dry_run", return_value=False),
            patch("sync.syncthing_steps._remove_syncthing_https"),
            patch("sync.syncthing_steps.os.path.lexists", return_value=True),
            patch("sync.syncthing_steps.os.path.islink", return_value=False),
            patch("sync.syncthing_steps.os.path.isfile", return_value=True),
            patch("sync.syncthing_steps.os.unlink") as unlink,
            patch("sync.syncthing_steps.run", return_value=completed),
        ):
            with self.assertRaisesRegex(RuntimeError, "stopped and disabled"):
                setup_syncthing(config)
        unlink.assert_not_called()

    def test_disable_preserves_unit_for_errors_transitions_and_enabled_states(self) -> None:
        # Exit codes alone are not enough: bus errors and transitional states
        # must never be interpreted as a stopped daemon.
        for disable_rc, active_rc, state, enabled_rc, enabled in (
            (1, 3, "inactive", 1, "disabled"),
            (0, 1, "", 1, "disabled"),
            (0, 1, "inactive", 1, "disabled"),
            (0, 3, "deactivating", 1, "disabled"),
            (0, 3, "activating", 1, "disabled"),
            (0, 4, "unknown", 4, "not-found"),
            (0, 3, "inactive", 0, "enabled"),
            (0, 3, "failed", 1, ""),
        ):
            with (
                self.subTest(disable_rc=disable_rc, state=state, enabled=enabled),
                patch("sync.syncthing_steps._assert_no_syncthing_recovery"),
                patch("sync.syncthing_steps.can_manage_system_services", return_value=True),
                patch("sync.syncthing_steps.is_dry_run", return_value=False),
                patch("sync.syncthing_steps._remove_syncthing_https"),
                patch("sync.syncthing_steps.os.path.lexists", return_value=True),
                patch("sync.syncthing_steps.os.path.islink", return_value=False),
                patch("sync.syncthing_steps.os.path.isfile", return_value=True),
                patch("sync.syncthing_steps.os.unlink") as unlink,
                patch("sync.syncthing_steps.run", side_effect=[
                    SimpleNamespace(returncode=disable_rc),
                    SimpleNamespace(returncode=active_rc, stdout=state),
                    SimpleNamespace(returncode=enabled_rc, stdout=enabled),
                ]) as run_command,
            ):
                with self.assertRaisesRegex(RuntimeError, "unit preserved"):
                    setup_syncthing(_config(enable_syncthing=False, disable_syncthing=True))
                unlink.assert_not_called()
                self.assertNotIn(["systemctl", "daemon-reload"], [call.args[0] for call in run_command.call_args_list])

    def test_disable_allows_failed_terminal_state_and_confirmed_absent_rerun(self) -> None:
        with patch("sync.syncthing_steps.run", side_effect=[
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=3, stdout="failed"),
            SimpleNamespace(returncode=1, stdout="disabled"),
        ]):
            _disable_before_removal(True)
        for active_rc, state, load_rc, load_state, allowed in (
            (4, "unknown", 0, "not-found", True),
            (3, "inactive", 0, "not-found", True),
            (3, "deactivating", 0, "not-found", False),
            (4, "unknown", 1, "", False),
            (4, "unknown", 0, "loaded", False),
        ):
            with self.subTest(state=state, load_state=load_state), patch("sync.syncthing_steps.run", side_effect=[
                SimpleNamespace(returncode=1),
                SimpleNamespace(returncode=active_rc, stdout=state),
                SimpleNamespace(returncode=4, stdout="not-found"),
                SimpleNamespace(returncode=load_rc, stdout=load_state),
            ]):
                if allowed:
                    _disable_before_removal(False)
                else:
                    with self.assertRaisesRegex(RuntimeError, "unit preserved"):
                        _disable_before_removal(False)

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


class SyncthingReconciliationTest(unittest.TestCase):
    """Real private file transactions; all service/CLI/HTTP effects are mocked."""

    old_config = b"<configuration>old login and GUI shares</configuration>\r\n"
    old_unit = "[Service]\nDescription=prior managed endpoint\n"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = temporary.name
        self.home = os.path.join(self.parent, "syncthing")
        self.unit_dir = os.path.join(self.parent, "units")
        self.unit = os.path.join(self.unit_dir, "basaltwater-syncthing.service")
        self.xml = os.path.join(self.home, "config.xml")
        self.marker = os.path.join(self.parent, ".syncthing-operation.json")
        os.mkdir(self.home, 0o700)
        os.mkdir(self.unit_dir, 0o700)
        write_bytes_atomic(self.xml, self.old_config, mode=0o640)
        write_text_atomic(self.unit, self.old_unit, mode=0o640)
        self.original_info = os.stat(self.xml)
        self.identity = {"cert.pem": b"original cert", "key.pem": b"original key", "index-v0.14.0.db/data": b"database"}
        for relative, content in self.identity.items():
            write_bytes_atomic(os.path.join(self.home, relative), content)
        self.active = True
        self.enabled = "enabled"
        self.failure = ""
        self.failed = False
        self.recovery_failure = ""
        self.waits = 0
        self.commands: list[list[str]] = []
        for name, value in (("SYNCTHING_HOME", self.home), ("SYNCTHING_SERVICE_FILE", self.unit)):
            self.enterContext(patch(f"sync.syncthing_steps.{name}", value))
        for name in ("_prepare_state_home", "_preflight_existing_folders", "_prepare_share_root"):
            self.enterContext(patch(f"sync.syncthing_steps.{name}"))
        self.enterContext(patch("sync.syncthing_steps.can_manage_system_services", return_value=True))
        self.enterContext(patch("sync.syncthing_steps.is_dry_run", return_value=False))
        self.enterContext(patch("sync.syncthing_steps.is_package_installed", return_value=True))
        self.enterContext(patch("sync.syncthing_steps._account", return_value=(os.getuid(), os.getgid(), "agent")))
        self.enterContext(patch("sync.syncthing_steps.run", side_effect=self.command))
        self.enterContext(patch("lib.unit_transaction.run", side_effect=self.command))
        self.generator = self.enterContext(patch("sync.syncthing_steps._run_as_user", side_effect=self.generate))
        self.enterContext(patch("sync.syncthing_steps.write_text_atomic", side_effect=self.write_unit))
        self.enterContext(patch("sync.syncthing_steps.write_bytes_atomic", side_effect=self.write_config))
        self.enterContext(patch("sync.syncthing_steps._wait_for_api", side_effect=self.wait_for_api))
        self.enterContext(patch("sync.syncthing_steps._load_current_config", return_value=_current_config()))
        self.policy = self.enterContext(patch("sync.syncthing_steps._put_config", side_effect=self.put_config))
        self.https = self.enterContext(patch("sync.syncthing_steps._configure_syncthing_https", return_value=None))

    def fail(self, stage: str) -> None:
        if self.failure == stage and not self.failed:
            self.failed = True
            raise RuntimeError(f"{stage} failed")

    def command(self, args: list[str], **_kwargs: object) -> SimpleNamespace:
        self.commands.append(args)
        stdout = ""
        rc = 0
        exists = os.path.exists(self.unit)
        if args[0] == "systemd-analyze":
            self.fail("verify")
        elif args[:2] == ["systemctl", "show"]:
            load = "loaded" if exists else "not-found"
            if "--value" in args:
                stdout = load
            else:
                stdout = (f"LoadState={load}\nActiveState={'active' if self.active else 'inactive'}\n"
                          f"SubState={'running' if self.active else 'dead'}\n"
                          f"UnitFileState={self.enabled if exists else ''}\n")
        elif args[:2] == ["systemctl", "stop"]:
            if self.failed and self.recovery_failure == "stop":
                raise RuntimeError("rollback stop failed")
            self.active = False
            rc = 0 if exists else 1
            self.fail("stop")  # The daemon may stop before the command fails.
        elif args[:2] == ["systemctl", "is-active"]:
            stdout = "active" if self.active else ("inactive" if exists else "unknown")
            rc = 0 if self.active else (3 if exists else 4)
        elif args[:2] == ["systemctl", "disable"]:
            self.enabled = "disabled"
            if self.failed and self.recovery_failure == "disable":
                raise RuntimeError("rollback disable failed")
        elif args[:2] == ["systemctl", "enable"]:
            self.enabled = "enabled-runtime" if "--runtime" in args else "enabled"
            if "--now" in args:
                self.active = True
                if self.failure == "unverified-enable":
                    self.enabled = "disabled"
                self.fail("activation")
            elif self.failed and self.recovery_failure == "enable":
                raise RuntimeError("rollback enable failed")
        elif args[:2] == ["systemctl", "restart"]:
            self.active = True
            self.fail("restart")
        elif args[:2] == ["systemctl", "start"]:
            # Restoration must publish the prior login and unit before start.
            self.assertEqual(self.read(self.xml), self.old_config)
            self.assertEqual(self.read(self.unit).decode(), self.old_unit)
            if self.recovery_failure == "start":
                raise RuntimeError("rollback start failed")
            self.active = True
            if self.recovery_failure == "verification":
                self.enabled = "disabled"
        elif args[:2] == ["systemctl", "daemon-reload"]:
            self.fail("reload")
        else:
            raise AssertionError(f"Unmocked command: {args}")
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    def generate(self, _username: str, args: list[str], **_kwargs: object) -> SimpleNamespace:
        if args[0] == "generate":
            self.assertFalse(self.active)
            for name in ("cert.pem", "key.pem"):
                path = os.path.join(self.home, name)
                if not os.path.exists(path):
                    write_bytes_atomic(path, b"first-install " + name.encode())
            write_bytes_atomic(self.xml, b"rotated login", mode=0o600)
            self.fail("generation")  # Models a partial credential mutation.
        else:
            self.fail("device-id")
        return SimpleNamespace(returncode=0, stdout=LOCAL_DEVICE_ID + "\n", stderr="")

    def write_unit(self, path: str, content: str, **kwargs: int) -> None:
        if content == self.old_unit and self.recovery_failure == "unit":
            raise OSError("unit restore failed")
        write_text_atomic(path, content, **kwargs)
        if path == self.unit and content != self.old_unit:
            self.fail("unit")  # Atomic publication may succeed before fsync fails.

    def write_config(self, path: str, content: bytes, **kwargs: int) -> None:
        if path == self.xml and content == self.old_config and self.recovery_failure == "config":
            raise OSError("config restore failed")
        write_bytes_atomic(path, content, **kwargs)

    def wait_for_api(self, _username: str) -> None:
        self.waits += 1
        if self.failed and self.recovery_failure == "health":
            raise RuntimeError("restored API unavailable")
        self.fail("initial-health" if self.waits == 1 else "final-health")

    def put_config(self, _desired: dict) -> None:
        write_bytes_atomic(self.xml, b"new policy and login")
        self.fail("policy")  # A failed HTTP response can follow a committed PUT.

    @staticmethod
    def read(path: str) -> bytes:
        with open(path, "rb") as stream:
            return stream.read()

    def assert_identity_retained(self) -> None:
        for relative, content in self.identity.items():
            self.assertEqual(self.read(os.path.join(self.home, relative)), content)

    def assert_no_recovery(self) -> None:
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(any(name.startswith(".syncthing-recovery-") for name in os.listdir(self.parent)))

    def test_success_rotates_login_from_stdin_preserves_gui_shares_and_cleans_snapshots(self) -> None:
        setup_syncthing(_config())
        generate_call = self.generator.call_args_list[0]
        self.assertIn("--gui-password=-", generate_call.args[1])
        self.assertEqual(generate_call.kwargs["input_data"], "correct horse battery staple\n")
        self.assertNotIn("correct horse battery staple", str(generate_call.args))
        desired = self.policy.call_args.args[0]
        self.assertEqual(desired["folders"], _current_config()["folders"])
        self.assertEqual(desired["devices"], _current_config()["devices"])
        self.assertTrue(self.active)
        self.assertEqual(self.enabled, "enabled")
        self.assertEqual(self.read(self.xml), b"new policy and login")
        self.assert_identity_retained()
        self.assert_no_recovery()

    def test_failures_restore_prior_login_unit_metadata_activation_and_identity(self) -> None:
        for stage in ("stop", "generation", "unit", "verify", "reload", "activation", "initial-health", "device-id", "policy", "restart", "final-health"):
            with self.subTest(stage=stage):
                self.failure, self.failed, self.waits = stage, False, 0
                with self.assertRaisesRegex(RuntimeError, f"{stage} failed"):
                    setup_syncthing(_config())
                self.assertEqual(self.read(self.xml), self.old_config)
                info = os.stat(self.xml)
                self.assertEqual((info.st_mode, info.st_uid, info.st_gid),
                                 (self.original_info.st_mode, self.original_info.st_uid, self.original_info.st_gid))
                self.assertEqual(self.read(self.unit).decode(), self.old_unit)
                self.assertEqual(os.stat(self.unit).st_mode & 0o777, 0o640)
                self.assertTrue(self.active)
                self.assertEqual(self.enabled, "enabled")
                self.assert_identity_retained()
                self.assert_no_recovery()
                self.https.assert_not_called()

    def test_unsafe_post_start_policy_restores_existing_endpoint(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outside the configured"):
            setup_syncthing(_config(syncthing_root="/mnt/team-files"))
        self.policy.assert_not_called()
        self.assertEqual(self.read(self.xml), self.old_config)
        self.assertTrue(self.active)
        self.assertEqual(self.enabled, "enabled")
        self.assert_no_recovery()

    def test_successful_commands_do_not_hide_unverified_enablement(self) -> None:
        self.failure = "unverified-enable"
        with self.assertRaisesRegex(RuntimeError, "not active and enabled"):
            setup_syncthing(_config())
        self.assertEqual(self.read(self.xml), self.old_config)
        self.assertTrue(self.active)
        self.assertEqual(self.enabled, "enabled")
        self.assert_no_recovery()

    def test_snapshot_includes_configuration_flushed_by_stop(self) -> None:
        self.old_config = b"configuration flushed by daemon during shutdown\r\n"
        self.failure = "generation"

        def flush_on_stop() -> None:
            _stop_before_configuration()
            write_bytes_atomic(self.xml, self.old_config, mode=0o640)

        with patch("sync.syncthing_steps._stop_before_configuration", side_effect=flush_on_stop):
            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                setup_syncthing(_config())
        self.assertEqual(self.read(self.xml), self.old_config)
        self.assertTrue(self.active)
        self.assert_no_recovery()

    def test_restore_preserves_independent_activation_and_enablement(self) -> None:
        for active, enabled in ((True, "disabled"), (True, "enabled-runtime"), (False, "enabled"), (False, "disabled")):
            with self.subTest(active=active, enabled=enabled):
                self.active, self.enabled = active, enabled
                self.failure, self.failed = "policy", False
                with self.assertRaisesRegex(RuntimeError, "policy failed"):
                    setup_syncthing(_config())
                self.assertEqual(self.active, active)
                self.assertEqual(self.enabled, enabled)
                self.assertEqual(self.read(self.xml), self.old_config)
                self.assert_no_recovery()

    def test_rollback_failures_retain_private_snapshots_and_block_retry(self) -> None:
        for failure in ("stop", "disable", "config", "unit", "enable", "start", "health", "verification"):
            with self.subTest(failure=failure):
                self.active, self.enabled, self.failed = True, "enabled", False
                self.failure, self.recovery_failure = "policy", failure
                write_bytes_atomic(self.xml, self.old_config, mode=0o640)
                write_text_atomic(self.unit, self.old_unit, mode=0o640)
                with self.assertRaisesRegex(RuntimeError, "rollback needs recovery"):
                    setup_syncthing(_config())
                record = json.loads(self.read(self.marker))
                self.assertEqual(record["status"], "recovery_required")
                backup = record["context"]["backup_dir"]
                self.assertEqual(os.stat(backup).st_mode & 0o777, 0o700)
                self.assertEqual(self.read(os.path.join(backup, "config.xml")), self.old_config)
                self.assertEqual(set(os.listdir(backup)), {"config.xml", "config-metadata.json", "previous.json"})
                for name in os.listdir(backup):
                    self.assertEqual(os.stat(os.path.join(backup, name)).st_mode & 0o777, 0o600)
                self.assertNotIn("correct horse battery staple", self.read(self.marker).decode())
                before = len(self.commands)
                with self.assertRaisesRegex(ValueError, "Unfinished syncthing-reconciliation"):
                    setup_syncthing(_config())
                with self.assertRaisesRegex(ValueError, "Unfinished syncthing-reconciliation"):
                    setup_syncthing(_config(enable_syncthing=False, disable_syncthing=True))
                self.assertEqual(len(self.commands), before)
                self.assert_identity_retained()
                # Explicit fixture reset models operator recovery between cases;
                # application code must never bypass this guard automatically.
                os.unlink(self.marker)

    def test_failed_first_install_is_stopped_disabled_and_retains_generated_identity(self) -> None:
        os.unlink(os.path.join(self.home, "index-v0.14.0.db/data"))
        os.rmdir(os.path.join(self.home, "index-v0.14.0.db"))
        for stage in ("generation", "unit", "activation", "policy", "final-health"):
            with self.subTest(stage=stage):
                for name in ("config.xml", "cert.pem", "key.pem"):
                    os.unlink(os.path.join(self.home, name))
                if os.path.exists(self.unit):
                    os.unlink(self.unit)
                self.active, self.enabled, self.failed, self.waits = False, "disabled", False, 0
                self.failure = stage
                with self.assertRaisesRegex(RuntimeError, f"{stage} failed"):
                    setup_syncthing(_config())
                self.assertFalse(self.active)
                self.assertEqual(self.enabled, "disabled")
                self.assertFalse(os.path.exists(self.unit))
                for name in ("cert.pem", "key.pem"):
                    self.assertEqual(self.read(os.path.join(self.home, name)), b"first-install " + name.encode())
                self.assertTrue(os.path.exists(self.xml))
                self.assertNotIn(["systemctl", "start", "basaltwater-syncthing.service"], self.commands)
                self.assert_no_recovery()

    def test_incomplete_identity_refuses_silent_device_regeneration_before_stop(self) -> None:
        for retained in ("cert.pem", "key.pem", "config.xml", None):
            with self.subTest(retained=retained):
                for name in ("cert.pem", "key.pem", "config.xml"):
                    path = os.path.join(self.home, name)
                    if name == retained:
                        write_bytes_atomic(path, b"existing identity fragment")
                    elif os.path.exists(path):
                        os.unlink(path)
                with self.assertRaisesRegex(RuntimeError, "Incomplete Syncthing identity"):
                    setup_syncthing(_config())
        self.generator.assert_not_called()
        self.assertEqual(self.commands, [])
        self.assertTrue(self.active)

    def test_missing_prior_config_never_reenables_an_unverified_stale_unit(self) -> None:
        os.unlink(self.xml)
        self.active = False
        self.failure = "generation"
        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            setup_syncthing(_config())
        self.assertFalse(self.active)
        self.assertEqual(self.enabled, "disabled")
        self.assertEqual(self.read(self.unit).decode(), self.old_unit)
        self.assertEqual(self.read(self.xml), b"rotated login")
        self.assert_identity_retained()
        self.assert_no_recovery()


if __name__ == "__main__":
    unittest.main()
