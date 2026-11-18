"""Cloudflare direct-access policy and CLI regression tests."""

from __future__ import annotations

import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from plugins.post_setup import build_post_setup_steps
from plugins.server import build_server_steps
from web.cloudflare_firewall import ORIGIN_PORTS, RULE_PREFIX, WEB_RULE_PREFIX, reconcile_tunnel_firewall
from web.cloudflare_steps import configure_cloudflare_firewall, run_cloudflare_tunnel_setup
from web.service_tools import setup_cloudflare_tunnel as tunnel_setup


class FirewallModel:
    """A UFW command fixture that tracks rules and shifting rule numbers."""

    def __init__(self, *, active=True, incoming_default='deny'):
        self.rules = [('3478/udp', 'Anywhere', 'antistatic STUN')]
        self.commands = []
        self.active = active
        self.incoming_default = incoming_default

    def run(self, command, **_kwargs):
        if isinstance(command, str):
            command = shlex.split(command)
        self.commands.append(command)
        if command[:3] == ['ufw', '--force', 'enable']:
            self.active = True
        elif command[:3] == ['ufw', 'status', 'verbose']:
            stdout = f'Status: active\nDefault: {self.incoming_default} (incoming)' if self.active else 'Status: inactive'
            return SimpleNamespace(returncode=0, stdout=stdout)
        elif command[:2] == ['ufw', 'status'] and 'grep' in command:
            return SimpleNamespace(returncode=0 if self.active else 1, stdout='')
        elif command[1] == 'allow':
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
            if not self.active:
                return SimpleNamespace(returncode=0, stdout='Status: inactive')
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

    def test_operator_broad_rule_is_preserved_and_reported(self):
        firewall = FirewallModel()
        firewall.rules.append(('80/tcp', 'Anywhere', 'operator web rule'))

        with self.assertRaisesRegex(RuntimeError, 'outside the requested'):
            reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=firewall.run)
        self.assertIn(('80/tcp', 'Anywhere', 'operator web rule'), firewall.rules)

    def test_unmanaged_web_rules_outside_lan_policy_are_reported(self):
        for port, source in (
            ('443/tcp (v6)', 'Anywhere (v6)'),
            ('79:81/tcp', 'Anywhere'),
            ('80,443/tcp', 'Anywhere'),
            ('Anywhere', 'Anywhere'),
            ('80/tcp on eth0', 'Anywhere'),
            ('443/tcp', '192.168.0.0/16'),
        ):
            with self.subTest(port=port, source=source):
                firewall = FirewallModel()
                firewall.rules.append((port, source, 'operator rule'))
                with self.assertRaisesRegex(RuntimeError, 'outside the requested'):
                    reconcile_tunnel_firewall(
                        allow_direct=False, sources=['192.168.0.0/24'], command_runner=firewall.run,
                    )

    def test_web_policy_preserves_existing_ssh_application_profile(self):
        firewall = FirewallModel()
        firewall.rules.append(('OpenSSH', 'Anywhere', 'operator ssh rule'))

        def run(command, **kwargs):
            if command[1:3] == ['app', 'info']:
                return SimpleNamespace(returncode=0, stdout='Profile: OpenSSH\nPort:\n  22/tcp\n')
            return firewall.run(command, **kwargs)

        reconcile_tunnel_firewall(allow_direct=False, sources=['192.168.0.0/24'], command_runner=run)
        self.assertIn(('OpenSSH', 'Anywhere', 'operator ssh rule'), firewall.rules)

    def test_web_profile_is_resolved_and_allowed_only_within_requested_sources(self):
        for source, accepted in (('192.168.0.52', True), ('Anywhere', False)):
            with self.subTest(source=source):
                firewall = FirewallModel()
                firewall.rules.append(('Nginx Full', source, 'operator profile'))

                def run(command, **kwargs):
                    if command[1:3] == ['app', 'info']:
                        return SimpleNamespace(returncode=0, stdout='Profile: Nginx Full\nPorts:\n  80,443/tcp\n')
                    return firewall.run(command, **kwargs)

                if accepted:
                    reconcile_tunnel_firewall(allow_direct=False, sources=['192.168.0.0/24'], command_runner=run)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'outside the requested'):
                        reconcile_tunnel_firewall(allow_direct=False, sources=['192.168.0.0/24'], command_runner=run)

    def test_udp_and_source_specific_management_rules_are_preserved(self):
        firewall = FirewallModel()
        firewall.rules.extend([
            ('80,443/udp', 'Anywhere', 'operator UDP'),
            ('22/tcp', '10.0.0.5', 'operator VPN management'),
        ])
        reconcile_tunnel_firewall(allow_direct=False, sources=['192.168.0.0/24'], command_runner=firewall.run)
        self.assertIn(('80,443/udp', 'Anywhere', 'operator UDP'), firewall.rules)
        self.assertIn(('22/tcp', '10.0.0.5', 'operator VPN management'), firewall.rules)

    def test_uninspectable_profile_is_fatal(self):
        for result in (
            SimpleNamespace(returncode=1, stdout=''),
            SimpleNamespace(returncode=0, stdout='Profile: Nginx Full\n'),
            SimpleNamespace(returncode=0, stdout='Ports:\n  unexpected\n'),
        ):
            with self.subTest(result=result):
                firewall = FirewallModel()
                firewall.rules.append(('Nginx Full', 'Anywhere', 'operator profile'))

                def run(command, **kwargs):
                    if command[1:3] == ['app', 'info']:
                        return result
                    return firewall.run(command, **kwargs)

                with self.assertRaisesRegex(RuntimeError, 'Could not inspect UFW application'):
                    reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=run)

    def test_successful_delete_without_effect_is_detected_after_cleanup(self):
        firewall = FirewallModel()
        firewall.rules.append(('443/tcp (v6)', 'Anywhere (v6)', f'{RULE_PREFIX}443'))

        def run(command, **kwargs):
            if command[1:3] == ['--force', 'delete']:
                return SimpleNamespace(returncode=0, stdout='Rule deleted')
            return firewall.run(command, **kwargs)

        with self.assertRaisesRegex(RuntimeError, 'outside the requested'):
            reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=run)


class TestCloudflareDirectCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()

    def _parse_config(self, argv):
        args = self.parser.parse_args(argv)
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
                self.assertEqual(command[-3:], ['--access-source', '192.168.0.0/24', '--clear-managed-hostnames'])

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

    def test_normal_setup_and_saved_standalone_refresh_preserve_independent_ssh_limits(self):
        for generic_sources in ([], ["192.0.2.0/24"]):
            with self.subTest(generic_sources=generic_sources), TemporaryDirectory() as directory:
                config = self._parse_config([
                    'setup', 'server_web', '192.0.2.52', 'alice', '--cloudflare',
                    '--ssh-access-source', '198.51.100.0/24',
                    *(option for network in generic_sources for option in ('--access-source', network)),
                ])
                saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
                firewall = FirewallModel()
                ssh_rules = [
                    ('22/tcp', '198.51.100.0/24', 'basaltwater SSH trusted source 198.51.100.0/24'),
                    ('2222/tcp', '203.0.113.2', 'operator rate limit'),
                    ('22/tcp', '203.0.113.0/24', 'basaltwater cloudflare ssh 203.0.113.0/24'),
                ]
                firewall.rules.extend(ssh_rules)
                with patch('web.cloudflare_steps.can_manage_firewall', return_value=True), patch(
                    'web.cloudflare_steps.run', side_effect=firewall.run,
                ):
                    configure_cloudflare_firewall(saved)

                credentials = Path(directory, "credentials.json")
                credentials.write_text("{}")
                tunnel = {"name": "example", "id": "id", "credentials_file": str(credentials)}
                sites = [{"hostname": "example.com", "service": "http://localhost:80"}]
                with patch.object(tunnel_setup, "CONFIG_DIR", directory), patch.object(
                    tunnel_setup, "STATE_FILE", str(Path(directory, "tunnel-state.json")),
                ), patch.object(tunnel_setup, "check_root"), patch.object(tunnel_setup, "install_cloudflared"), patch.object(
                    tunnel_setup, "_config_is_current", return_value=True,
                ), patch.object(tunnel_setup, "enabled_nginx_hostnames", return_value={"example.com"}), patch.object(
                    tunnel_setup, "install_and_start_service", return_value=True,
                ), patch.object(tunnel_setup, "can_manage_firewall", return_value=True), patch.object(
                    tunnel_setup, "run_command", side_effect=firewall.run,
                ):
                    tunnel_setup.save_state(tunnel, sites, access_sources=generic_sources, hostnames=["example.com"])
                    self.assertTrue(tunnel_setup.main(interactive=False))

                self.assertEqual([rule for rule in firewall.rules if rule[0] in {"22/tcp", "2222/tcp"}], ssh_rules)
                self.assertFalse(any(
                    {"ssh", "22/tcp", "22", "enable", "default", "limit"}.intersection(command)
                    for command in firewall.commands
                ))

    def test_early_web_setup_preserves_origin_rules_when_the_helper_fails(self):
        for saved, existing_origins in (
            (False, []),
            (True, [('80/tcp', '192.0.2.0/24', f'{RULE_PREFIX}80 source 192.0.2.0/24')]),
            (True, [('443/tcp', '192.0.2.0/24', f'{WEB_RULE_PREFIX} 443 source 192.0.2.0/24')]),
        ):
            with self.subTest(saved=saved, existing_origins=existing_origins):
                config = self._parse_config([
                    'setup', 'server_web', '192.0.2.52', 'alice', '--cloudflare',
                ])
                if saved:
                    config = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
                firewall = FirewallModel(active=saved)
                firewall.rules.extend(existing_origins)
                base = build_server_steps(config)
                firewall_index = next(index for index, (label, _step) in enumerate(base)
                                      if label == "Configuring firewall for web server")
                nginx_index = next(index for index, (_label, step) in enumerate(base)
                                   if step.__name__ == "install_nginx")
                self.assertLess(firewall_index, nginx_index)
                with patch('security.security_steps.can_manage_firewall', return_value=True), patch(
                    'security.security_steps.run', side_effect=firewall.run,
                ):
                    base[firewall_index][1](config)
                self.assertEqual(
                    [rule for rule in firewall.rules if rule[0] in {f"{port}/tcp" for port in ORIGIN_PORTS}],
                    existing_origins,
                )
                self.assertFalse(any(
                    cmd[1:3] in (['allow', '80/tcp'], ['allow', '443/tcp']) for cmd in firewall.commands
                ))
                activate = next(step for label, step in build_post_setup_steps(config, Mock())
                                if label == "Verifying Cloudflare tunnel activation")
                with patch('web.cloudflare_steps.os.path.exists', side_effect=lambda path: (
                    saved or not path.endswith('tunnel-state.json')
                )), patch.dict('os.environ', {'BASALTWATER_SETUP_PROMPT_RELAY': '1'}), patch(
                    'web.cloudflare_steps.run', return_value=SimpleNamespace(returncode=1),
                ) as helper, patch('web.cloudflare_steps.configure_cloudflare_firewall') as late_firewall:
                    with self.assertRaisesRegex(RuntimeError, "activation"):
                        activate(config)
                    late_firewall.assert_not_called()
                self.assertEqual('--non-interactive' in helper.call_args.args[0], saved)
                self.assertEqual(
                    [rule for rule in firewall.rules if rule[0] in {f"{port}/tcp" for port in ORIGIN_PORTS}],
                    existing_origins,
                )

    def test_saved_migration_removes_owned_base_web_rules_after_activation(self):
        firewall = FirewallModel()
        firewall.rules.extend([
            ('80/tcp', 'Anywhere', f'{WEB_RULE_PREFIX} 80'),
            ('443/tcp (v6)', 'fd00::/64', f'{WEB_RULE_PREFIX} 443 source fd00::/64'),
            ('443/tcp (v6)', 'Anywhere (v6)', 'gogs web'),
            ('8443/tcp', 'fd00::/64', f'{WEB_RULE_PREFIX} 8443 source fd00::/64'),
        ])
        reconcile_tunnel_firewall(allow_direct=False, sources=[], command_runner=firewall.run)
        self.assertEqual([rule[0] for rule in firewall.rules], ['3478/udp', '8443/tcp'])

    def test_early_cloudflare_setup_refuses_a_permissive_firewall_default(self):
        from security.security_steps import configure_firewall_web

        config = self._parse_config(['setup', 'server_web', '192.0.2.52', 'alice', '--cloudflare'])
        firewall = FirewallModel(incoming_default='allow')
        with patch('security.security_steps.can_manage_firewall', return_value=True), patch(
            'security.security_steps.run', side_effect=firewall.run,
        ):
            with self.assertRaisesRegex(RuntimeError, 'deny or reject incoming UFW default'):
                configure_firewall_web(config)
        self.assertEqual(firewall.rules, [('3478/udp', 'Anywhere', 'antistatic STUN')])

    def test_incapable_machine_skips_firewall_commands(self):
        config = self._parse_config(['setup', 'server_web', '192.168.0.52', 'alice', '--cloudflare'])
        with patch('web.cloudflare_steps.can_manage_firewall', return_value=False), patch('web.cloudflare_steps.run') as run:
            configure_cloudflare_firewall(config)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
