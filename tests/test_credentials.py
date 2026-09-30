"""Tests for workspace credential storage and runtime resolution."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.credentials import (
    get_runtime_credential,
    list_workspace_credentials,
    load_workspace_credentials,
    prepare_runtime_config,
    remove_workspace_credential,
    set_workspace_credential,
    store_cli_credentials,
)
from lib.workspace import get_credentials_path


class TestWorkspaceCredentials(unittest.TestCase):
    def test_set_list_and_remove_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("alice", "secret1", tmpdir)
            set_workspace_credential("bob", "secret2", tmpdir)

            self.assertEqual(list_workspace_credentials(tmpdir), ["alice", "bob"])
            self.assertEqual(load_workspace_credentials(tmpdir), {"alice": "secret1", "bob": "secret2"})

            self.assertTrue(remove_workspace_credential("alice", tmpdir))
            self.assertFalse(remove_workspace_credential("missing", tmpdir))
            self.assertEqual(list_workspace_credentials(tmpdir), ["bob"])

    def test_load_workspace_credentials_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = get_credentials_path(tmpdir)
            os.makedirs(tmpdir, exist_ok=True)
            with open(credentials_path, "w", encoding="utf-8") as file_obj:
                json.dump({"version": 1, "credentials": {"alice": {"password": "secret1"}}}, file_obj)
            os.chmod(credentials_path, 0o644)

            self.assertEqual(load_workspace_credentials(tmpdir), {"alice": "secret1"})
            self.assertEqual(os.stat(credentials_path).st_mode & 0o777, 0o600)

    def test_credential_links_and_special_files_are_rejected_without_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = get_credentials_path(directory)
            external = os.path.join(directory, 'unrelated.json')
            content = '{"version":1,"credentials":{"alice":{"password":"secret"}}}'
            with open(external, 'w') as stream:
                stream.write(content)
            os.chmod(external, 0o640)
            for kind in ('symlink', 'hardlink', 'fifo', 'directory', 'dangling'):
                with self.subTest(kind=kind):
                    if kind == 'symlink':
                        os.symlink(external, path)
                    elif kind == 'hardlink':
                        os.link(external, path)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    elif kind == 'directory':
                        os.mkdir(path)
                    else:
                        os.symlink(os.path.join(directory, 'missing'), path)
                    with self.assertRaises(ValueError):
                        set_workspace_credential('bob', 'replacement', directory)
                    self.assertEqual(os.stat(external).st_mode & 0o777, 0o640)
                    with open(external) as stream:
                        self.assertEqual(stream.read(), content)
                    self.assertTrue(os.path.lexists(path))
                    if kind == 'directory':
                        os.rmdir(path)
                    else:
                        os.unlink(path)

    def test_invalid_credential_state_blocks_updates_and_retains_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = get_credentials_path(directory)
            for content in ('invalid', '[]', '{"version":true,"credentials":{}}',
                            '{"version":1}', '{"version":1,"credentials":[]}',
                            '{"version":1,"credentials":{" alice":{"password":"secret"}}}',
                            '{"version":1,"credentials":{"alice":{"password":""}}}',
                            ' ' * (1024 * 1024 + 1)):
                with self.subTest(content=content[:80]):
                    with open(path, 'w') as stream:
                        stream.write(content)
                    with self.assertRaises(ValueError):
                        set_workspace_credential('bob', 'replacement', directory)
                    with open(path) as stream:
                        self.assertEqual(stream.read(), content)

    def test_store_cli_credentials_saves_inline_passwords(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                share_credentials=[["guest", "secret1"]],
                samba_shares=[["read", "media", "/mnt/media", "shareuser:secret2"]],
                smb_mounts=[["/mnt/share", "1.2.3.4", "mountuser:secret3", "docs", "/"]],
            )

            store_cli_credentials(config, tmpdir)

            self.assertEqual(
                load_workspace_credentials(tmpdir),
                {"guest": "secret1", "mountuser": "secret3", "shareuser": "secret2"},
            )

    def test_prepare_runtime_config_resolves_workspace_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("guest", "secret1", tmpdir)
            set_workspace_credential("mountuser", "secret2", tmpdir)
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                samba_shares=[["read", "media", "/mnt/media", "guest"]],
                smb_mounts=[["/mnt/share", "1.2.3.4", "mountuser", "docs", "/"]],
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(config.smb_mounts, [["/mnt/share", "1.2.3.4", "mountuser", "docs", "/"]])
            self.assertEqual(runtime_config.share_credentials, [["guest", "secret1"]])
            self.assertEqual(
                runtime_config.smb_mounts,
                [["/mnt/share", "1.2.3.4", "mountuser:secret2", "docs", "/"]],
            )

    def test_prepare_runtime_config_rejects_missing_mount_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                smb_mounts=[["/mnt/share", "1.2.3.4", "mountuser", "docs", "/"]],
            )

            with self.assertRaisesRegex(ValueError, "Missing credential for SMB mount user: mountuser"):
                prepare_runtime_config(config, tmpdir)

    def test_prepare_runtime_config_resolves_antistatic_admin_credential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("operator", "secret1", tmpdir)
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                antistatic_server="lobby.example.com",
                antistatic_admin="operator",
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(runtime_config.share_credentials, [["operator", "secret1"]])

    def test_prepare_runtime_config_rejects_missing_antistatic_admin_credential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                antistatic_server="lobby.example.com",
                antistatic_admin="operator",
            )

            with self.assertRaisesRegex(ValueError, "Missing credential for Antistatic admin: operator"):
                prepare_runtime_config(config, tmpdir)

    def test_prepare_runtime_config_resolves_syncthing_admin_credential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("syncthing-admin", "secret1", tmpdir)
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                enable_syncthing=True,
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(
                runtime_config.share_credentials,
                [["syncthing-admin", "secret1"]],
            )

    def test_prepare_runtime_config_rejects_missing_syncthing_admin_credential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                enable_syncthing=True,
            )

            with self.assertRaisesRegex(
                ValueError,
                "Missing credential for Syncthing admin: syncthing-admin",
            ):
                prepare_runtime_config(config, tmpdir)

    def test_prepare_runtime_config_optionally_resolves_gogs_admin_credential(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("gitadmin", "gogs-secret", tmpdir)
            config = SetupConfig(
                host="host",
                username="gitadmin",
                system_type="server_web",
                gogs=[":3000", "/srv/gogs"],
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(
                get_runtime_credential(runtime_config, "gitadmin"),
                "gogs-secret",
            )

    def test_missing_gogs_admin_credential_keeps_generation_optional(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SetupConfig(
                host="host",
                username="gitadmin",
                system_type="server_web",
                gogs=[":3000", "/srv/gogs"],
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(runtime_config.share_credentials)
            self.assertIsNone(get_runtime_credential(runtime_config, "gitadmin"))

    def test_set_workspace_credential_rejects_separator_username(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "Credential username must not contain ':'"):
                set_workspace_credential("bad:user", "secret1", tmpdir)

            with self.assertRaisesRegex(ValueError, "Credential username must not contain ','"):
                set_workspace_credential("bad,user", "secret1", tmpdir)


if __name__ == "__main__":
    unittest.main()
