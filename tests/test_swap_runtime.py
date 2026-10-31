"""Failure-recovery and ownership tests for managed swap reconciliation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

from common.swap_steps import (
    SWAP_SCHEMA_VERSION,
    _active_swap_inventory,
    _ensure_swap_device,
    _load_state,
    _ownership_union,
    _reconcile_zram,
    _remove_managed_zswap,
    _configure_zswap,
    _write_state,
    _zram_configuration,
    _zram_device_busy,
    configure_swap,
)
from lib.config import SetupConfig
from lib.remote_utils import CommandExecutionError
from lib.swap_config import SwapDevice, SwapFile, SwapZram


class TestSwapRuntimeSafety(unittest.TestCase):
    def _config(self, **overrides) -> SetupConfig:
        values = {
            "host": "vm",
            "username": "agent",
            "system_type": "agent_code_vm",
            "machine_type": "vm",
        }
        values.update(overrides)
        return SetupConfig(**values)

    @patch("common.swap_steps.run")
    def test_swap_inventory_raw_output_handles_paths_and_priorities(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/swap path/file 20\n/dev/vdb 0\n",
        )

        self.assertEqual(
            _active_swap_inventory(),
            {"/swap path/file": 20, "/dev/vdb": 0},
        )
        self.assertNotIn("--json", mock_run.call_args.args[0])

    def test_named_disk_activation_then_native_active_swap_rerun(self):
        record = {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "fstype": None,
            "mountpoints": [None],
            "pkname": None,
            "serial": "bw-swap-fast",
        }
        config = self._config(
            container_storage=[["swap-fast", "local-lvm", "8G"]],
            swap_devices=[["fast", "swap-fast", "priority=200", "discard=once"]],
        )
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name, value in {
                "SWAP_STATE_FILE": os.path.join(directory, "swap.json"),
                "FSTAB_PATH": os.path.join(directory, "fstab"),
            }.items():
                stack.enter_context(patch(f"common.swap_steps.{name}", value))
            for name, value in {
                "is_dry_run": False,
                "can_manage_swap": True,
                "_find_declared_disk": record,
                "_swap_type": None,
                "_swap_uuid": "11111111-1111",
                "_reconcile_zram": [],
                "_configure_swappiness": None,
                "_configure_zswap": None,
                "_configure_resume": None,
            }.items():
                stack.enter_context(
                    patch(f"common.swap_steps.{name}", return_value=value)
                )
            stack.enter_context(
                patch("common.swap_steps._wipefs_signatures", side_effect=[[], ["swap"]])
            )
            stack.enter_context(
                patch(
                    "common.swap_steps._active_swap_inventory",
                    side_effect=[{}, {}, {"/dev/sda": 200}, {"/dev/sda": 200}],
                )
            )
            mock_run = stack.enter_context(patch("common.swap_steps.run"))

            configure_swap(config)
            initial_state = _load_state()
            with open(os.path.join(directory, "fstab"), encoding="utf-8") as stream:
                initial_fstab = stream.read()
            self.assertEqual(
                mock_run.call_args_list,
                [
                    call("mkswap --label bw-fast /dev/sda"),
                    call("udevadm settle"),
                    call("swapon --priority 200 --discard=once /dev/sda"),
                ],
            )
            self.assertEqual(initial_state["areas"][0]["serial"], "bw-swap-fast")
            self.assertIn("UUID=11111111-1111", initial_fstab)
            record.update(fstype="swap", mountpoints=["[SWAP]"])
            mock_run.reset_mock()

            # Auto reruns recover the named disk and policy from persisted ownership.
            configure_swap(self._config())

            mock_run.assert_not_called()
            self.assertEqual(_load_state(), initial_state)
            with open(os.path.join(directory, "fstab"), encoding="utf-8") as stream:
                self.assertEqual(stream.read(), initial_fstab)

    @patch("common.swap_steps.run")
    @patch("common.swap_steps._active_swap_inventory", return_value={"/dev/sda": 200})
    @patch("common.swap_steps._swap_uuid", return_value="11111111-1111")
    @patch("common.swap_steps._wipefs_signatures", return_value=["swap"])
    @patch("common.swap_steps._find_device_by_path")
    @patch("common.swap_steps._resolve_direct_device", return_value="/dev/sda")
    def test_active_direct_swap_lsblk_forms_and_policy_reconciliation(
        self, _resolve, mock_find, _signatures, _uuid, mock_inventory, mock_run
    ):
        area = SwapDevice("fast", "UUID=11111111-1111", 200, "once")
        for discard, option, active_name, fields in (
            ("off", "", "/dev/sda", {"mountpoint": "[SWAP]"}),
            ("once", " --discard=once", "/dev/sda", {"mountpoints": ["[SWAP]"]}),
            ("pages", " --discard=pages", "/dev/sda", {"mountpoints": "[SWAP]"}),
            (
                "both", " --discard", "/dev/disk/by-uuid/11111111-1111",
                {"mountpoints": [None, ""], "mountpoint": "[SWAP]"},
            ),
        ):
            with self.subTest(discard=discard, fields=fields):
                adopted = SwapDevice("fast", area.source, 200, discard)
                mock_inventory.return_value = {active_name: 200}
                mock_find.return_value = {"path": "/dev/sda", "fstype": "swap", **fields}
                prior = _ensure_swap_device(self._config(), adopted, None)
                self.assertFalse(prior["provider_owned"])
                self.assertEqual(prior["uuid"], "11111111-1111")
                self.assertEqual(prior["discard"], discard)
                self.assertEqual(
                    mock_run.call_args_list,
                    [
                        call("swapoff /dev/sda"),
                        call(f"swapon --priority 200{option} /dev/sda"),
                    ],
                )
                mock_run.reset_mock()
                self.assertEqual(_ensure_swap_device(self._config(), adopted, prior), prior)
                # Recovering the same owned device through its identity is idle too.
                _ensure_swap_device(self._config(), adopted, None, [prior])
                mock_run.assert_not_called()

        for observed_priority, prior_discard in ((10, "once"), (200, "off")):
            with self.subTest(priority=observed_priority, discard=prior_discard):
                mock_inventory.return_value = {"/dev/sda": observed_priority}
                result = _ensure_swap_device(
                    self._config(), area, {**prior, "discard": prior_discard}
                )
                self.assertEqual(result["priority"], 200)
                self.assertEqual(result["discard"], "once")
                self.assertEqual(
                    mock_run.call_args_list,
                    [
                        call("swapoff /dev/sda"),
                        call("swapon --priority 200 --discard=once /dev/sda"),
                    ],
                )
                mock_run.reset_mock()

    @patch("common.swap_steps._configure_resume")
    @patch("common.swap_steps._configure_zswap")
    @patch("common.swap_steps._configure_swappiness")
    @patch("common.swap_steps._reconcile_zram", return_value=[])
    @patch("common.swap_steps._active_swap_inventory", return_value={"/dev/sda": 200})
    @patch("common.swap_steps._swap_uuid", return_value="11111111-1111")
    @patch("common.swap_steps._wipefs_signatures", return_value=["swap"])
    @patch(
        "common.swap_steps._find_device_by_path",
        return_value={"path": "/dev/sda", "fstype": "swap", "mountpoints": ["[SWAP]"]},
    )
    @patch("common.swap_steps._resolve_direct_device", return_value="/dev/sda")
    @patch("common.swap_steps.can_manage_swap", return_value=True)
    @patch("common.swap_steps.is_dry_run", return_value=False)
    def test_failed_first_active_adoption_does_not_record_requested_policy(self, *_mocks):
        config = self._config(
            swap_devices=[["fast", "UUID=11111111-1111", "priority=200", "discard=once"]]
        )
        for failure, commands in (
            ([RuntimeError("swapoff failed")], [call("swapoff /dev/sda")]),
            (
                [None, RuntimeError("swapon failed"), None],
                [
                    call("swapoff /dev/sda"),
                    call("swapon --priority 200 --discard=once /dev/sda"),
                    call("swapon --priority 200 /dev/sda"),
                ],
            ),
        ):
            with (
                self.subTest(failure=commands[-1]),
                tempfile.TemporaryDirectory() as directory,
                patch("common.swap_steps.SWAP_STATE_FILE", os.path.join(directory, "swap.json")),
                patch("common.swap_steps._write_state") as write_state,
                patch("common.swap_steps._replace_fstab") as replace_fstab,
                patch("common.swap_steps.run", side_effect=failure) as mock_run,
            ):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    configure_swap(config)
                self.assertEqual(mock_run.call_args_list, commands)
                write_state.assert_not_called()
                replace_fstab.assert_not_called()

    @patch("common.swap_steps.run")
    @patch("common.swap_steps._wipefs_signatures")
    @patch("common.swap_steps._find_device_by_path")
    @patch("common.swap_steps._resolve_direct_device", return_value="/dev/sda")
    def test_swap_marker_does_not_hide_filesystem_mounts_or_children(
        self, _resolve, mock_find, mock_signatures, mock_run
    ):
        for fields in (
            {"mountpoint": "/srv/data"},
            {"mountpoints": "/srv/data"},
            {"mountpoints": ["[SWAP]", "/srv/data"]},
            {"mountpoints": ["[SWAP]"], "mountpoint": "/srv/data"},
            {"mountpoints": [None], "mountpoint": "/srv/data"},
            {"mountpoints": ["[SWAP]"], "children": [{"name": "sda1"}]},
        ):
            with self.subTest(fields=fields):
                mock_find.return_value = {"path": "/dev/sda", "fstype": "swap", **fields}
                with self.assertRaisesRegex(RuntimeError, "partitioned or mounted"):
                    _ensure_swap_device(
                        self._config(), SwapDevice("fast", "UUID=11111111-1111"), None
                    )
        mock_signatures.assert_not_called()
        mock_run.assert_not_called()

    def test_legacy_provider_state_is_accepted_for_upgrade(self):
        state = {
            "schema": SWAP_SCHEMA_VERSION,
            "areas": [
                {
                    "name": "fast",
                    "type": "device",
                    "source": "swap-fast",
                    "path": "/dev/vdb",
                    "uuid": "11111111-1111",
                    "priority": 200,
                    "discard": "off",
                    "provider_owned": True,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "swap.json")
            with open(path, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file)
            with patch("common.swap_steps.SWAP_STATE_FILE", path):
                self.assertEqual(_load_state()["areas"], state["areas"])

    def test_state_loader_rejects_duplicate_device_identity(self):
        state = {
            "schema": SWAP_SCHEMA_VERSION,
            "areas": [
                {
                    "name": "fast",
                    "type": "device",
                    "source": "/dev/disk/by-id/one",
                    "path": "/dev/vdb",
                    "uuid": "11111111-1111",
                    "priority": 200,
                    "discard": "off",
                    "provider_owned": False,
                },
                {
                    "name": "duplicate",
                    "type": "device",
                    "source": "/dev/disk/by-id/two",
                    "path": "/dev/vdb",
                    "uuid": "22222222-2222",
                    "priority": 100,
                    "discard": "off",
                    "provider_owned": False,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "swap.json")
            with open(path, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file)
            with patch("common.swap_steps.SWAP_STATE_FILE", path):
                with self.assertRaisesRegex(RuntimeError, "duplicate resource"):
                    _load_state()

    @patch("common.swap_steps.write_json_atomic")
    def test_state_writer_refuses_duplicate_resolved_devices(self, mock_write):
        first = {
            "name": "one",
            "type": "device",
            "source": "/dev/disk/by-id/one",
            "path": "/dev/vdb",
            "uuid": "11111111-1111",
            "priority": 100,
            "discard": "off",
            "provider_owned": False,
        }
        second = {
            **first,
            "name": "two",
            "source": "UUID=11111111-1111",
        }

        with self.assertRaisesRegex(RuntimeError, "duplicate resource"):
            _write_state([first, second])
        mock_write.assert_not_called()

    @patch("common.swap_steps._active_swap_inventory")
    @patch("common.swap_steps._swap_uuid", return_value="11111111-1111")
    @patch("common.swap_steps._wipefs_signatures", return_value=["swap"])
    @patch(
        "common.swap_steps._find_device_by_path",
        return_value={"path": "/dev/vdb", "children": [], "fstype": "swap"},
    )
    @patch("common.swap_steps._resolve_direct_device", return_value="/dev/vdb")
    def test_device_alias_collision_stops_before_swap_changes(
        self,
        _mock_resolve,
        _mock_find,
        _mock_signatures,
        _mock_uuid,
        mock_inventory,
    ):
        claimed = {
            "name": "one",
            "type": "device",
            "source": "/dev/disk/by-id/one",
            "path": "/dev/vdb",
            "uuid": "11111111-1111",
            "priority": 100,
            "discard": "off",
            "provider_owned": False,
        }

        with self.assertRaisesRegex(RuntimeError, "same block device"):
            _ensure_swap_device(
                self._config(),
                SwapDevice("two", "UUID=11111111-1111"),
                None,
                [],
                [claimed],
            )
        mock_inventory.assert_not_called()

    def test_ownership_union_replaces_renamed_same_resource(self):
        old = {
            "name": "old",
            "type": "device",
            "source": "/dev/disk/by-id/stable",
            "path": "/dev/vdb",
            "uuid": "11111111-1111",
            "priority": 100,
        }
        new = {**old, "name": "new", "priority": 200}

        self.assertEqual(_ownership_union([new], [old]), [new])

    @patch("common.swap_steps._configure_resume")
    @patch("common.swap_steps._configure_zswap")
    @patch("common.swap_steps._configure_swappiness")
    @patch("common.swap_steps._replace_fstab")
    @patch("common.swap_steps._reconcile_zram", return_value=[])
    @patch("common.swap_steps._ensure_swap_file")
    @patch("common.swap_steps._write_state")
    @patch("common.swap_steps._active_swap", return_value=set())
    @patch("common.swap_steps._load_state")
    @patch("common.swap_steps.can_manage_swap", return_value=True)
    @patch("common.swap_steps.is_dry_run", return_value=False)
    def test_new_file_ownership_is_journaled_before_creation(
        self,
        _mock_dry_run,
        _mock_manage,
        mock_load,
        _mock_active,
        mock_write,
        mock_ensure,
        _mock_zram,
        _mock_fstab,
        _mock_swappiness,
        _mock_zswap,
        _mock_resume,
    ):
        mock_load.return_value = {"schema": SWAP_SCHEMA_VERSION, "areas": []}
        mock_ensure.return_value = {
            "name": "root",
            "type": "file",
            "source": "/managed-swap",
            "priority": 100,
            "size": "1G",
        }
        with patch("common.swap_steps.os.path.exists", return_value=False):
            configure_swap(
                self._config(swap_files=[["root", "/managed-swap", "1G"]])
            )

        first_state = mock_write.call_args_list[0].args[0]
        self.assertEqual(first_state[0]["source"], "/managed-swap")
        self.assertTrue(first_state[0]["pending"])
        mock_ensure.assert_called_once_with(
            SwapFile("root", "/managed-swap", "1G"),
            first_state[0],
        )

    @patch("common.swap_steps._configure_resume")
    @patch("common.swap_steps._configure_zswap")
    @patch("common.swap_steps._configure_swappiness")
    @patch("common.swap_steps._replace_fstab")
    @patch("common.swap_steps._reconcile_zram", return_value=[])
    @patch("common.swap_steps._remove_area")
    @patch("common.swap_steps._ensure_swap_device")
    @patch("common.swap_steps._write_state")
    @patch("common.swap_steps._active_swap", return_value=set())
    @patch("common.swap_steps._load_state")
    @patch("common.swap_steps.can_manage_swap", return_value=True)
    @patch("common.swap_steps.is_dry_run", return_value=False)
    def test_renamed_device_is_not_disabled_as_an_omitted_old_area(
        self,
        _mock_dry_run,
        _mock_manage,
        mock_load,
        _mock_active,
        _mock_write,
        mock_ensure,
        mock_remove,
        _mock_zram,
        _mock_fstab,
        _mock_swappiness,
        _mock_zswap,
        _mock_resume,
    ):
        source = "/dev/disk/by-id/scsi-stable"
        old = {
            "name": "old",
            "type": "device",
            "source": source,
            "path": "/dev/vdb",
            "uuid": "11111111-1111",
            "priority": 100,
            "discard": "off",
            "provider_owned": False,
        }
        new = {**old, "name": "new", "priority": 200}
        mock_load.return_value = {"schema": SWAP_SCHEMA_VERSION, "areas": [old]}
        mock_ensure.return_value = new
        config = self._config(swap_devices=[["new", source, "priority=200"]])

        configure_swap(config)

        mock_ensure.assert_called_once_with(
            config,
            SwapDevice("new", source, priority=200),
            old,
            [old],
            [],
        )
        mock_remove.assert_not_called()

    @patch("common.swap_steps._stop_zram_sources")
    @patch("common.swap_steps._zram_device_busy", return_value=False)
    @patch("common.swap_steps._active_swap_inventory")
    @patch("common.swap_steps.run")
    def test_unchanged_zram_restarts_a_missing_owned_unit(
        self, mock_run, mock_inventory, _mock_busy, _stop
    ):
        area = SwapZram("fast", "1G", 300, "auto")
        old = {
            "name": "fast",
            "type": "zram",
            "source": "/dev/zram0",
            "priority": 300,
            "size": "1G",
            "algorithm": "auto",
        }
        mock_inventory.side_effect = [{}, {"/dev/zram0": 300}]
        with patch(
            "common.swap_steps.Path.read_text",
            return_value=_zram_configuration([area]),
        ):
            self.assertEqual(_reconcile_zram([area], [old]), [old])

        self.assertIn(
            call("systemctl start dev-zram0.swap"),
            mock_run.call_args_list,
        )

    @patch("common.swap_steps._stop_zram_sources")
    @patch("common.swap_steps._zram_device_busy", return_value=False)
    @patch("common.swap_steps._active_swap_inventory")
    @patch("common.swap_steps.run")
    def test_unchanged_zram_repairs_live_priority_drift(
        self, mock_run, mock_inventory, _mock_busy, stop
    ):
        area = SwapZram("fast", "1G", 300, "auto")
        old = {
            "name": "fast",
            "type": "zram",
            "source": "/dev/zram0",
            "priority": 300,
            "size": "1G",
            "algorithm": "auto",
        }
        mock_inventory.side_effect = [
            {"/dev/zram0": 10},
            {"/dev/zram0": 300},
        ]
        with patch(
            "common.swap_steps.Path.read_text",
            return_value=_zram_configuration([area]),
        ):
            self.assertEqual(_reconcile_zram([area], [old]), [old])

        stop.assert_called_once_with(["/dev/zram0"])
        self.assertIn(
            call("systemctl start dev-zram0.swap"),
            mock_run.call_args_list,
        )

    @patch("common.swap_steps._zram_device_busy", return_value=True)
    @patch("common.swap_steps._active_swap_inventory", return_value={})
    def test_new_zram_refuses_an_unmanaged_busy_device(
        self, _mock_active, _mock_busy
    ):
        journal = MagicMock()
        with patch("common.swap_steps.Path.read_text", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "unmanaged zram"):
                _reconcile_zram(
                    [SwapZram("fast", "1G")],
                    [],
                    journal_ownership=journal,
                )
        journal.assert_not_called()


class TestCompressedSwapTeardown(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_path = root / 'zram.conf'
        self.state_path = root / 'swap.json'
        self.zswap_path = root / 'zswap.service'
        self.area = SwapZram('fast', '64M', 300)
        self.old = dict(name='fast', type='zram', source='/dev/zram0', priority=300, size='64M', algorithm='auto')
        self.config_path.write_text(_zram_configuration([self.area]))
        self.old_content = self.config_path.read_text()
        self.inventory = {'/dev/zram0': 300}
        self.allocated = {'/dev/zram0'}
        self.units = {'systemd-zram-setup@zram0.service': dict(LoadState='loaded', ActiveState='active', SubState='exited', UnitFileState='static')}
        self.events = []
        self.refuse = None
        self.uncooperative = False
        self.fail_start = False
        self.refuse_recovery = False
        self.failure = CommandExecutionError('systemctl', 1, 'Stop refused')
        self.start_failure = CommandExecutionError('systemctl start', 1, 'Activation failed')
        self.enterContext(patch('common.swap_steps.ZRAM_PATH', str(self.config_path)))
        self.enterContext(patch('common.swap_steps.SWAP_STATE_FILE', str(self.state_path)))
        self.enterContext(patch('common.swap_steps.ZSWAP_SERVICE_PATH', str(self.zswap_path)))
        self.enterContext(patch('common.swap_steps.FSTAB_PATH', str(root/'fstab')))
        self.enterContext(patch('common.swap_steps.can_manage_swap', return_value=True))
        self.enterContext(patch('common.swap_steps.is_dry_run', return_value=False))
        self.enterContext(patch('common.swap_steps._active_swap_inventory', side_effect=lambda: dict(self.inventory)))
        self.enterContext(patch('common.swap_steps._zram_device_busy', side_effect=lambda index, active: f'/dev/zram{index}' in self.allocated))
        self.enterContext(patch('common.swap_steps.inspect_unit_state', side_effect=self.inspect))
        self.enterContext(patch('common.swap_steps.run', side_effect=self.command))
        _write_state([self.old])

    def inspect(self, unit):
        return dict(self.units.get(unit, dict(LoadState='not-found', ActiveState='inactive', SubState='dead', UnitFileState='')))

    def command(self, command, **kwargs):
        argv = shlex.split(command) if isinstance(command, str) else command
        self.events.append((tuple(argv), self.config_path.read_text() if self.config_path.exists() else None))
        if argv[0] != 'systemctl' or argv[1] == 'daemon-reload':
            return SimpleNamespace(returncode=0)
        action, unit = argv[1], argv[-1]
        source = ('/dev/' + unit.split('@', 1)[1].removesuffix('.service') if '@' in unit
                  else '/dev/' + unit.removeprefix('dev-').removesuffix('.swap') if unit.endswith('.swap') else None)
        if action in {'stop', 'disable'}:
            if unit == self.refuse:
                if kwargs.get('check', True):
                    raise self.failure
                return SimpleNamespace(returncode=1)
            if not self.uncooperative:
                self.units[unit]['ActiveState'] = 'inactive'
                if action == 'disable':
                    self.units[unit]['UnitFileState'] = 'disabled'
                self.inventory.pop(source, None)
                self.allocated.discard(source)
        elif action == 'start':
            loader = f'systemd-zram-setup@{Path(source).name}.service'
            if self.fail_start:
                self.fail_start = False
                if self.refuse_recovery:
                    self.refuse = loader
                    self.units[loader]['ActiveState'] = 'active'
                raise self.start_failure
            priority = int(next(line.split(' = ')[1] for line in self.config_path.read_text().splitlines() if line.startswith('swap-priority')))
            self.units.setdefault(loader, dict(LoadState='loaded', UnitFileState='static', SubState='exited'))['ActiveState'] = 'active'
            self.inventory[source] = priority
            self.allocated.add(source)
        return SimpleNamespace(returncode=0)

    def test_refused_zram_removal_preserves_generator_and_ownership(self):
        self.refuse = 'systemd-zram-setup@zram0.service'
        for name in ('_configure_resume', '_configure_swappiness', '_configure_zswap'):
            self.enterContext(patch('common.swap_steps.'+name))
        with self.assertRaises(CommandExecutionError) as raised:
            configure_swap(SetupConfig(host='vm', username='agent', system_type='server_lite', swap_mode='none'))
        self.assertIs(raised.exception, self.failure)
        self.assertEqual(self.config_path.read_text(), self.old_content)
        self.assertEqual(_load_state()['areas'], [self.old])
        self.assertEqual(self.inventory, {'/dev/zram0': 300})
        self.assertFalse(any(event[0][1] == 'daemon-reload' for event in self.events))

    def test_successful_stop_with_live_zram_does_not_authorize_removal(self):
        self.uncooperative = True
        with self.assertRaisesRegex(RuntimeError, 'still active|still allocated'):
            _reconcile_zram([], [self.old])
        self.assertEqual(self.config_path.read_text(), self.old_content)
        self.assertEqual(_load_state()['areas'], [self.old])

    def test_reconfiguration_stops_before_publication_and_repeated_rerun_is_idle(self):
        changed = SwapZram('fast', '64M', 400)
        records = _reconcile_zram([changed], [self.old])
        stop = next(event for event in self.events if event[0][1] == 'stop')
        self.assertEqual(stop[1], self.old_content)
        self.assertEqual(self.inventory, {'/dev/zram0': 400})
        self.events.clear()
        self.assertEqual(_reconcile_zram([changed], records), records)
        self.assertEqual(self.events, [])

    def test_failed_activation_restores_previous_configuration_and_active_policy(self):
        self.fail_start = True
        with self.assertRaises(CommandExecutionError) as raised:
            _reconcile_zram([SwapZram('fast', '64M', 400)], [self.old])
        self.assertIs(raised.exception, self.start_failure)
        self.assertEqual(self.config_path.read_text(), self.old_content)
        self.assertEqual(self.inventory, {'/dev/zram0': 300})

    def test_failed_recovery_keeps_candidate_configuration_for_inspection(self):
        self.fail_start = self.refuse_recovery = True
        desired = [SwapZram('fast', '64M', 400)]
        with self.assertRaisesRegex(RuntimeError, 'zram recovery') as raised:
            _reconcile_zram(desired, [self.old])
        self.assertIs(raised.exception.__cause__, self.start_failure)
        self.assertEqual(self.config_path.read_text(), _zram_configuration(desired))
        self.assertEqual(_load_state()['areas'], [self.old])

    def test_explicitly_absent_sparse_owned_unit_does_not_stop_another_device(self):
        self.inventory.clear()
        self.allocated.clear()
        self.units.clear()
        self.assertEqual(_reconcile_zram([], [{**self.old, 'source':'/dev/zram3'}]), [])
        self.assertFalse(any(event[0][1] == 'stop' for event in self.events))
        self.assertEqual(self.config_path.read_text(), '[zram0]\nzram-size = 0\n\n[zram3]\nzram-size = 0\n')

    def test_removed_device_disablement_survives_new_configuration_and_package_hooks(self):
        self.config_path.write_text('[zram0]\nzram-size = 0\n\n[zram1]\nzram-size = 0\n')
        self.inventory.clear()
        self.allocated.clear()
        self.units['systemd-zram-setup@zram0.service']['ActiveState'] = 'inactive'
        records = _reconcile_zram([self.area], [])
        expected = _zram_configuration([self.area]) + '\n[zram1]\nzram-size = 0\n'
        self.assertEqual(self.config_path.read_text(), expected)
        install = next(event for event in self.events if event[0][0] == 'apt-get')
        self.assertEqual(install[1], expected)
        self.events.clear()
        self.assertEqual(_reconcile_zram([self.area], records), records)
        self.assertEqual(self.events, [])

    def test_all_unit_inspection_precedes_retirement(self):
        second = {**self.old, 'name':'second', 'source':'/dev/zram1'}
        def inspect(unit):
            if '@zram1.' in unit:
                raise RuntimeError('Cannot inspect second unit')
            return self.inspect(unit)
        with patch('common.swap_steps.inspect_unit_state', side_effect=inspect):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
                _reconcile_zram([], [self.old, second])
        self.assertEqual(self.events, [])
        self.assertEqual(self.config_path.read_text(), self.old_content)

    def test_failed_inventory_and_unreadable_device_are_not_inactivity(self):
        with patch('common.swap_steps.run', return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaisesRegex(RuntimeError, 'Could not inspect active swap'):
                _active_swap_inventory()
        for error in (PermissionError('unreadable'), ValueError('invalid size')):
            with self.subTest(error=error), patch('common.swap_steps.Path.read_text', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'Could not inspect zram device'):
                    _zram_device_busy(0, set())

    def test_zswap_refused_removal_retains_definition_for_both_clear_paths(self):
        unit = 'basaltwater-zswap.service'
        self.zswap_path.write_text('retained zswap policy\n')
        self.units[unit] = dict(LoadState='loaded', ActiveState='active', SubState='exited', UnitFileState='enabled')
        self.refuse = unit
        exists = os.path.exists
        with patch('common.swap_steps.os.path.exists', side_effect=lambda path: False if path == '/sys/module/zswap/parameters/enabled' else exists(path)):
            for clear in (lambda: _configure_zswap(False, None), _remove_managed_zswap):
                with self.subTest(clear=clear), self.assertRaises(CommandExecutionError):
                    clear()
                self.assertEqual(self.zswap_path.read_text(), 'retained zswap policy\n')

    def test_zswap_unlinked_loaded_unit_still_requires_checked_disable(self):
        unit = 'basaltwater-zswap.service'
        self.units[unit] = dict(LoadState='not-found', ActiveState='active', SubState='exited', UnitFileState='')
        self.refuse = unit
        with self.assertRaises(CommandExecutionError):
            _remove_managed_zswap()
        self.assertEqual(self.units[unit]['ActiveState'], 'active')

    def test_zswap_kernel_postcondition_failure_retains_service(self):
        unit = 'basaltwater-zswap.service'
        self.zswap_path.write_text('retained zswap policy\n')
        self.units[unit] = dict(LoadState='loaded', ActiveState='active', SubState='exited', UnitFileState='enabled')
        exists, read = os.path.exists, Path.read_text
        kernel_path = '/sys/module/zswap/parameters/enabled'
        with (
            patch('common.swap_steps.os.path.exists', side_effect=lambda path: True if path == kernel_path else exists(path)),
            patch('common.swap_steps.Path.read_text', autospec=True, side_effect=lambda path, **kwargs: 'Y' if str(path) == kernel_path else read(path, **kwargs)),
        ):
            with self.assertRaisesRegex(RuntimeError, 'zswap is still enabled'):
                _remove_managed_zswap()
        self.assertEqual(self.zswap_path.read_text(), 'retained zswap policy\n')
        self.assertEqual(self.units[unit]['ActiveState'], 'inactive')


if __name__ == "__main__":
    unittest.main()
