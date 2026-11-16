"""Bounded saved selection, private root authority and uncertain DNS results."""

from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import headscale_dns_authority as authority, headscale_dns_control as control
from lib import headscale_dns_orchestration as flow
from lib.headscale_dns_binding import DNSBinding
from lib.headscale_management import ManagementBinding
from lib.headscale_management_cli import add_headscale_management_parser, run_headscale_management_command
from lib.headscale_private import read_private, write_private
from lib.headscale_transport import SSHBinding, TransportError

URL = 'https://vpn.example.com'
KEY = 'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + b'x' * 32).decode()
NODE = DNSBinding(URL, '7', ('100.64.0.7',))
PEER = DNSBinding(URL, '8', ('100.64.0.8',))
PUBLISHED = replace(NODE, advertised=True, publication_id='00000000-0000-4000-8000-000000000001')


class FlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        self.binding = ManagementBinding('server', URL, SSHBinding('control.example.com', '/private/key', '/private/hosts'))
        self.dns = SSHBinding('dns.example.com', '/private/dns', '/private/hosts')
        self.peer = SSHBinding('peer.example.com', '/private/peer', '/private/hosts')
        self.state = {'binding': None, 'journal': None, 'config': {}}
        self.node = NODE
        self.calls = []
        for name, kwargs in {
            'resolve_management': {'return_value': self.binding},
            'saved_target': {'side_effect': lambda ref, *a: self.dns if ref in {'dns', self.dns.host} else self.peer},
            'resource_lock': {'side_effect': lambda *a: nullcontext()},
            '_pins': {'side_effect': lambda target: target.host + ' ' + KEY + '\n'},
            'exchange': {'side_effect': self.exchange},
        }.items():
            self.addCleanup(patch.stopall)
            patch.object(flow, name, **kwargs).start()

    def exchange(self, binding, service, request, **kwargs):
        action = request['action']
        self.calls.append((binding, service, request))
        if action == 'inspect':
            return self.state
        if action == 'observe':
            return {'binding': (self.node if binding == self.dns else PEER).to_dict()}
        if action == 'key':
            return {'key': KEY}
        if action == 'authorize':
            return {'authorized': True}
        if action == 'confirm':
            return {'confirmed': True, 'binding': self.state['binding']}
        self.assertIsNotNone(read_private(str(Path(self.workspace) / 'headscale-dns-attempt.json')))
        return {'binding': PUBLISHED.to_dict() if action == 'publish' else None}

    def run_flow(self, action='publish', **kw):
        return flow.manage_dns('office', action=action, resolver='dns', peer='peer', workspace=self.workspace, **kw)

    def test_observed_ids_and_exact_withdrawal_publication(self):
        self.assertEqual(self.run_flow()['binding'], PUBLISHED.to_dict())
        request = self.calls[-1][2]['parameters']
        self.assertEqual(request['resolver'], NODE.to_dict())
        self.assertEqual(request['peer'], PEER.to_dict())
        self.assertFalse(list(Path(self.workspace).iterdir()))
        self.state['binding'] = PUBLISHED.to_dict()
        self.node = PUBLISHED
        self.run_flow('withdraw')
        self.assertEqual(self.calls[-1][2]['parameters']['resolver']['publication_id'], PUBLISHED.publication_id)

    def test_dry_run_uses_no_credentials_network_locks_or_writes(self):
        with patch.object(flow, '_credential') as credential, patch.object(flow, 'resource_lock') as lock, \
                patch.object(flow, 'write_private') as write:
            self.assertTrue(self.run_flow(dry_run=True)['dry_run'])
            credential.assert_not_called()
            lock.assert_not_called()
            write.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_existing_publication_or_journal_refused_before_authorization(self):
        for state in ({'binding': PUBLISHED.to_dict(), 'journal': None}, {'binding': None, 'journal': {'retained': True}}):
            self.state = state
            self.calls.clear()
            with self.assertRaisesRegex(ValueError, 'inspect/reconcile'):
                self.run_flow()
            self.assertEqual([call[2]['action'] for call in self.calls], ['inspect'])

    def test_lost_and_unknown_results_preserve_exact_attempt_and_block_replay(self):
        for reply in (TransportError('lost'), {'unknown': True}):
            with self.subTest(reply=type(reply).__name__):
                journal = Path(self.workspace) / 'headscale-dns-attempt.json'
                journal.unlink(missing_ok=True)
                original = self.exchange
                def fail(binding, service, request, **kw):
                    if request['action'] == 'publish':
                        if isinstance(reply, Exception):
                            raise reply
                        return reply
                    return original(binding, service, request, **kw)
                with patch.object(flow, 'exchange', side_effect=fail):
                    with self.assertRaises((TransportError, ValueError)):
                        self.run_flow()
                before = journal.read_bytes()
                with patch.object(flow, 'exchange') as exchange:
                    with self.assertRaisesRegex(ValueError, 'Retained DNS attempt'):
                        self.run_flow()
                    exchange.assert_not_called()
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(json.loads(before)['parameters']['resolver'], NODE.to_dict())

    def test_foreign_network_duplicate_peers_and_stale_withdrawal_fail(self):
        for node in (replace(NODE, network_url='https://other.example.com'), PEER):
            self.node = node
            with self.assertRaises(ValueError):
                self.run_flow()
        self.node = PUBLISHED
        self.state['binding'] = replace(PUBLISHED, publication_id='00000000-0000-4000-8000-000000000002').to_dict()
        with self.assertRaisesRegex(ValueError, 'actual advertised'):
            self.run_flow('withdraw')
        self.assertFalse(any(call[2]['action'] in {'authorize', 'publish', 'withdraw'} for call in self.calls))

    def attempt(self, action):
        value = {'endpoint': URL, 'action': action, 'parameters': {
            'resolver': (NODE if action == 'publish' else PUBLISHED).to_dict(), 'peer': PEER.to_dict(),
            'resolver_host': self.dns.host, 'peer_host': self.peer.host,
            'resolver_pins': self.dns.host + ' ' + KEY + '\n', 'peer_pins': self.peer.host + ' ' + KEY + '\n'}}
        write_private(str(Path(self.workspace) / 'headscale-dns-attempt.json'), value)
        return value

    def test_reviewed_completion_observes_both_settled_sides_without_mutation_replay(self):
        for action in ('publish', 'withdraw'):
            for node in (NODE, PUBLISHED):
                with self.subTest(action=action, advertised=node.advertised):
                    expected = self.attempt(action)
                    self.node = node
                    self.state['binding'] = node.to_dict() if node.advertised else None
                    self.calls.clear()
                    result = flow.reconcile_dns_attempt('office', expected=expected, workspace=self.workspace)
                    self.assertTrue(result['reconciled'])
                    self.assertEqual(['inspect', 'observe', 'observe', 'confirm'], [c[2]['action'] for c in self.calls])
                    self.assertIsNone(read_private(str(Path(self.workspace) / 'headscale-dns-attempt.json')))

    def test_completion_preserves_stale_unsettled_changed_and_unconfirmed_attempts(self):
        expected = self.attempt('withdraw')
        path = str(Path(self.workspace) / 'headscale-dns-attempt.json')
        for state, node in (({'binding': None, 'journal': {'pending': True}, 'config': {}}, NODE),
                            ({'binding': None, 'journal': None, 'config': {}}, PUBLISHED),
                            ({'binding': PUBLISHED.to_dict(), 'journal': None, 'config': {}},
                             replace(PUBLISHED, publication_id='00000000-0000-4000-8000-000000000002'))):
            self.state, self.node = state, node
            with self.assertRaises(ValueError):
                flow.reconcile_dns_attempt('office', expected=expected, workspace=self.workspace)
            self.assertEqual(expected, read_private(path))
        with self.assertRaises(ValueError):
            flow.reconcile_dns_attempt('office', expected={**expected, 'endpoint': 'https://other.example.com'}, workspace=self.workspace)
        self.state, self.node = {'binding': None, 'journal': None, 'config': {}}, NODE
        original = self.exchange
        def lost(binding, service, request, **kw):
            if request['action'] == 'confirm':
                raise TransportError('lost confirmation')
            return original(binding, service, request, **kw)
        with patch.object(flow, 'exchange', side_effect=lost), self.assertRaises(TransportError):
            flow.reconcile_dns_attempt('office', expected=expected, workspace=self.workspace)
        self.assertEqual(expected, read_private(path))

    def test_completion_dry_run_never_calls_native_or_writes(self):
        expected = self.attempt('publish')
        with patch.object(flow, 'resource_lock', side_effect=AssertionError('lock')), \
                patch.object(flow, 'remove_file_durable', side_effect=AssertionError('write')):
            self.assertTrue(flow.reconcile_dns_attempt('office', expected=expected,
                                                       workspace=self.workspace, dry_run=True)['dry_run'])
        self.assertEqual([], self.calls)

    def test_public_completion_command_loads_exact_private_review(self):
        expected = self.attempt('publish')
        path = Path(self.workspace) / 'review.json'
        write_private(str(path), expected)
        parser = argparse.ArgumentParser()
        add_headscale_management_parser(parser.add_subparsers())
        args = parser.parse_args(['headscale-manage', 'dns-reconcile', 'office',
                                  '--expected-attempt', str(path), '--workspace', self.workspace, '--dry-run'])
        with patch.object(flow, 'reconcile_dns_attempt', return_value={'dry_run': True}) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(0, run_headscale_management_command(args))
        self.assertEqual(expected, run.call_args.kwargs['expected'])

    def test_public_parser_dispatch_and_generic_error(self):
        parser = argparse.ArgumentParser()
        add_headscale_management_parser(parser.add_subparsers())
        for action in ('publish', 'withdraw', 'inspect'):
            argv = ['headscale-manage', 'dns-' + action, 'office', '--dry-run']
            if action != 'inspect':
                argv += ['--resolver', 'dns', '--peer', 'peer']
            args = parser.parse_args(argv)
            with patch.object(flow, 'manage_dns', return_value={'dry_run': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(run_headscale_management_command(args), 0)
                self.assertEqual(run.call_args.kwargs['action'], action)
        output = io.StringIO()
        with patch.object(flow, 'manage_dns', side_effect=ValueError('private-detail')), redirect_stderr(output):
            self.assertEqual(run_headscale_management_command(args), 1)
        self.assertNotIn('private-detail', output.getvalue())
        output = io.StringIO()
        with patch.object(flow, 'manage_dns', side_effect=flow.DNSPublicationExists('private-detail')), redirect_stderr(output):
            self.assertEqual(run_headscale_management_command(args), 1)
        self.assertIn('publication refused', output.getvalue())
        self.assertNotIn('private-detail', output.getvalue())


class AuthorityTests(unittest.TestCase):
    def test_settled_confirmation_checks_native_peer_without_reactivation(self):
        from lib import headscale_dns_publish as publication

        admin = SimpleNamespace(endpoint=URL, paths=SimpleNamespace(private=Path('/private/control')))
        parameters = {'resolver': PUBLISHED.to_dict(), 'peer': PEER.to_dict(),
                      'resolver_host': 'dns.example.com', 'peer_host': 'peer.example.com',
                      'resolver_pins': 'pins', 'peer_pins': 'pins'}
        config = {'dns': {'magic_dns': True, 'base_domain': 'mesh.example.net', 'override_local_dns': True,
                          'nameservers': {'global': list(PUBLISHED.addresses), 'split': {}}}}
        with patch.object(control, 'dns_binding_lease', return_value=nullcontext(SimpleNamespace(binding=PUBLISHED))), \
                patch('lib.operation_state.OperationStateStore') as store, \
                patch.object(publication, '_journal', return_value=None) as journal, \
                patch.object(publication, '_read_config', return_value=config), \
                patch.object(publication, '_node') as node, patch.object(publication, '_verify_active') as verify, \
                patch.object(control, 'private_bytes', side_effect=lambda path: URL.encode() if path.endswith('/endpoint') else b'retained-placeholder'), \
                patch.object(control, 'publication_key', return_value=('/private/key', KEY)), \
                patch.object(control, 'remote', return_value='remote'), \
                patch.object(control, 'publish_dns', side_effect=AssertionError('activation')), \
                patch.object(control, 'withdraw_dns', side_effect=AssertionError('withdrawal')):
            store.return_value.load.return_value = None
            self.assertEqual({'confirmed': True, 'binding': PUBLISHED.to_dict()}, control.confirm(admin, parameters))
            node.assert_called_once_with('remote', PUBLISHED)
            verify.assert_called_once_with(admin, config, PUBLISHED, 'remote', PEER, PUBLISHED)
            journal.return_value = {'pending': True}
            verify.reset_mock()
            with self.assertRaisesRegex(ValueError, 'Reconcile native'):
                control.confirm(admin, parameters)
            verify.assert_not_called()

    def test_publication_key_reuses_endpoint_binding_and_never_replaces_lost_generation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control, 'root_directory'), \
                patch.object(control, 'resource_lock', side_effect=lambda *a: nullcontext()), \
                patch.object(control, 'private_bytes') as read, patch.object(control, 'write_bytes_atomic') as write, \
                patch.object(control.subprocess, 'run', return_value=SimpleNamespace(stdout=(KEY + '\n').encode())) as run:
            private = Path(directory)
            (private / 'dns-authority').mkdir(mode=0o700)
            admin = SimpleNamespace(endpoint=URL, paths=SimpleNamespace(private=private))
            read.side_effect = [URL.encode(), b'private-placeholder']
            key, public = control.publication_key(admin)
            self.assertEqual(public, KEY)
            self.assertTrue(key.endswith('/dns-authority/identity'))
            self.assertEqual(run.call_args.args[0][1], '-y')
            write.assert_not_called()
            run.reset_mock()
            read.side_effect = [URL.encode(), None]
            with self.assertRaisesRegex(ValueError, 'generation unconfirmed'):
                control.publication_key(admin)
            run.assert_not_called()
            read.side_effect = [b'https://other.example.com']
            with self.assertRaisesRegex(ValueError, 'endpoint changed'):
                control.publication_key(admin)
            run.assert_not_called()

    def test_peer_observation_requires_owned_private_native_identity(self):
        from common import unbound_steps
        from lib import headscale_client as client
        ownership = {'phase': 'enrolled', 'node_id': PEER.node_id}
        with patch.object(client, 'client_lock', side_effect=lambda: nullcontext()), \
                patch.object(client, 'reject_control_host'), patch.object(client, 'owned', return_value=ownership), \
                patch.object(unbound_steps, '_observe', return_value=PEER) as observe:
            self.assertEqual(authority.observe(URL, 'peer'), {'binding': PEER.to_dict()})
            ownership['node_id'] = '99'
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                authority.observe(URL, 'peer')
            ownership['phase'] = 'joining'
            observe.reset_mock()
            with self.assertRaisesRegex(ValueError, 'enrolled'):
                authority.observe(URL, 'peer')
            observe.assert_not_called()

    def test_private_authority_rejects_nonroot_owner_hardlinks_and_symlink_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            linked = Path(directory) / 'link'
            linked.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(ValueError):
                authority.root_directory(str(linked))
            path = Path(directory) / 'private'
            path.write_text('placeholder')
            path.chmod(0o600)
            with patch.object(authority, 'root_directory'), \
                    patch.object(authority.os, 'fstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=123, st_nlink=1, st_size=11)):
                with self.assertRaises(ValueError):
                    authority.private_bytes(str(path))
            with patch.object(authority, 'root_directory'), \
                    patch.object(authority.os, 'fstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=2, st_size=11)):
                with self.assertRaises(ValueError):
                    authority.private_bytes(str(path))

    def test_exact_hashed_pins_and_pattern_or_marked_refusal(self):
        host = 'dns.example.com'
        salt = b's' * 20
        hashed = '|1|' + base64.b64encode(salt).decode() + '|' + base64.b64encode(hmac.new(salt, host.encode(), hashlib.sha1).digest()).decode()
        for name in (host, hashed):
            self.assertEqual(control.pinned_entries(host, name + ' ' + KEY), host + ' ' + KEY + '\n')
        for text in ('other.example.com ' + KEY, '*.example.com ' + KEY, '@revoked ' + host + ' ' + KEY, '!other,' + host + ' ' + KEY):
            with self.assertRaises(ValueError):
                control.pinned_entries(host, text)

    def test_authorization_idempotent_preserves_unrelated_and_rejects_unsafe_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(authority, 'root_directory'), \
                patch.object(authority, 'resource_lock', side_effect=lambda *a: nullcontext()), \
                patch.object(authority.os, 'geteuid', return_value=0):
            path = Path(directory) / 'authorized_keys'
            unrelated = b'# existing key comment\n'
            path.write_bytes(unrelated)
            path.chmod(0o600)
            # Model ownership on nonroot runners without relaxing mode/type checks.
            original = authority.os.fstat
            def root_stat(fd):
                info = original(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink, st_size=info.st_size)
            with patch.object(authority.os, 'fstat', side_effect=root_stat):
                authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)
                before = path.read_bytes()
                authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)
                self.assertEqual(before, path.read_bytes())
                self.assertTrue(before.startswith(unrelated))
                self.assertIn(('restrict,command="' + authority.COMMAND + ' --scope ' + authority.publication_scope(URL, NODE.node_id, 'resolver') + '"').encode(), before)
                for endpoint, node, role in (('https://other.example.com', NODE.node_id, 'resolver'),
                                             (URL, '99', 'resolver'), (URL, NODE.node_id, 'peer')):
                    with self.assertRaisesRegex(ValueError, 'foreign authorization'):
                        authority.authorize(KEY, endpoint, node, role, directory=directory)
                    self.assertEqual(before, path.read_bytes())
                for ending in (b'\n', b'\r\n', b''):
                    with self.subTest(ending=ending):
                        path.write_bytes(unrelated + f'restrict,command="{authority.COMMAND}" {KEY}'.encode() + ending)
                        authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)
                        self.assertEqual(before, path.read_bytes())
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)
                path.chmod(0o600)
                path.write_text(KEY + '\n')
                with self.assertRaisesRegex(ValueError, 'foreign authorization'):
                    authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)
                path.unlink()
                path.symlink_to('missing')
                with self.assertRaises((OSError, ValueError)):
                    authority.authorize(KEY, URL, NODE.node_id, 'resolver', directory=directory)

    def test_authorization_scope_uses_root_observed_node_not_request_identity(self):
        request = {'endpoint': URL, 'action': 'authorize', 'parameters': {'key': KEY, 'role': 'resolver'}}
        with patch.object(authority.os, 'geteuid', return_value=0), \
                patch.object(authority, 'observe', return_value={'binding': NODE.to_dict()}), \
                patch.object(authority, 'authorize', return_value={'authorized': True}) as authorize:
            self.assertTrue(authority.execute_request(json.dumps(request).encode())['ok'])
            authorize.assert_called_once_with(KEY, URL, NODE.node_id, 'resolver')
            request['parameters']['node_id'] = '99'
            self.assertFalse(authority.execute_request(json.dumps(request).encode())['ok'])
            self.assertEqual(1, authorize.call_count)

    def test_root_schemas_and_endpoint_fail_closed_without_secret_errors(self):
        for module in (authority, control):
            with patch.object(module.os, 'geteuid', return_value=0):
                for payload in (b'x' * 16385, b'{}', json.dumps({'endpoint': URL, 'action': 'shell', 'parameters': {}, 'extra': True}).encode()):
                    self.assertFalse(module.execute_request(payload)['ok'])
        with patch.object(control.os, 'geteuid', return_value=0), patch.object(control, 'NativeAdmin') as admin:
            admin.return_value.endpoint = 'https://other.example.com'
            with patch.object(control, 'inspect_dns') as inspect:
                self.assertFalse(control.execute_request(json.dumps({'endpoint': URL, 'action': 'inspect', 'parameters': {}}).encode())['ok'])
                inspect.assert_not_called()

    def test_control_exact_withdrawal_and_journal_guard_use_existing_api(self):
        admin = SimpleNamespace(endpoint=URL)
        parameters = {'resolver': PUBLISHED.to_dict(), 'peer': PEER.to_dict(),
                      'resolver_host': 'dns.example.com', 'peer_host': 'peer.example.com',
                      'resolver_pins': 'dns.example.com ' + KEY + '\n', 'peer_pins': 'peer.example.com ' + KEY + '\n'}
        state = {'binding': PUBLISHED.to_dict(), 'journal': None}
        with patch.object(control, 'inspect_dns', return_value=state), \
                patch.object(control, 'publication_key', return_value=('/private/key', KEY)) as key, \
                patch.object(control, 'remote', return_value=Mock()), patch.object(control, 'withdraw_dns', return_value={'binding': None}) as withdraw:
            control.change(admin, 'withdraw', parameters)
            self.assertEqual(withdraw.call_args.args, (PUBLISHED,))
            withdraw.reset_mock()
            state['journal'] = {'pending': True}
            key.reset_mock()
            with self.assertRaisesRegex(ValueError, 'inspect/reconcile'):
                control.change(admin, 'withdraw', parameters)
            key.assert_not_called()
            withdraw.assert_not_called()


class SelectionTests(unittest.TestCase):
    def test_malformed_saved_inventory_fails_with_bounded_validation_error(self):
        with tempfile.TemporaryDirectory() as workspace:
            setups = Path(workspace) / 'setups'
            setups.mkdir(mode=0o700)
            path = setups / 'invalid.json'
            for value in ([], 1, 'invalid', None, {}, {'host': 'dns.example.com', 'args': []}):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    path.chmod(0o600)
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'Invalid saved host inventory'), \
                            patch.object(flow, 'exchange', side_effect=AssertionError('network')):
                        flow.saved_target('dns', SimpleNamespace(server_id='server', endpoint=URL), workspace)
                    self.assertEqual(before, path.read_bytes())

    def test_actual_cache_writer_permissions_and_noncreating_read(self):
        from lib.cache import load_setup_command, save_setup_command
        from lib.config import SetupConfig
        from lib.workspace import WORKSPACE_ENV_VAR

        with tempfile.TemporaryDirectory() as workspace, patch.dict(os.environ, {WORKSPACE_ENV_VAR: workspace}):
            self.assertIsNone(load_setup_command('192.0.2.11'))
            self.assertFalse((Path(workspace) / 'setups').exists())
            config = SetupConfig(host='192.0.2.11', username='agent', system_type='server_lite',
                friendly_name='dns', ssh_key='/private/key', tailscale_server='office',
                tailscale_server_id='server', tailscale_login_server=URL)
            save_setup_command(config)
            setups = Path(workspace) / 'setups'
            self.assertEqual(0o700, setups.stat().st_mode & 0o777)
            # Older caches with safe readable parents also remain usable.
            setups.chmod(0o755)
            with patch.object(flow, 'resolve_client_server', return_value=SimpleNamespace(id='server', url=URL)):
                result = flow.saved_target('dns', SimpleNamespace(server_id='server', endpoint=URL), workspace)
            self.assertEqual(config.host, result.host)

    def test_unique_alias_actual_host_explicit_key_endpoint_and_port(self):
        with tempfile.TemporaryDirectory() as workspace:
            setups = Path(workspace) / 'setups'
            setups.mkdir(mode=0o700)
            path = setups / 'one.json'
            record = {'host': 'dns.example.com', 'args': {'friendly_name': 'dns'}, 'system_type': 'server_web'}
            write_private(str(path), record)
            config = SimpleNamespace(host='dns.example.com', ssh_key='/private/key', tailscale=True,
                                     tailscale_server='office', tailscale_server_id='server', tailscale_login_server=URL)
            management = SimpleNamespace(server_id='server', endpoint=URL)
            with patch.object(flow, '_load_cache_file', return_value=config), \
                    patch.object(flow, 'resolve_client_server', return_value=SimpleNamespace(id='server', url=URL)):
                self.assertEqual(flow.saved_target('dns', management, workspace).host, 'dns.example.com')
                config.ssh_key = None
                with self.assertRaises(ValueError):
                    flow.saved_target('dns', management, workspace)
                config.ssh_key = '/private/key'
                record['args']['ssh_port'] = 2222
                write_private(str(path), record)
                with self.assertRaisesRegex(ValueError, 'port 22'):
                    flow.saved_target('dns', management, workspace)
                record['args'].pop('ssh_port')
                write_private(str(path), record)
                write_private(str(setups / 'two.json'), record)
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    flow.saved_target('dns', management, workspace)


if __name__ == '__main__':
    unittest.main()
