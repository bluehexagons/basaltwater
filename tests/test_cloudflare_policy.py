"""Cloudflare direct-access policy and CLI regression tests."""

from __future__ import annotations

import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from web.cloudflare_firewall import RULE_PREFIX, reconcile_tunnel_firewall
from web.cloudflare_steps import configure_cloudflare_firewall, run_cloudflare_tunnel_setup


class FirewallModel:
    """A UFW command fixture that tracks rules and shifting rule numbers."""

    def __init__(self):
        self.rules = [('3478/udp', 'Anywhere', 'antistatic STUN')]
        self.commands = []

    def run(self, command, **_kwargs):
        self.commands.append(command)
        if command[1] == 'allow':
            comment = command[command.index('comment') + 1]
            if command[2] == 'from':
                source = command[3]
                port = command[command.index('port') + 1] + '/tcp'
            else:
                port, source = command[2], 'Anywhere'
            self.rules = [rule for rule in self.rules if rule[:2] != (port, source)]
            self.rules.append((port, source, comment))
        elif command[1:3] == ['delete', 'allow']:
            port = command[3]
            self.rules = [rule for rule in self.rules if rule[:2] != (port, 'Anywhere')]
        elif command[1:3] == ['--force', 'delete']:
            self.rules.pop(int(command[3]) - 1)
        elif command[1:3] == ['status', 'numbered']:
            lines = ['Status: active']
            lines.extend(
                f'[{index:2}] {port} ALLOW IN {source} # {comment}'
                for index, (port, source, comment) in enumerate(self.rules, 1)
            )
            return SimpleNamespace(returncode=0, stdout='\n'.join(lines))
        return SimpleNamespace(returncode=0, stdout='')


class TestCloudflareWebPolicy(unittest.TestCase):
    def test_unrestricted_then_lan_then_tunnel_only_removes_stale_rules(self):
        firewall = FirewallModel()
        reconcile_tunnel_firewall(allow_direct=True, sources=[], command_runner=firewall.run)
        self.assertTrue(all((f'{port}/tcp', 'Anywhere', f'{RULE_PREFIX}{port}') in firewall.rules for port in (80, 443)))
        reconcile_tunnel_firewall(
            allow_direct=False, sources=['192.168.0.52/24', 'fd00::/64'], command_runner=firewall.run,
        )
        web_rules = [rule for rule in firewall.rules if rule[0] in ('80/tcp', '443/tcp')]
        self.assertEqual(len(web_rules), 4)
        self.assertEqual({rule[1] for rule in web_rules}, {'192.168.0.0/24', 'fd00::/64'})
        reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=firewall.run)
        self.assertEqual(firewall.rules, [('3478/udp', 'Anywhere', 'antistatic STUN')])

    def test_stale_ipv6_rules_are_deleted_in_descending_order(self):
        firewall = FirewallModel()
        firewall.rules.extend([
            ('80/tcp (v6)', 'Anywhere (v6)', f'{RULE_PREFIX}80'),
            ('443/tcp (v6)', 'Anywhere (v6)', f'{RULE_PREFIX}443'),
        ])
        reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=firewall.run)
        deletes = [command for command in firewall.commands if command[1:3] == ['--force', 'delete']]
        self.assertEqual(deletes, [['ufw', '--force', 'delete', '3'], ['ufw', '--force', 'delete', '2']])
        self.assertEqual(len(firewall.rules), 1)

    def test_invalid_sources_fail_before_firewall_commands(self):
        for sources in (['192.168.0.0/24; touch /tmp/unsafe'], [None], '192.168.0.0/24'):
            with self.subTest(sources=sources):
                runner = Mock()
                with self.assertRaises(ValueError):
                    reconcile_tunnel_firewall(allow_direct=False, sources=sources, command_runner=runner)
                runner.assert_not_called()

    def test_failed_firewall_inspection_is_fatal(self):
        for result in (SimpleNamespace(returncode=1, stdout=''), SimpleNamespace(returncode=0, stdout='Status: inactive')):
            with self.subTest(result=result):
                with self.assertRaisesRegex(RuntimeError, 'Could not inspect'):
                    reconcile_tunnel_firewall(allow_direct=True, sources=[], command_runner=Mock(return_value=result))


class TestCloudflareDirectCli(unittest.TestCase):
    def _parse_config(self, argv):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(argv)
        with patch('lib.system_utils.get_local_timezone', return_value='UTC'):
            return SetupConfig.from_args(args, 'server_web')

    def test_flag_survives_saved_config_reconstruction_and_remote_handoff(self):
        config = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare', '--cloudflare-allow-direct'])
        self.assertTrue(config.cloudflare_allow_direct)
        saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        self.assertTrue(saved.cloudflare_allow_direct)
        rebuilt = self._parse_config(shlex.split(' '.join(saved.to_setup_command()))[1:])
        self.assertTrue(rebuilt.cloudflare_allow_direct)
        remote = create_setup_argument_parser('remote', for_remote=True).parse_args(shlex.split(' '.join(saved.to_remote_args())))
        self.assertTrue(remote.cloudflare_allow_direct)

    def test_unrelated_patch_preserves_policy_and_explicit_negative_disables_it(self):
        cached = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare', '--cloudflare-allow-direct'])
        for flag, expected in (([], True), (['--no-cloudflare-allow-direct'], False)):
            with self.subTest(flag=flag):
                update = self._parse_config(['patch', '192.168.0.52', 'alice', *flag])
                merged = merge_setup_configs(cached, update)
                self.assertEqual(merged.cloudflare_allow_direct, expected)
                self.assertTrue(merged.enable_cloudflare)

    def test_lan_access_is_resolved_and_passed_to_creation_and_refresh(self):
        config = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare', '--lan-access'])
        remote_args = create_setup_argument_parser('remote', for_remote=True).parse_args(shlex.split(' '.join(config.to_remote_args())))
        self.assertEqual(remote_args.access_sources, ['192.168.0.0/24'])
        for creating in (True, False):
            with self.subTest(creating=creating), patch('web.cloudflare_steps.os.path.exists', side_effect=lambda path: not creating or not path.endswith('tunnel-state.json')), \
                    patch.dict('os.environ', {'BASALTWATER_SETUP_PROMPT_RELAY': '1'}), \
                    patch('web.cloudflare_steps.run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertTrue(run_cloudflare_tunnel_setup(config, allow_create=True))
                command = run.call_args.args[0]
                self.assertIn('--no-allow-direct', command)
                self.assertEqual(command[-2:], ['--access-source', '192.168.0.0/24'])

    def test_activated_tunnel_allows_lan_origins_and_unrestricted_opt_in(self):
        for extra, expected in ((['--lan-access'], '192.168.0.0/24'), (['--cloudflare-allow-direct'], 'Anywhere')):
            with self.subTest(extra=extra):
                config = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare', *extra])
                firewall = FirewallModel()

                def run(command, **kwargs):
                    if isinstance(command, list):
                        return firewall.run(command, **kwargs)
                    return SimpleNamespace(returncode=0, stdout='')

                with patch('web.cloudflare_steps.can_manage_firewall', return_value=True), patch('web.cloudflare_steps.run', side_effect=run):
                    configure_cloudflare_firewall(config)
                web_rules = [rule for rule in firewall.rules if rule[0] in ('80/tcp', '443/tcp')]
                self.assertEqual({rule[1] for rule in web_rules}, {expected})
                if '--lan-access' in extra:
                    self.assertEqual({rule[1] for rule in firewall.rules if rule[0] == '22/tcp'}, {expected})
                    self.assertNotIn(['ufw', 'limit', 'ssh'], firewall.commands)

    def test_incapable_machine_skips_firewall_commands(self):
        config = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare'])
        with patch('web.cloudflare_steps.can_manage_firewall', return_value=False), patch('web.cloudflare_steps.run') as run:
            configure_cloudflare_firewall(config)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
