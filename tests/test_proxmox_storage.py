"""Tests for lib/proxmox_storage.py."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import PosixPath
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_storage import (
    OrphanedVolume,
    ProxmoxStorageError,
    _REFERENCE_SCRIPT,
    _parse_pvesm_list,
    delete_volume,
    list_orphaned_volumes,
)


def _host() -> ProxmoxHost:
    return ProxmoxHost(name="pve1", address="10.0.0.10")


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", stderr)


_PVESM_LIST_LOCAL_LVM = (
    "Volid                          Format  Type      Size VMID\n"
    "local-lvm:vm-100-disk-0        raw     images    10G  100\n"
    "local-lvm:vm-999-disk-0        raw     images    20G  999\n"  # orphan — no VMID 999
    "local-lvm:vm-100-disk-1        raw     rootdir    5G  100\n"
)

_QM_LIST = "VMID NAME STATUS\n100 myvm running\n"
_PCT_LIST = "VMID Status Name\n"   # no containers


class _Inventory:
    """Mock native observations independently of query ordering."""

    def __init__(self) -> None:
        self.responses = {
            'qm': _ok(_QM_LIST),
            'pct': _ok(_PCT_LIST),
            'cluster': _ok(json.dumps([{'node': 'pve1', 'type': 'qemu', 'vmid': 100}])),
            'node': _ok('"pve1"'),
            'pools': _ok(json.dumps([
                {'storage': 'local-lvm', 'active': 1, 'enabled': 1, 'shared': 0},
            ])),
            'replication': _ok('[]'),
            'volumes': _ok(_PVESM_LIST_LOCAL_LVM),
            'references': _ok(json.dumps({'node': 'pve1', 'guests': [
                {'node': 'pve1', 'type': 'qemu', 'vmid': 100, 'references': []},
            ]})),
        }
        self.commands: list[str] = []

    def json(self, key: str, value: object) -> None:
        self.responses[key] = _ok(json.dumps(value))

    def guests(self, *identities: tuple[str, str, int]) -> None:
        self.json('cluster', [dict(node=node, type=kind, vmid=vmid) for node, kind, vmid in identities])
        self.json('references', {'node': 'pve1', 'guests': [
            dict(node=node, type=kind, vmid=vmid, references=[]) for node, kind, vmid in identities
        ]})
        for kind, key in (('qemu', 'qm'), ('lxc', 'pct')):
            self.responses[key] = _ok('VMID NAME STATUS\n' + ''.join(
                f'{vmid} guest stopped\n' for node, guest_kind, vmid in identities
                if node == 'pve1' and kind == guest_kind
            ))

    def run(self, host, cmd, **kwargs):
        self.commands.append(cmd)
        if cmd == 'qm list':
            key = 'qm'
        elif cmd == 'pct list':
            key = 'pct'
        elif cmd.startswith('pvesh get /cluster/resources '):
            key = 'cluster'
        elif cmd == 'pvesh get /cluster/replication --output-format json':
            key = 'replication'
        elif cmd == 'pvesh get /nodes/pve1/storage --output-format json':
            key = 'pools'
        elif cmd == 'pvesm list local-lvm':
            key = 'volumes'
        elif cmd.startswith('python3 -c '):
            key = 'references' if shlex.split(cmd)[2] == _REFERENCE_SCRIPT else 'node'
        else:
            raise AssertionError(f'Unexpected command: {cmd}')
        return self.responses[key]


class TestParsePvesmList(unittest.TestCase):
    def test_parses_guest_disk_entries(self) -> None:
        entries = _parse_pvesm_list(_PVESM_LIST_LOCAL_LVM)
        self.assertEqual(len(entries), 3)
        volids = [e[0] for e in entries]
        self.assertIn("local-lvm:vm-100-disk-0", volids)
        self.assertIn("local-lvm:vm-999-disk-0", volids)

    def test_skips_non_guest_content(self) -> None:
        stdout = (
            "Volid                  Format  Type     Size VMID\n"
            "local:iso/debian.iso   iso     iso       1G  \n"
            "local-lvm:vm-100-disk-0 raw    images   10G  100\n"
        )
        entries = _parse_pvesm_list(stdout)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "local-lvm:vm-100-disk-0")

    def test_rejects_guest_volumes_without_vmid(self) -> None:
        stdout = (
            "Volid  Format Type Size VMID\n"
            "local-lvm:base-9000-disk-0 raw images 10G\n"
        )
        with self.assertRaises(ProxmoxStorageError):
            _parse_pvesm_list(stdout)

    def test_rejects_malformed_successful_volume_output(self) -> None:
        for output in ('', 'unexpected output', 'Volid Format Type Size VMID\ntruncated',
                       'Volid Format Type Size VMID\npool:disk raw images 10G -1'):
            with self.subTest(output=output), self.assertRaises(ProxmoxStorageError):
                _parse_pvesm_list(output)


class TestListOrphanedVolumes(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = _Inventory()
        runner = patch('lib.proxmox_storage._run', side_effect=self.inventory.run)
        self.runner = runner.start()
        self.addCleanup(runner.stop)

    def scan(self):
        return list_orphaned_volumes(_host())

    def test_empty_local_inventories_allow_orphan_scan(self) -> None:
        for output in ('', ' \n'):
            with self.subTest(output=output):
                self.inventory.guests()
                self.inventory.responses['qm'] = _ok(output)
                self.inventory.responses['pct'] = _ok(output)
                self.assertEqual({volume.vmid for volume in self.scan()}, {100, 999})

    def test_malformed_local_inventory_aborts_before_volume_scan(self) -> None:
        for output in ('unexpected output', 'VMID NAME STATUS\nnot-a-vmid guest running',
                       'VMID NAME STATUS\n0 guest stopped', 'VMID NAME STATUS\n100 guest stopped\n100 guest stopped'):
            with self.subTest(output=output):
                self.runner.reset_mock()
                self.inventory.responses['qm'] = _ok(output)
                with self.assertRaises(ProxmoxStorageError):
                    self.scan()
                self.assertEqual(self.runner.call_count, 1)

    def test_identifies_orphaned_volume(self) -> None:
        self.assertEqual([volume.vmid for volume in self.scan()], [999])

    def test_returns_empty_when_no_orphans(self) -> None:
        self.inventory.guests(('pve1', 'qemu', 100), ('pve1', 'lxc', 999))
        self.assertEqual(self.scan(), [])

    def test_referenced_disk_with_absent_owner_is_not_orphan(self) -> None:
        for kind in ('qemu', 'lxc'):
            with self.subTest(kind=kind):
                self.inventory.guests(('pve1', kind, 100))
                self.inventory.json('references', {'node': 'pve1', 'guests': [
                    {'node': 'pve1', 'type': kind, 'vmid': 100,
                     'references': ['local-lvm:vm-999-disk-0']},
                ]})
                self.assertEqual(self.scan(), [])

    def test_remote_owner_protects_shared_storage_only(self) -> None:
        self.inventory.guests(('pve2', 'qemu', 100), ('pve2', 'lxc', 999))
        for shared, expected in ((1, set()), (0, {100, 999})):
            with self.subTest(shared=shared):
                self.inventory.json('pools', [dict(storage='local-lvm', active=1, enabled=1, shared=shared)])
                self.assertEqual({volume.vmid for volume in self.scan()}, expected)

    def test_remote_reference_protects_shared_storage_only(self) -> None:
        self.inventory.guests(('pve2', 'qemu', 101))
        self.inventory.json('references', {'node': 'pve1', 'guests': [
            dict(node='pve2', type='qemu', vmid=101, references=['local-lvm:vm-999-disk-0']),
        ]})
        for shared, expected in ((1, {100}), (0, {100, 999})):
            with self.subTest(shared=shared):
                self.inventory.json('pools', [dict(storage='local-lvm', active=1, enabled=1, shared=shared)])
                self.assertEqual({volume.vmid for volume in self.scan()}, expected)

    def test_replication_protects_remote_and_absent_owners(self) -> None:
        self.inventory.guests(('pve2', 'qemu', 100))
        self.inventory.json('replication', [dict(guest=100, disable=1), dict(guest=999, remove_job='full')])
        self.assertEqual(self.scan(), [])

    def test_inactive_storage_is_not_listed(self) -> None:
        self.inventory.json('pools', [dict(storage='unmounted', active=0, enabled=1, shared=0)])
        self.assertEqual(self.scan(), [])
        self.assertFalse(any(cmd.startswith('pvesm list') for cmd in self.inventory.commands))

    def test_incomplete_inventory_never_returns_orphans(self) -> None:
        for key in self.inventory.responses:
            previous = self.inventory.responses[key]
            with self.subTest(key=key):
                self.inventory.responses[key] = _fail()
                with self.assertRaises(ProxmoxStorageError):
                    self.scan()
            self.inventory.responses[key] = previous

    def test_malformed_inventory_aborts(self) -> None:
        for key, observations in {
            'cluster': ['', '{}', '[{"vmid":0}]', '[{"vmid":100,"node":"pve1"}]'],
            'node': ['', 'null', '"invalid/node"'],
            'pools': ['', '{}', '[{"storage":"local-lvm","active":1,"enabled":1}]',
                      '[{"storage":"invalid/pool","active":1,"enabled":1,"shared":0}]'],
            'replication': ['', '{}', '[{"guest":"100"}]', '[{}]'],
            'references': ['', '{}', '{"node":"pve1","guests":[]}',
                           '{"node":"pve1","guests":[{"node":"pve1","type":"qemu","vmid":100}]}'],
            'volumes': ['', 'Volid Format Type Size VMID\nwrong:vm-999-disk-0 raw images 10G 999',
                        'Volid Format Type Size VMID\nlocal-lvm:vm-999-disk-0 raw images 10G 999\n'
                        'local-lvm:vm-999-disk-0 raw images 10G 999'],
        }.items():
            previous = self.inventory.responses[key]
            for output in observations:
                with self.subTest(key=key, output=output):
                    self.inventory.responses[key] = _ok(output)
                    with self.assertRaises(ProxmoxStorageError):
                        self.scan()
            self.inventory.responses[key] = previous

    def test_local_inventory_must_agree_with_configuration_identity(self) -> None:
        self.inventory.responses['qm'] = _ok('')
        with self.assertRaisesRegex(ProxmoxStorageError, 'incomplete guest configuration'):
            self.scan()

    def test_changed_node_identity_aborts(self) -> None:
        self.inventory.guests()
        self.inventory.json('references', {'node': 'pve2', 'guests': []})
        with self.assertRaisesRegex(ProxmoxStorageError, 'node identity changed'):
            self.scan()

    def test_fresh_scan_blocks_deleting_referenced_volume(self) -> None:
        self.assertEqual([volume.vmid for volume in self.scan()], [999])
        self.inventory.json('references', {'node': 'pve1', 'guests': [
            dict(node='pve1', type='qemu', vmid=100, references=['local-lvm:vm-999-disk-0']),
        ]})
        with self.assertRaisesRegex(ProxmoxStorageError, 'fresh orphan inventory'):
            delete_volume(_host(), 'local-lvm:vm-999-disk-0')
        self.assertFalse(any('pvesm free' in cmd for cmd in self.inventory.commands))


class TestConfigurationReferenceScript(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = PosixPath(temporary.name)
        for node in ('pve1', 'pve2'):
            for directory in ('qemu-server', 'lxc'):
                (self.root / 'nodes' / node / directory).mkdir(parents=True)
        (self.root / 'local').symlink_to(self.root / 'nodes' / 'pve1')
        self.volids = {f'pool:vm-999-disk-{index}' for index in range(5)}
        self.storage_inventory = [{'storage': name} for name in ('pool', 'alias', 'ceph')]

    def run_script(self):
        def path(value):
            return PosixPath(str(value).replace('/etc/pve/', str(self.root) + '/'))

        native_run = subprocess.run

        def run(args, **kwargs):
            if args == ['pvesh', 'get', '/storage', '--output-format', 'json']:
                return _ok(json.dumps(self.storage_inventory))
            return native_run(args, **kwargs)

        output = io.StringIO()
        with patch('pathlib.Path', side_effect=path), patch('subprocess.run', side_effect=run), patch.object(sys, 'argv', ['-c', json.dumps(sorted(self.volids))]), redirect_stdout(output):
            exec(compile(_REFERENCE_SCRIPT, '<reference inventory>', 'exec'), {})
        return json.loads(output.getvalue())

    @patch('subprocess.run')
    def test_all_sections_and_guest_types_are_examined(self, runner) -> None:
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text(
            'scsi0: pool:vm-999-disk-0,size=1G\n'
            'unused0: pool:vm-999-disk-1\n'
            '[PENDING]\nscsi1: pool:vm-999-disk-2\n'
            '[before]\nscsi0: pool:vm-999-disk-3\n'
        )
        (self.root / 'nodes/pve2/lxc/101.conf').write_text(
            'rootfs: pool:vm-999-disk-4,size=1G\nmp0: pool:vm-999-disk-0,mp=/data\n'
        )
        for index in range(5):
            (self.root / f'volume-{index}.raw').touch()
        runner.side_effect = lambda args, **kwargs: _ok(str(self.root / f'volume-{args[2][-1]}.raw'))
        data = self.run_script()
        self.assertEqual(data['node'], 'pve1')
        refs = {row['vmid']: set(row['references']) for row in data['guests']}
        self.assertEqual(refs[100], self.volids - {'pool:vm-999-disk-4'})
        self.assertEqual(refs[101], self.volids)

    @patch('subprocess.run')
    def test_storage_alias_blocks_initial_and_fresh_deletion_scans(self, runner) -> None:
        self.volids = {'pool:vm-999-disk-0', 'alias:vm-999-disk-0'}
        target = self.root / 'volume.raw'
        target.touch()
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('scsi0: alias:vm-999-disk-0,size=1G\n')
        runner.return_value = _ok(str(target))
        inventory = _Inventory()
        inventory.json('pools', [dict(storage=name, active=1, enabled=1, shared=0) for name in ('pool', 'alias')])
        commands = []

        def run(host, cmd, **kwargs):
            commands.append(cmd)
            if cmd.startswith('pvesm list '):
                storage = shlex.split(cmd)[2]
                return _ok(f'Volid Format Type Size VMID\n{storage}:vm-999-disk-0 raw images 1G 999\n')
            if cmd.startswith('python3 -c ') and shlex.split(cmd)[2] == _REFERENCE_SCRIPT:
                return _ok(json.dumps(self.run_script()))
            return inventory.run(host, cmd, **kwargs)

        with patch('lib.proxmox_storage._run', side_effect=run):
            self.assertEqual(list_orphaned_volumes(_host()), [])
            for volid in sorted(self.volids):
                with self.subTest(volid=volid), self.assertRaisesRegex(ProxmoxStorageError, 'fresh orphan inventory'):
                    delete_volume(_host(), volid)
        self.assertFalse(any(command.startswith('pvesm free ') for command in commands))

    @patch('subprocess.run')
    def test_reference_through_unlisted_alias_protects_backing_volume(self, runner) -> None:
        self.volids = {'pool:vm-999-disk-0'}
        target = self.root / 'volume.raw'
        target.touch()
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('[before]\nscsi0: alias:vm-999-disk-0\n')
        runner.return_value = _ok(str(target))
        self.assertEqual(self.run_script()['guests'][0]['references'], ['pool:vm-999-disk-0'])
        self.assertEqual({call.args[0][2] for call in runner.call_args_list},
                         {'pool:vm-999-disk-0', 'alias:vm-999-disk-0'})

    @patch('subprocess.run')
    def test_custom_qemu_arguments_block_initial_and_fresh_deletion_scans(self, runner) -> None:
        self.volids = {'local-lvm:vm-100-disk-0', 'local-lvm:vm-999-disk-0'}
        self.storage_inventory = [{'storage': 'local-lvm'}]
        target = self.root / 'volume.raw'
        target.touch()
        alias = self.root / 'disk aliases' / 'attached.raw'
        alias.parent.mkdir()
        alias.symlink_to(target)
        runner.return_value = _ok(str(target))
        inventory = _Inventory()
        commands = []

        def run(host, cmd, **kwargs):
            commands.append(cmd)
            if cmd.startswith('python3 -c ') and shlex.split(cmd)[2] == _REFERENCE_SCRIPT:
                return _ok(json.dumps(self.run_script()))
            return inventory.run(host, cmd, **kwargs)

        for text in (f'args: -drive "file={alias},format=raw,if=none,id=extra"\n',
                     f"args: -drive 'file={alias},format=raw,if=none,id=extra'\n",
                     'args: -blockdev {"driver":"rbd","pool":"extra","image":"disk"}\n',
                     f'name: guest\n[before]\nargs: -drive "file={alias},format=raw"\n'):
            with self.subTest(text=text), patch('lib.proxmox_storage._run', side_effect=run):
                (self.root / 'nodes/pve1/qemu-server/100.conf').write_text(text)
                self.assertEqual(list_orphaned_volumes(_host()), [])
                with self.assertRaisesRegex(ProxmoxStorageError, 'fresh orphan inventory'):
                    delete_volume(_host(), 'local-lvm:vm-999-disk-0')
        self.assertFalse(any(command.startswith('pvesm free ') for command in commands))

    @patch('subprocess.run')
    def test_native_aliases_with_distinct_paths_to_same_inode_are_protected(self, runner) -> None:
        self.volids = {'pool:vm-999-disk-0'}
        target = self.root / 'volume.raw'
        alias = self.root / 'alias.raw'
        target.touch()
        alias.hardlink_to(target)
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('scsi0: alias:vm-999-disk-0\n')
        runner.side_effect = lambda args, **kwargs: _ok(str(target if args[2].startswith('pool:') else alias))
        self.assertEqual(self.run_script()['guests'][0]['references'], ['pool:vm-999-disk-0'])

    @patch('subprocess.run')
    def test_unresolvable_native_alias_aborts(self, runner) -> None:
        self.volids = {'pool:vm-999-disk-0'}
        target = self.root / 'volume.raw'
        target.touch()
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('scsi0: alias:vm-999-disk-0\n')
        runner.side_effect = lambda args, **kwargs: _ok(str(target if args[2].startswith('pool:') else self.root / 'missing'))
        with self.assertRaises(FileNotFoundError):
            self.run_script()

    def test_invalid_configured_storage_inventory_aborts(self) -> None:
        for data in ({}, [], [{'storage': 'pool'}, {'storage': 'pool'}], [{'storage': 'bad/name'}]):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'storage'):
                self.storage_inventory = data
                self.run_script()

    @patch('subprocess.run')
    def test_absolute_path_alias_is_protected(self, runner) -> None:
        target = self.root / 'volume.raw'
        target.touch()
        alias = self.root / 'alias.raw'
        alias.symlink_to(target)
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text(f'scsi0: {alias}\n')
        runner.return_value = _ok(str(target))
        self.assertEqual(set(self.run_script()['guests'][0]['references']), self.volids)
        self.assertEqual(runner.call_count, len(self.volids))

    @patch('subprocess.run')
    def test_remote_path_alias_conservatively_protects_shared_candidates(self, runner) -> None:
        target = self.root / 'volume.raw'
        target.touch()
        unrelated = self.root / 'unrelated.raw'
        unrelated.touch()
        alias = self.root / 'remote-alias.raw'
        (self.root / 'nodes/pve2/qemu-server/101.conf').write_text(f'scsi0: {alias}\n')
        runner.return_value = _ok(str(target))
        # A remote alias can be missing here or point somewhere else locally.
        for local_alias_exists in (False, True):
            with self.subTest(local_alias_exists=local_alias_exists):
                if local_alias_exists:
                    alias.symlink_to(unrelated)
                self.assertEqual(set(self.run_script()['guests'][0]['references']), self.volids)

    @patch('subprocess.run')
    def test_absolute_subdirectory_reference_protects_containing_volume(self, runner) -> None:
        volume = self.root / 'subvol-999-disk-0'
        child = volume / 'data'
        child.mkdir(parents=True)
        (self.root / 'nodes/pve1/lxc/100.conf').write_text(f'mp0: {child},mp=/share\n')
        runner.return_value = _ok(str(volume))
        self.assertEqual(set(self.run_script()['guests'][0]['references']), self.volids)

    @patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['pvesm', 'path']))
    def test_unresolvable_volume_path_aborts(self, runner) -> None:
        (self.root / 'nodes/pve1/lxc/100.conf').write_text('mp0: /dev/unknown,mp=/data\n')
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_script()

    @patch('subprocess.run')
    def test_inactive_device_aliases_abort(self, runner) -> None:
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text(
            'scsi0: /dev/mapper/pve-vm--999--disk--0\n'
        )
        runner.return_value = _ok(str(self.root / 'missing-vg/vm-999-disk-0'))
        with self.assertRaises(FileNotFoundError):
            self.run_script()

    @patch('subprocess.run')
    def test_rbd_identifiers_are_retained_without_blocking_local_scan(self, runner) -> None:
        self.volids = {'ceph:vm-999-disk-0', 'pool:vm-999-disk-0', 'pool:vm-999-disk-1'}
        attached = self.root / 'attached.raw'
        orphan = self.root / 'orphan.raw'
        attached.touch()
        orphan.touch()
        (self.root / 'nodes/pve1/lxc/100.conf').write_text(f'mp0: {attached},mp=/data\n')
        for rbd_path in ('rbd:pool/vm-999-disk-0',
                         'rbd:pool/namespace/vm-999-disk-0:conf=/etc/pve/ceph.conf:id=admin'):
            with self.subTest(rbd_path=rbd_path):
                paths = {'ceph:vm-999-disk-0': rbd_path,
                         'pool:vm-999-disk-0': str(attached),
                         'pool:vm-999-disk-1': str(orphan)}
                runner.side_effect = lambda args, **kwargs: _ok(paths[args[2]])
                self.assertEqual(set(self.run_script()['guests'][0]['references']),
                                 {'ceph:vm-999-disk-0', 'pool:vm-999-disk-0'})

    @patch('subprocess.run')
    def test_unknown_or_malformed_volume_paths_abort(self, runner) -> None:
        (self.root / 'nodes/pve1/lxc/100.conf').write_text('mp0: /dev/unknown,mp=/data\n')
        for path in ('', 'rbd:', 'unknown:pool/image', '/dev/volume\nextra'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'invalid volume path'):
                runner.return_value = _ok(path)
                self.run_script()

    def mapping_runner(self, maps, target):
        def run(args, **kwargs):
            if args[:2] == ['pvesh', 'get']:
                self.assertEqual(args[2], '/cluster/mapping/dir')
                return _ok(json.dumps(maps))
            self.assertEqual(args[:2], ['pvesm', 'path'])
            return _ok(str(target))
        return run

    @patch('subprocess.run')
    def test_local_virtiofs_protects_exact_parent_and_child_paths(self, runner) -> None:
        volume = self.root / 'subvol-999-disk-0'
        child = volume / 'shared files'
        child.mkdir(parents=True)
        for mapped in (volume, volume.parent, child):
            for value in ('dirid=share,cache=always', 'share'):
                with self.subTest(mapped=mapped, value=value):
                    (self.root / 'nodes/pve1/qemu-server/100.conf').write_text(
                        f'name: guest\n[PENDING]\nvirtiofs0: {value}\n'
                    )
                    runner.side_effect = self.mapping_runner([
                        dict(id='share', map=[f'node=pve1,path={mapped}', 'node=pve2,path=/remote']),
                    ], volume)
                    self.assertEqual(set(self.run_script()['guests'][0]['references']), self.volids)

    @patch('subprocess.run')
    def test_unrelated_virtiofs_path_does_not_protect_volume(self, runner) -> None:
        volume = self.root / 'subvol-999-disk-0'
        share = self.root / 'unrelated'
        volume.mkdir()
        share.mkdir()
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('virtiofs0: dirid=share\n')
        runner.side_effect = self.mapping_runner([dict(id='share', map=[f'node=pve1,path={share}'])], volume)
        self.assertEqual(self.run_script()['guests'][0]['references'], [])

    @patch('subprocess.run')
    def test_unresolved_or_invalid_virtiofs_mapping_aborts(self, runner) -> None:
        volume = self.root / 'volume.raw'
        volume.touch()
        (self.root / 'nodes/pve1/qemu-server/100.conf').write_text('virtiofs0: dirid=share\n')
        for maps in ([], {}, [dict(id='share', map=['node=pve2,path=/remote'])],
                     [dict(id='share', map=[f'node=pve1,path={self.root / "missing"}'])],
                     [dict(id='share', map=['node=pve1,path=/tmp,node=pve2'])],
                     [dict(id='share', map=['node=pve1,path=/tmp', 'node=pve1,path=/tmp'])]):
            with self.subTest(maps=maps), self.assertRaises((ValueError, FileNotFoundError)):
                runner.side_effect = self.mapping_runner(maps, volume)
                self.run_script()

    @patch('subprocess.run')
    def test_remote_virtiofs_conservatively_protects_shared_candidates(self, runner) -> None:
        volume = self.root / 'volume.raw'
        volume.touch()
        (self.root / 'nodes/pve2/qemu-server/100.conf').write_text('virtiofs0: dirid=share\n')
        runner.side_effect = self.mapping_runner([dict(id='share', map=['node=pve2,path=/remote'])], volume)
        self.assertEqual(set(self.run_script()['guests'][0]['references']), self.volids)

    def test_missing_config_directory_aborts(self) -> None:
        (self.root / 'nodes/pve2/lxc').rmdir()
        with self.assertRaises(FileNotFoundError):
            self.run_script()

    def test_empty_configuration_aborts(self) -> None:
        (self.root / 'nodes/pve1/qemu-server/100.conf').touch()
        with self.assertRaisesRegex(ValueError, 'empty guest configuration'):
            self.run_script()


class TestDeleteVolume(unittest.TestCase):
    def setUp(self):
        scan = patch('lib.proxmox_storage.list_orphaned_volumes', return_value=[
            OrphanedVolume('local-lvm:vm-999-disk-0', 'local-lvm', 999, '20G'),
        ])
        self.scan = scan.start()
        self.addCleanup(scan.stop)

    @patch('lib.proxmox_storage._ssh_run')
    def test_rechecks_before_deleting(self, mock_run):
        self.scan.return_value = []
        with self.assertRaisesRegex(ProxmoxStorageError, 'fresh orphan inventory'):
            delete_volume(_host(), 'local-lvm:vm-999-disk-0')
        mock_run.assert_not_called()

    @patch("lib.proxmox_storage._ssh_run")
    def test_calls_pvesm_free(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok()
        delete_volume(_host(), "local-lvm:vm-999-disk-0")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pvesm", cmd)
        self.assertIn("free", cmd)
        self.assertIn("local-lvm:vm-999-disk-0", cmd)

    @patch("lib.proxmox_storage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _fail("volume in use")
        with self.assertRaises(ProxmoxStorageError):
            delete_volume(_host(), "local-lvm:vm-999-disk-0")

    @patch("lib.proxmox_storage._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok()
        delete_volume(_host(), "local-lvm:vm-999-disk-0", dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
