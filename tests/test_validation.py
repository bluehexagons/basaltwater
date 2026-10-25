"""Tests for lib/validation.py: validate_directory_empty, validate_network_endpoint, validate_positive_integer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.validation import (
    parse_memory_mib,
    validate_apt_packages,
    validate_agent_repositories,
    validate_agent_git_settings,
    validate_antistatic_settings,
    validate_deploy_specs,
    validate_deploy_targets,
    validate_gogs_settings,
    validate_samba_share_specs,
    validate_directory_empty,
    validate_scrub_specs,
    validate_sync_specs,
    validate_network_endpoint,
    validate_positive_integer,
    validate_memory_string,
    validate_package_name,
    validate_hosted_flags,
    validate_smb_mount_specs,
    validate_ssl_email,
    validate_timezone_name,
    validate_workspace_dir,
    validate_vm_storage_settings,
)


class TestValidateDirectoryEmpty(unittest.TestCase):
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            validate_directory_empty(tmpdir)  # should not raise

    def test_non_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, 'file.txt'), 'w') as f:
                f.write('content')
            with self.assertRaises(ValueError):
                validate_directory_empty(tmpdir)

    def test_hidden_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, '.hidden'), 'w') as f:
                f.write('hidden')
            validate_directory_empty(tmpdir)  # hidden files should not count

    def test_not_a_directory(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            with self.assertRaises(ValueError):
                validate_directory_empty(path)
        finally:
            os.unlink(path)

    def test_nonexistent_directory(self):
        with self.assertRaises(ValueError):
            validate_directory_empty('/nonexistent/path/xyz')


class TestValidateNetworkEndpoint(unittest.TestCase):
    def test_valid_ip_port(self):
        validate_network_endpoint('192.168.1.1:8080')  # should not raise

    def test_valid_hostname_port(self):
        validate_network_endpoint('example.com:443')  # should not raise

    def test_empty_endpoint(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('')

    def test_missing_port(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('192.168.1.1')

    def test_port_out_of_range_high(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('192.168.1.1:70000')

    def test_port_out_of_range_zero(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('192.168.1.1:0')

    def test_invalid_host(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('-invalid:80')

    def test_non_numeric_port(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('host:abc')

    def test_multiple_colons(self):
        with self.assertRaises(ValueError):
            validate_network_endpoint('host:80:90')


class TestValidateDeployTargets(unittest.TestCase):
    def test_none_passes(self):
        validate_deploy_targets(None)

    def test_valid_targets_pass(self):
        validate_deploy_targets(['app1.example.com', '192.168.1.20'])

    def test_empty_target_fails(self):
        with self.assertRaisesRegex(ValueError, "Deploy target must be a non-empty hostname or IP"):
            validate_deploy_targets([''])

    def test_invalid_target_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid deploy target host: bad target"):
            validate_deploy_targets(['bad target'])


class TestValidateDeploySpecs(unittest.TestCase):
    def test_none_passes(self):
        validate_deploy_specs(None)

    def test_valid_domain_and_path_specs_pass(self):
        validate_deploy_specs([
            ['example.com/blog,/srv/www/app', 'https://github.com/user/repo.git'],
        ])

    def test_invalid_domain_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid deploy domain: bad domain"):
            validate_deploy_specs([['bad domain', 'https://github.com/user/repo.git']])

    def test_empty_git_url_fails(self):
        with self.assertRaisesRegex(ValueError, "Deploy git URL must be a non-empty string"):
            validate_deploy_specs([['example.com', '']])

    def test_empty_deploy_spec_entry_fails(self):
        with self.assertRaisesRegex(ValueError, "Deploy target spec list must not contain empty entries"):
            validate_deploy_specs([['example.com,,other.example.com', 'https://github.com/user/repo.git']])


class TestValidateAgentRepositories(unittest.TestCase):
    def test_none_passes(self):
        validate_agent_repositories(None)

    def test_valid_urls_pass(self):
        validate_agent_repositories([
            'https://github.com/user/repo.git',
            'https://gitlab.com/user/repo-two.git',
            'https://codeberg.org/user/repo_three.git',
        ])

    def test_ssh_and_non_https_urls_fail(self):
        for url in (
            'ssh://git@github.com/user/repo.git',
            'git@github.com:user/repo.git',
            'http://example.com/user/repo.git',
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "https://"):
                validate_agent_repositories([url])

    def test_empty_url_fails(self):
        with self.assertRaisesRegex(ValueError, "--repo requires a non-empty git URL"):
            validate_agent_repositories([''])

    def test_option_like_url_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid --repo git URL"):
            validate_agent_repositories(['--upload-pack=bad'])

    def test_local_path_fails(self):
        with self.assertRaisesRegex(ValueError, "--repo must be"):
            validate_agent_repositories(['/tmp/repo'])

    def test_unsafe_repo_name_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid --repo repository name"):
            validate_agent_repositories(['https://github.com/user/bad repo.git'])

        for invalid in (
            'https://github.com/user/.',
            'https://github.com/user/..',
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError,
                "Invalid --repo repository name",
            ):
                validate_agent_repositories([invalid])

    def test_surrounding_whitespace_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid --repo git URL"):
            validate_agent_repositories([' https://github.com/user/repo.git'])

    def test_http_urls_with_embedded_credentials_fail(self):
        for url in (
            'https://user:token@example.com/repo.git',
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ValueError,
                "embedded credentials",
            ):
                validate_agent_repositories([url])

    def test_duplicate_repo_name_fails(self):
        with self.assertRaisesRegex(ValueError, "Duplicate --repo repository name: repo"):
            validate_agent_repositories([
                'https://github.com/one/repo.git',
                'https://gitlab.com/two/repo.git',
            ])


class TestValidateGogsSettings(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = {
            "host": "host",
            "username": "agent",
            "system_type": "server_web",
            "gogs": None,
            "gogs_sources": None,
        }
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_none_passes(self):
        validate_gogs_settings(self._make_config())

    def test_valid_domain_and_default_path_pass(self):
        validate_gogs_settings(
            self._make_config(gogs=['git.example.com:3000'], enable_ssl=True)
        )

    def test_valid_domain_and_absolute_data_path_pass(self):
        validate_gogs_settings(
            self._make_config(
                gogs=['git.example.com:3000', '/srv/gogs'],
                enable_cloudflare=True,
            )
        )

    def test_valid_hostless_private_sources_pass(self):
        validate_gogs_settings(
            self._make_config(
                gogs=[':3000', '/srv/gogs'],
                gogs_sources=['192.168.0.0/24', '10.0.0.5'],
            )
        )

    def test_valid_hostless_ssl_private_sources_pass(self):
        validate_gogs_settings(
            self._make_config(
                gogs=[':3000', '/srv/gogs'],
                gogs_sources=['192.168.0.0/24'],
                enable_ssl=True,
            )
        )

    def test_hostless_gogs_rejects_global_generic_source(self):
        with self.assertRaisesRegex(ValueError, "Hostless Gogs access sources"):
            validate_gogs_settings(
                self._make_config(
                    gogs=[':3000', '/srv/gogs'],
                    access_sources=['0.0.0.0/0'],
                )
            )

    def test_hostless_gogs_ignores_generic_ipv6_sources(self):
        validate_gogs_settings(
            self._make_config(
                gogs=[':3000', '/srv/gogs'],
                access_sources=['fc00::/7'],
            )
        )

    def test_hostless_cloudflare_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot use --cloudflare"):
            validate_gogs_settings(
                self._make_config(
                    gogs=[':3000', '/srv/gogs'],
                    enable_cloudflare=True,
                )
            )

    def test_hostname_cloudflare_does_not_apply_generic_source_to_gogs(self):
        validate_gogs_settings(
            self._make_config(
                gogs=['git.example.test:3000', '/srv/gogs'],
                enable_cloudflare=True,
                access_sources=['0.0.0.0/0'],
            )
        )

    def test_ip_address_cannot_be_used_as_gogs_hostname(self):
        with self.assertRaisesRegex(ValueError, "requires a DNS hostname"):
            validate_gogs_settings(
                self._make_config(
                    gogs=['192.168.0.51:3000', '/srv/gogs'],
                    enable_ssl=True,
                )
            )

    def test_port_80_is_reserved_for_nginx_ingress(self):
        with self.assertRaisesRegex(ValueError, "port 80 is reserved"):
            validate_gogs_settings(
                self._make_config(
                    gogs=[':80', '/srv/gogs'],
                    gogs_sources=['192.168.0.0/24'],
                    enable_ssl=True,
                )
            )

    def test_hostless_plaintext_port_80_is_allowed_without_web_server(self):
        validate_gogs_settings(
            self._make_config(
                system_type="server_lite",
                include_web_server=False,
                gogs=[':80', '/srv/gogs'],
                gogs_sources=['192.168.0.0/24'],
            )
        )

    def test_hostname_ssl_rejects_source_restricted_acme_port(self):
        with self.assertRaisesRegex(ValueError, "public port 80"):
            validate_gogs_settings(
                self._make_config(
                    gogs=['git.example.test:3000', '/srv/gogs'],
                    enable_ssl=True,
                    access_sources=['192.168.0.0/24'],
                )
            )

    def test_invalid_argument_count_fails(self):
        with self.assertRaisesRegex(ValueError, "--gogs requires DOMAIN\\[:PORT\\] and optional DATA_PATH"):
            validate_gogs_settings(
                self._make_config(
                    gogs=['git.example.com', '/srv/gogs', 'extra'],
                    enable_ssl=True,
                )
            )

    def test_invalid_domain_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid Gogs domain: bad domain"):
            validate_gogs_settings(
                self._make_config(gogs=['bad domain:3000'], enable_ssl=True)
            )

    def test_relative_data_path_fails(self):
        with self.assertRaisesRegex(ValueError, "Gogs data path must be absolute: relative/path"):
            validate_gogs_settings(
                self._make_config(
                    gogs=['git.example.com', 'relative/path'],
                    enable_ssl=True,
                )
            )

    def test_domain_requires_encrypted_ingress(self):
        with self.assertRaisesRegex(ValueError, "requires --ssl or --cloudflare"):
            validate_gogs_settings(self._make_config(gogs=['git.example.com']))

    def test_sources_require_hostless_gogs(self):
        with self.assertRaisesRegex(ValueError, "requires --gogs"):
            validate_gogs_settings(
                self._make_config(gogs_sources=['192.168.0.0/24'])
            )
        with self.assertRaisesRegex(ValueError, "valid only for hostless"):
            validate_gogs_settings(
                self._make_config(
                    gogs=['git.example.com'],
                    gogs_sources=['192.168.0.0/24'],
                    enable_ssl=True,
                )
            )

    def test_sources_reject_public_ipv4_ipv6_and_duplicates(self):
        for sources, message in (
            (['8.8.8.8'], "private or otherwise non-global"),
            (['fd00::/64'], "only private IPv4"),
            (['10.0.0.1', '10.0.0.1/32'], "Duplicate --gogs-source"),
        ):
            with self.subTest(sources=sources), self.assertRaisesRegex(
                ValueError, message
            ):
                validate_gogs_settings(
                    self._make_config(gogs=[':3000'], gogs_sources=sources)
                )


class TestValidateAgentGitSettings(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = {
            "host": "host",
            "username": "agent",
            "system_type": "server_dev",
            "agent_tools": ["gh"],
        }
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_public_cross_host_policy_passes_without_credentials(self):
        validate_agent_git_settings(
            self._make_config(git_host="gitlab.com", git_access="none")
        )

    def test_github_credentials_require_git_access_policy(self):
        with self.assertRaisesRegex(ValueError, "require --git-access"):
            validate_agent_git_settings(
                self._make_config(git_auth_source="active")
            )

    def test_github_credentials_require_gh(self):
        with self.assertRaisesRegex(ValueError, "requires --agent-tool gh"):
            validate_agent_git_settings(
                self._make_config(
                    agent_tools=["codex"],
                    git_access="read",
                    git_auth_source="active",
                )
            )

    def test_github_credentials_reject_non_github_host(self):
        with self.assertRaisesRegex(ValueError, "only --git-host github.com"):
            validate_agent_git_settings(
                self._make_config(
                    git_access="read",
                    git_host="gitlab.com",
                    git_auth_source="active",
                )
            )

    def test_agent_auth_rejects_tools_without_supported_credentials(self):
        with self.assertRaisesRegex(ValueError, "supported credentials"):
            validate_agent_git_settings(
                self._make_config(
                    agent_tools=[],
                    agent_auth_source="active",
                )
            )

        with self.assertRaisesRegex(ValueError, "supported credentials"):
            validate_agent_git_settings(
                self._make_config(
                    agent_tools=[],
                    agent_auth_files=[["codex", "/run/secrets/codex.json"]],
                )
            )

    def test_malformed_agent_auth_file_is_rejected_as_configuration_error(self):
        for malformed in ("bad-spec", [["codex"], "/run/secrets/codex.json"]):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(
                ValueError, "--agent-auth-file"
            ):
                validate_agent_git_settings(
                    self._make_config(agent_auth_files=[malformed])
                )


class TestValidateAntistaticSettings(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = {
            "host": "host",
            "username": "root",
            "system_type": "server_lite",
            "antistatic_server": "lobby.example.com",
        }
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_server_without_admin_passes(self):
        validate_antistatic_settings(self._make_config())

    def test_invalid_domain_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid Antistatic server domain"):
            validate_antistatic_settings(self._make_config(antistatic_server="bad domain"))

    def test_invalid_port_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid Antistatic server port"):
            validate_antistatic_settings(
                self._make_config(antistatic_server="lobby.example.com:nope")
            )

    def test_admin_requires_server(self):
        with self.assertRaisesRegex(ValueError, "requires --antistatic-server"):
            validate_antistatic_settings(
                self._make_config(antistatic_server=None, antistatic_admin="operator")
            )

    def test_admin_disable_requires_server_spec_for_remote_cleanup(self):
        with self.assertRaisesRegex(ValueError, "requires --antistatic-server"):
            validate_antistatic_settings(
                self._make_config(antistatic_server=None, antistatic_admin="")
            )

    def test_admin_requires_proxy_hostname(self):
        with self.assertRaisesRegex(ValueError, "hostname-based reverse proxy"):
            validate_antistatic_settings(
                self._make_config(
                    antistatic_server=":8080",
                    antistatic_admin="operator",
                    enable_ssl=True,
                    share_credentials=[["operator", "secret1"]],
                )
            )

    def test_admin_requires_tls_ingress(self):
        with self.assertRaisesRegex(ValueError, "requires --ssl or --cloudflare"):
            validate_antistatic_settings(
                self._make_config(
                    antistatic_admin="operator",
                    share_credentials=[["operator", "secret1"]],
                )
            )

    def test_admin_with_ssl_and_credential_passes(self):
        validate_antistatic_settings(
            self._make_config(
                antistatic_admin="operator",
                enable_ssl=True,
                share_credentials=[["operator", "secret1"]],
            )
        )

    def test_admin_password_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "password must not contain control"):
            validate_antistatic_settings(
                self._make_config(
                    antistatic_admin="operator",
                    enable_ssl=True,
                    share_credentials=[["operator", "secret\nvalue"]],
                )
            )


class TestValidateSyncSpecs(unittest.TestCase):
    def test_none_passes(self):
        validate_sync_specs(None)

    def test_valid_sync_specs_pass(self):
        validate_sync_specs([['/src', '/dst', 'daily']])

    def test_relative_source_fails(self):
        with self.assertRaisesRegex(ValueError, "Source path must be absolute: relative"):
            validate_sync_specs([['relative', '/dst', 'daily']])

    def test_invalid_frequency_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid interval 'yearly'"):
            validate_sync_specs([['/src', '/dst', 'yearly']])


class TestValidateScrubSpecs(unittest.TestCase):
    def test_none_passes(self):
        validate_scrub_specs(None)

    def test_valid_scrub_specs_pass(self):
        validate_scrub_specs([['/data', '.pardatabase', '5%', 'weekly']])

    def test_relative_directory_fails(self):
        with self.assertRaisesRegex(ValueError, "Directory path must be absolute: relative"):
            validate_scrub_specs([['relative', '.pardatabase', '5%', 'weekly']])

    def test_invalid_redundancy_fails(self):
        with self.assertRaisesRegex(ValueError, "Redundancy percentage must be between 1 and 100: 0%"):
            validate_scrub_specs([['/data', '.pardatabase', '0%', 'weekly']])


class TestValidateSmbMountSpecs(unittest.TestCase):
    def test_none_passes(self):
        validate_smb_mount_specs(None)

    def test_valid_smb_mount_specs_pass(self):
        validate_smb_mount_specs([['/mnt/share', '192.168.1.10', 'user:pass', 'docs', '/sub']])

    def test_smb_mountpoint_must_be_under_mnt(self):
        with self.assertRaisesRegex(ValueError, "below /mnt"):
            validate_smb_mount_specs([['/etc', '192.168.1.10', 'user:pass', 'docs', '/']])

    def test_smb_mountpoint_must_be_normalized_and_unique(self):
        with self.assertRaisesRegex(ValueError, "normalized"):
            validate_smb_mount_specs([['/mnt/projects/..', '192.168.1.10', 'user:pass', 'docs', '/']])
        with self.assertRaisesRegex(ValueError, "Duplicate SMB mountpoint"):
            validate_smb_mount_specs([
                ['/mnt/projects', '192.168.1.10', 'user:pass', 'docs', '/'],
                ['/mnt/projects', '192.168.1.11', 'user:pass', 'archive', '/'],
            ])

    def test_invalid_host_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid SMB mount host: bad host"):
            validate_smb_mount_specs([['/mnt/share', 'bad host', 'user:pass', 'docs', '/sub']])

    def test_invalid_share_name_fails(self):
        with self.assertRaisesRegex(ValueError, r"Invalid share name .*bad/share"):
            validate_smb_mount_specs([['/mnt/share', '192.168.1.10', 'user:pass', 'bad/share', '/sub']])

    def test_invalid_subdir_fails(self):
        with self.assertRaisesRegex(ValueError, "Subdirectory must start with /: subdir"):
            validate_smb_mount_specs([['/mnt/share', '192.168.1.10', 'user:pass', 'docs', 'subdir']])

    def test_control_characters_in_credentials_fail(self):
        with self.assertRaisesRegex(ValueError, "SMB mount password must not contain control"):
            validate_smb_mount_specs([['/mnt/share', '192.168.1.10', 'user:pass\nvalue', 'docs', '/']])

    def test_empty_password_fails(self):
        with self.assertRaisesRegex(ValueError, "credentials must include a non-empty"):
            validate_smb_mount_specs([['/mnt/share', '192.168.1.10', 'user:', 'docs', '/']])


class TestValidateSambaShareSpecs(unittest.TestCase):
    def test_none_passes(self):
        validate_samba_share_specs(None)

    def test_valid_share_specs_pass(self):
        validate_samba_share_specs([['read', 'docs', '/mnt/docs', 'shareuser']], [['shareuser', 'secret']])

    def test_relative_path_fails(self):
        with self.assertRaisesRegex(ValueError, "Share path must be absolute: relative"):
            validate_samba_share_specs([['read', 'docs', 'relative', 'shareuser:secret']])

    def test_invalid_share_name_fails(self):
        with self.assertRaisesRegex(ValueError, r"Invalid Samba share name .*bad/share"):
            validate_samba_share_specs([['read', 'bad/share', '/mnt/docs', 'shareuser:secret']])

    def test_duplicate_share_names_and_root_path_fail(self):
        with self.assertRaisesRegex(ValueError, "Duplicate Samba share name"):
            validate_samba_share_specs([
                ['read', 'docs', '/srv/docs', 'shareuser:secret'],
                ['write', 'docs', '/srv/docs-write', 'shareuser:secret'],
            ])
        with self.assertRaisesRegex(ValueError, "must not be the filesystem root"):
            validate_samba_share_specs([['write', 'root', '/', 'shareuser:secret']])

    def test_missing_credential_fails(self):
        with self.assertRaisesRegex(ValueError, "Missing credential for share user: shareuser"):
            validate_samba_share_specs([['read', 'docs', '/mnt/docs', 'shareuser']])

    def test_control_characters_in_share_name_fail(self):
        with self.assertRaisesRegex(ValueError, "Samba share name must not contain control"):
            validate_samba_share_specs([['read', 'docs\nother', '/mnt/docs', 'shareuser:secret']])

    def test_config_syntax_in_share_name_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid Samba share name"):
            validate_samba_share_specs(
                [["read", "docs]", "/mnt/docs", "shareuser:secret"]]
            )

    def test_share_name_that_exceeds_group_limit_fails(self):
        with self.assertRaisesRegex(ValueError, "too long for its Unix group"):
            validate_samba_share_specs(
                [["read", "a" * 24, "/mnt/docs", "shareuser:secret"]]
            )


class TestValidateWorkspaceDir(unittest.TestCase):
    def test_existing_workspace_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            validate_workspace_dir(tmpdir)

    def test_new_nested_workspace_directory_uses_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            validate_workspace_dir(os.path.join(tmpdir, 'nested', 'workspace'))

    def test_workspace_path_rejects_existing_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
            path = tmpfile.name
        try:
            with self.assertRaises(ValueError):
                validate_workspace_dir(path)
        finally:
            os.unlink(path)


class TestValidateSslEmail(unittest.TestCase):
    def test_none_passes(self):
        validate_ssl_email(None)

    def test_valid_email_passes(self):
        validate_ssl_email("admin@example.com")

    def test_invalid_email_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid SSL email address: bad-email"):
            validate_ssl_email("bad-email")


class TestValidateTimezoneName(unittest.TestCase):
    def test_none_passes(self):
        validate_timezone_name(None)

    def test_valid_timezone_passes(self):
        validate_timezone_name("UTC")

    def test_invalid_timezone_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid timezone: Mars/Olympus"):
            validate_timezone_name("Mars/Olympus")


class TestValidatePositiveInteger(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_positive_integer('42'), 42)

    def test_valid_with_spaces(self):
        self.assertEqual(validate_positive_integer('  7  '), 7)

    def test_zero_not_positive(self):
        with self.assertRaises(ValueError):
            validate_positive_integer('0')

    def test_negative(self):
        with self.assertRaises(ValueError):
            validate_positive_integer('-5')

    def test_empty(self):
        with self.assertRaises(ValueError):
            validate_positive_integer('')

    def test_non_numeric(self):
        with self.assertRaises(ValueError):
            validate_positive_integer('abc')

    def test_custom_name_in_error(self):
        with self.assertRaises(ValueError) as ctx:
            validate_positive_integer('0', name='count')
        self.assertIn('count', str(ctx.exception))


class TestValidateMemoryString(unittest.TestCase):
    def test_valid_gigabytes(self):
        validate_memory_string('2G')  # should not raise

    def test_valid_megabytes(self):
        validate_memory_string('512M')  # should not raise

    def test_valid_kilobytes(self):
        validate_memory_string('1024K')  # should not raise

    def test_valid_terabytes(self):
        validate_memory_string('1T')  # should not raise

    def test_case_insensitive(self):
        validate_memory_string('2g')  # should not raise

    def test_fractional_gigabytes_convert_exactly(self):
        self.assertEqual(parse_memory_mib('1.5G'), 1536)
        self.assertEqual(parse_memory_mib('0.5G'), 512)
        validate_memory_string('1.25G')

    def test_fraction_must_resolve_to_whole_mebibytes(self):
        with self.assertRaisesRegex(ValueError, 'whole MiB'):
            validate_memory_string('1.1G')
        with self.assertRaisesRegex(ValueError, 'whole MiB'):
            validate_memory_string('0.5M')

    def test_invalid_suffix(self):
        with self.assertRaises(ValueError):
            validate_memory_string('2GB')

    def test_no_suffix(self):
        with self.assertRaises(ValueError):
            validate_memory_string('512')

    def test_empty_string(self):
        with self.assertRaises(ValueError):
            validate_memory_string('')

    def test_letters_only(self):
        with self.assertRaises(ValueError):
            validate_memory_string('abc')

    def test_custom_name_in_error(self):
        with self.assertRaises(ValueError) as ctx:
            validate_memory_string('bad', name='--storage')
        self.assertIn('--storage', str(ctx.exception))


class TestValidatePackageName(unittest.TestCase):
    def test_valid_package_name(self):
        self.assertEqual(validate_package_name('python3-venv'), 'python3-venv')

    def test_valid_package_name_with_plus(self):
        self.assertEqual(validate_package_name('libgtk-3-0t64+extra'), 'libgtk-3-0t64+extra')

    def test_empty_package_name(self):
        with self.assertRaises(ValueError):
            validate_package_name('')

    def test_invalid_package_name(self):
        with self.assertRaises(ValueError):
            validate_package_name('python3; rm -rf /')


class TestValidateAptPackages(unittest.TestCase):
    def test_none_passes(self):
        validate_apt_packages(None)

    def test_valid_packages_pass(self):
        validate_apt_packages(["python3-venv", "curl"])

    def test_invalid_package_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid --apt-install name: python3; rm -rf /"):
            validate_apt_packages(["python3; rm -rf /"])


class _MockConfig:
    """Minimal mock for SetupConfig used by validate_hosted_flags."""
    def __init__(self, **kwargs):
        self.host = kwargs.get('host', '10.0.0.50')
        self.username = kwargs.get('username', 'user')
        self.machine_type = kwargs.get('machine_type')
        self.ssh_key = kwargs.get('ssh_key')
        self.static_ipv4 = kwargs.get('static_ipv4')
        self.hosted_node = kwargs.get('hosted_node')
        self.hosted_bridge = kwargs.get('hosted_bridge')
        self.container_memory = kwargs.get('container_memory')
        self.vm_balloon_min = kwargs.get('vm_balloon_min')
        self.container_storage = kwargs.get('container_storage')
        self.storage_mounts = kwargs.get('storage_mounts')
        self.storage_caches = kwargs.get('storage_caches')
        self.agent_workspace = kwargs.get('agent_workspace')
        self.container_cores = kwargs.get('container_cores', 1)
        self.vm_image = kwargs.get('vm_image')
        self.vm_image_sha512 = kwargs.get('vm_image_sha512')
        self.vm_image_storage = kwargs.get('vm_image_storage')


class TestValidateHostedFlags(unittest.TestCase):
    def test_no_hosted_node_passes(self):
        config = _MockConfig(hosted_node=None)
        validate_hosted_flags(config)  # should not raise

    def test_valid_config(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
            container_cores=2,
        )
        validate_hosted_flags(config)  # should not raise

    def test_hosted_vm_requires_guest_public_key(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaisesRegex(ValueError, r'requires an SSH identity'):
            validate_hosted_flags(config)

    def test_hosted_vm_rejects_hostname_target_without_static_ip(self):
        config = _MockConfig(
            host='vm.example.com',
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key, 'w', encoding='utf-8') as private_key:
                private_key.write('private')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            with self.assertRaisesRegex(ValueError, r'literal IPv4'):
                validate_hosted_flags(config)

    def test_hosted_vm_accepts_key_and_ipv4_target(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='1.5G',
            vm_balloon_min='0.5G',
            container_storage=[['root', 'auto', '10G']],
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key, 'w', encoding='utf-8') as private_key:
                private_key.write('private')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            validate_hosted_flags(config)

    def test_hosted_vm_rejects_public_key_without_private_key(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            with self.assertRaisesRegex(ValueError, r'readable SSH private key'):
                validate_hosted_flags(config)

    def test_image_storage_requires_hosted_provisioning(self):
        config = _MockConfig(hosted_node=None, vm_image_storage='local')
        with self.assertRaisesRegex(ValueError, r'requires --provision-on'):
            validate_hosted_flags(config)

    def test_image_storage_cannot_override_image_storage_reference(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
            vm_image='local:import/debian.qcow2',
            vm_image_storage='local',
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key, 'w', encoding='utf-8') as private_key:
                private_key.write('private')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            with self.assertRaisesRegex(ValueError, r'applies to downloaded'):
                validate_hosted_flags(config)

    def test_custom_vm_image_requires_sha512(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
            vm_image='https://example.com/custom.qcow2',
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key, 'w', encoding='utf-8') as private_key:
                private_key.write('private')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            with self.assertRaisesRegex(ValueError, r'require --image-sha512'):
                validate_hosted_flags(config)

    def test_custom_vm_image_rejects_invalid_sha512(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
            vm_image='https://example.com/custom.qcow2',
            vm_image_sha512='not-a-hash',
        )
        with tempfile.TemporaryDirectory() as tmp:
            config.ssh_key = os.path.join(tmp, 'id_test')
            with open(config.ssh_key, 'w', encoding='utf-8') as private_key:
                private_key.write('private')
            with open(config.ssh_key + '.pub', 'w', encoding='utf-8') as pubkey:
                pubkey.write('ssh-ed25519 AAAA test\n')
            with self.assertRaisesRegex(ValueError, r'128 hexadecimal'):
                validate_hosted_flags(config)

    def test_missing_memory(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory=None,
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('--memory', str(ctx.exception))

    def test_missing_storage(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=None,
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('--storage', str(ctx.exception))

    def test_named_storage_requires_mount(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['bad', 'auto', '10G']],
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('requires --storage-mount', str(ctx.exception))

    def test_invalid_memory_format(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2GB',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaises(ValueError):
            validate_hosted_flags(config)

    def test_fractional_memory_is_accepted(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='1.5G',
            container_storage=[['root', 'auto', '10G']],
        )

        validate_hosted_flags(config)

    def test_balloon_min_must_not_exceed_vm_memory(self):
        config = _MockConfig(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_memory='2G',
            vm_balloon_min='3G',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaisesRegex(ValueError, r'cannot exceed --memory'):
            validate_hosted_flags(config)

    def test_balloon_min_is_vm_only(self):
        config = _MockConfig(
            machine_type='unprivileged',
            hosted_node='10.0.0.1',
            container_memory='2G',
            vm_balloon_min='1G',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaisesRegex(ValueError, r'requires --machine vm'):
            validate_hosted_flags(config)

    def test_balloon_min_requires_hosted_provisioning(self):
        config = _MockConfig(hosted_node=None, vm_balloon_min='1G')
        with self.assertRaisesRegex(ValueError, r'requires --provision-on'):
            validate_hosted_flags(config)

    def test_invalid_hosted_node_host(self):
        config = _MockConfig(
            hosted_node='bad host',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaisesRegex(ValueError, "Invalid Proxmox node host: bad host"):
            validate_hosted_flags(config)

    def test_named_sdn_bridge_is_accepted(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            hosted_bridge='sdn-public',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        validate_hosted_flags(config)

    def test_invalid_bridge_name_is_rejected(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            hosted_bridge='bad bridge',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
        )
        with self.assertRaisesRegex(ValueError, "Invalid network interface"):
            validate_hosted_flags(config)

    def test_invalid_storage_amount_for_root(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', 'bad']],
        )
        with self.assertRaises(ValueError):
            validate_hosted_flags(config)

    def test_template_storage_omits_amount(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
        )
        validate_hosted_flags(config)  # should not raise

    def test_template_storage_with_amount_rejected(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local', 'ignored']],
        )
        with self.assertRaises(ValueError):
            validate_hosted_flags(config)

    def test_named_path_storage_requires_mount(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['path', 'auto', '10G']],
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('requires --storage-mount', str(ctx.exception))

    def test_zero_cores_rejected(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G']],
            container_cores=0,
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('cores', str(ctx.exception).lower())

    def test_duplicate_storage_types_rejected(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G'], ['root', 'local', '20G']],
        )
        with self.assertRaises(ValueError):
            validate_hosted_flags(config)

    def test_storage_too_few_elements(self):
        config = _MockConfig(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=['root', 'auto'],
        )
        with self.assertRaises(ValueError) as ctx:
            validate_hosted_flags(config)
        self.assertIn('TYPE POOL AMOUNT', str(ctx.exception))


class TestValidateVMStorageSettings(unittest.TestCase):
    def test_valid_named_data_disks_and_mounts(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[
                ['root', 'local-lvm', '32G'],
                ['git-data', 'bulk-lvm', '128G'],
                ['agent-data', 'bulk-lvm', '128G'],
                ['archive-data', 'bulk-lvm', '128G'],
            ],
            storage_mounts=[
                ['git-data', '/srv/gogs', 'ext4'],
                ['agent-data', '/srv/agent-workspace', 'xfs', 'empty'],
                ['archive-data', '/srv/archive', 'btrfs', 'empty'],
            ],
            agent_workspace='/srv/agent-workspace',
        )

        validate_vm_storage_settings(config, require_provisioning=False)

    def test_unknown_mount_filesystem_is_rejected(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['data', 'bulk-lvm', '32G']],
            storage_mounts=[['data', '/srv/data', 'zfs']],
        )

        with self.assertRaisesRegex(
            ValueError, 'filesystem must be ext4, xfs, or btrfs'
        ):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_valid_lvm_block_cache_consumes_unmounted_cache_disk(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[
                ['data', 'ts1-storage', '3T'],
                ['data-cache', 'local-lvm', '128G'],
            ],
            storage_mounts=[['data', '/srv/data', 'xfs']],
            storage_caches=[['data', 'data-cache']],
        )

        validate_vm_storage_settings(config, require_provisioning=False)

    def test_cache_rejects_unknown_reused_or_mounted_cache_disks(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[
                ['data', 'ts1-storage', '3T'],
                ['cache', 'local-lvm', '32G'],
            ],
            storage_mounts=[
                ['data', '/srv/data'],
                ['cache', '/srv/cache'],
            ],
            storage_caches=[['data', 'cache']],
        )
        with self.assertRaisesRegex(ValueError, 'must not use --storage-mount'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.storage_mounts = [['data', '/srv/data']]
        config.storage_caches = [['data', 'missing']]
        with self.assertRaisesRegex(ValueError, 'unknown VM data disk'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.storage_caches = [
            ['data', 'cache'],
            ['cache', 'data'],
        ]
        with self.assertRaisesRegex(ValueError, 'both cache data and cache media'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_cache_mode_and_minimum_size_are_validated(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[
                ['data', 'ts1-storage', '3T'],
                ['cache', 'local-lvm', '256M'],
            ],
            storage_mounts=[['data', '/srv/data']],
            storage_caches=[['data', 'cache', 'unsafe-mode']],
        )
        with self.assertRaisesRegex(ValueError, 'mode must be'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.storage_caches = [['data', 'cache']]
        with self.assertRaisesRegex(ValueError, 'at least 512M'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_unknown_disk_and_duplicate_mounts_are_rejected(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['agent-data', 'local-lvm', '32G']],
            storage_mounts=[['other-data', '/srv/agent-workspace']],
        )
        with self.assertRaisesRegex(ValueError, 'missing: agent-data'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.storage_mounts = [
            ['agent-data', '/srv/one'],
            ['agent-data', '/srv/two'],
        ]
        with self.assertRaisesRegex(ValueError, 'Duplicate --storage-mount'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_home_mount_is_allowed_for_a_new_vm(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['one', 'local-lvm', '32G']],
            storage_mounts=[['one', '/home']],
        )

        config.hosted_node = '10.0.0.1'
        validate_vm_storage_settings(config, require_provisioning=True)

    def test_unsafe_and_overlapping_mount_paths_are_rejected(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['one', 'local-lvm', '32G']],
            storage_mounts=[['one', '/srv/data']],
        )

        config.container_storage = [
            ['one', 'local-lvm', '32G'],
            ['two', 'local-lvm', '32G'],
        ]
        config.storage_mounts = [
            ['one', '/srv/data'],
            ['two', '/srv/data/nested'],
        ]
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.container_storage = [['one', 'local-lvm', '32G']]
        config.storage_mounts = [['one', '/srv/data%specifier']]
        with self.assertRaisesRegex(ValueError, 'components may contain only'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_migrate_policy_and_lxc_data_disks_are_rejected(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['home-data', 'local-lvm', '32G']],
            storage_mounts=[['home-data', '/srv/home-data', 'ext4', 'migrate']],
        )
        with self.assertRaisesRegex(ValueError, 'only the empty policy'):
            validate_vm_storage_settings(config, require_provisioning=False)

        config.machine_type = 'unprivileged'
        config.storage_mounts = [['home-data', '/srv/home-data']]
        with self.assertRaisesRegex(ValueError, 'require --machine vm'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_logical_name_length_preserves_complete_serial(self):
        config = _MockConfig(
            machine_type='vm',
            container_storage=[['this-name-is-far-too-long', 'local-lvm', '32G']],
            storage_mounts=[['this-name-is-far-too-long', '/srv/data']],
        )
        with self.assertRaisesRegex(ValueError, 'at most 17 characters'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_agent_workspace_rejects_broad_system_path(self):
        config = _MockConfig(
            username='agent',
            machine_type='vm',
            agent_workspace='/etc/agent-workspace',
        )

        with self.assertRaisesRegex(ValueError, 'approved tool-owned path'):
            validate_vm_storage_settings(config, require_provisioning=False)

    def test_agent_workspace_accepts_user_home_or_tool_owned_path(self):
        for path in ('/home/agent/repos', '/srv/agent-workspace'):
            with self.subTest(path=path):
                config = _MockConfig(
                    username='agent',
                    machine_type='vm',
                    agent_workspace=path,
                )
                validate_vm_storage_settings(config, require_provisioning=False)


if __name__ == '__main__':
    unittest.main()
