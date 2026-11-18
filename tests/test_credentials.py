"""Tests for workspace credential storage and runtime resolution."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.credentials import (
    get_runtime_credential,
    list_workspace_credentials,
    load_workspace_credentials,
    migrate_workspace_credential,
    prepare_runtime_config,
    remove_workspace_credential,
    set_workspace_credential,
    store_cli_credentials,
)
from lib.validation import validate_agent_git_settings
from lib.workspace import get_credentials_path


class TestWorkspaceCredentials(unittest.TestCase):
    def test_set_list_and_remove_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("alice", "secret1", tmpdir)
            set_workspace_credential("bob", "secret2", tmpdir)

            self.assertEqual(list_workspace_credentials(tmpdir), [("legacy", "", "alice"), ("legacy", "", "bob")])
            self.assertEqual(load_workspace_credentials(tmpdir), {
                ("legacy", "", "alice"): "secret1", ("legacy", "", "bob"): "secret2",
            })

            self.assertTrue(remove_workspace_credential("alice", tmpdir))
            self.assertFalse(remove_workspace_credential("missing", tmpdir))
            self.assertEqual(list_workspace_credentials(tmpdir), [("legacy", "", "bob")])

    def test_load_workspace_credentials_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = get_credentials_path(tmpdir)
            os.makedirs(tmpdir, exist_ok=True)
            with open(credentials_path, "w", encoding="utf-8") as file_obj:
                json.dump({"version": 1, "credentials": {"alice": {"password": "secret1"}}}, file_obj)
            os.chmod(credentials_path, 0o644)

            self.assertEqual(load_workspace_credentials(tmpdir), {("legacy", "", "alice"): "secret1"})
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
                            '{"version":1,"credentials":{},"credentials":{}}',
                            '{"version":2,"credentials":{},"scoped_credentials":'
                            '[["gogs","target","alice","one"],["gogs","target","alice","two"]]}',
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
                samba_shares=[["read", "media", "/mnt/media", "guest,shareuser:secret2"]],
                smb_mounts=[["/mnt/share", "1.2.3.4", "mountuser: secret3 ", "docs", "/"]],
            )

            store_cli_credentials(config, tmpdir)

            self.assertEqual(
                load_workspace_credentials(tmpdir),
                {
                    ("samba", "host", "guest"): "secret1",
                    ("samba", "host", "shareuser"): "secret2",
                    ("smb", "//1.2.3.4/docs", "mountuser"): " secret3 ",
                },
            )
            replay = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
            resolved = prepare_runtime_config(replay, tmpdir)
            self.assertEqual(resolved.smb_mounts, config.smb_mounts)

    def test_prepare_runtime_config_resolves_workspace_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            set_workspace_credential("guest", "secret1", tmpdir, service="samba", destination="host")
            set_workspace_credential("mountuser", "secret2", tmpdir, service="smb", destination="//1.2.3.4/docs")
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
            set_workspace_credential("operator", "secret1", tmpdir, service="antistatic", destination="host")
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                antistatic_server="lobby.example.com",
                antistatic_admin="operator",
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(get_runtime_credential(runtime_config, "operator", service="antistatic"), "secret1")

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
            set_workspace_credential("syncthing-admin", "secret1", tmpdir, service="syncthing", destination="host")
            config = SetupConfig(
                host="host",
                username="user",
                system_type="server_lite",
                enable_syncthing=True,
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertIsNone(config.share_credentials)
            self.assertEqual(
                get_runtime_credential(runtime_config, "syncthing-admin", service="syncthing"),
                "secret1",
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
            set_workspace_credential("gitadmin", "gogs-secret", tmpdir, service="gogs", destination="host")
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
            set_workspace_credential("gitadmin", "legacy-secret", tmpdir)
            set_workspace_credential("gitadmin", "other-host-secret", tmpdir, service="gogs", destination="other")
            set_workspace_credential("gitadmin", "samba-secret", tmpdir, service="samba", destination="host")
            config = SetupConfig(
                host="host",
                username="gitadmin",
                system_type="server_web",
                gogs=[":3000", "/srv/gogs"],
                samba_shares=[["read", "docs", "/mnt/docs", "gitadmin"]],
            )

            runtime_config = prepare_runtime_config(config, tmpdir)

            self.assertEqual(runtime_config.share_credentials, [["gitadmin", "samba-secret"]])
            self.assertIsNone(get_runtime_credential(runtime_config, "gitadmin"))

    def test_same_username_isolated_per_origin_service_and_smb_endpoint(self):
        with tempfile.TemporaryDirectory() as workspace:
            first, second = "https://git.example.test", "https://git.example.test:8443"
            for service, destination, password in (
                ("git", "https://GIT.example.test:443/", "git-first"),
                ("git", second, "git-second"),
                ("gogs", "target.example.test", "gogs-only"),
                ("smb", "//NAS.example.test/docs", "smb-docs"),
                ("smb", "//nas.example.test/media", "smb-media"),
                ("smb", "//other.example.test/docs", "smb-other"),
            ):
                set_workspace_credential("alice", password, workspace, service=service, destination=destination)
            config = SetupConfig(
                host="target.example.test", username="alice", system_type="server_web",
                gogs=["git.public.example.test:3000", "/srv/gogs"],
                git_access="read", git_credentials=[[first, "alice"], [second, "alice"]],
                smb_mounts=[
                    ["/mnt/docs", "nas.example.test", "alice", "docs", "/team"],
                    ["/mnt/media", "nas.example.test", "alice", "media", "/"],
                    ["/mnt/other", "other.example.test", "alice", "docs", "/"],
                ],
            )
            saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
            runtime = prepare_runtime_config(saved, workspace)
            parser = create_setup_argument_parser("remote", for_remote=True)
            args = parser.parse_args(shlex.split(" ".join(runtime.to_remote_args())))
            args.host = "localhost"
            remote = SetupConfig.from_args(args, config.system_type)
            validate_agent_git_settings(remote)
            self.assertEqual(get_runtime_credential(remote, "alice", service="git", origin=first), "git-first")
            self.assertEqual(get_runtime_credential(remote, "alice", service="git", origin=second), "git-second")
            self.assertEqual(get_runtime_credential(remote, "alice"), "gogs-only")
            self.assertEqual([spec[2] for spec in remote.smb_mounts], [
                "alice:smb-docs", "alice:smb-media", "alice:smb-other",
            ])
            self.assertNotIn("resolved_credentials", remote.to_dict())
            self.assertNotIn("gogs-only", repr(remote))

    def test_legacy_entry_requires_explicit_copy_and_scope_is_exact(self):
        with tempfile.TemporaryDirectory() as workspace:
            legacy_value = {"version": 1, "credentials": {"alice": {"password": "old-value"}}}
            with open(get_credentials_path(workspace), "w", encoding="utf-8") as store:
                json.dump(legacy_value, store)
            config = SetupConfig(
                host="target.example.test", username="alice", system_type="server_web",
                git_access="read", git_credentials=[["https://git.example.test", "alice"]],
            )
            with self.assertRaisesRegex(ValueError, "credentials migrate alice --service git"):
                prepare_runtime_config(config, workspace)
            migrate_workspace_credential("alice", "git", "https://GIT.example.test:443/", workspace)
            migrate_workspace_credential("alice", "git", "https://git.example.test", workspace)
            self.assertEqual(load_workspace_credentials(workspace), {
                ("legacy", "", "alice"): "old-value",
                ("git", "https://git.example.test", "alice"): "old-value",
            })
            config.git_credentials = [["https://other.example.test", "alice"]]
            with self.assertRaisesRegex(ValueError, "Missing credential for Git user"):
                prepare_runtime_config(config, workspace)
            set_workspace_credential("alice", "new-value", workspace, service="git", destination="https://git.example.test")
            with self.assertRaisesRegex(ValueError, "already exists"):
                migrate_workspace_credential("alice", "git", "https://git.example.test", workspace)

    def test_saved_smb_mount_cannot_use_username_from_another_share_or_service(self):
        with tempfile.TemporaryDirectory() as workspace:
            set_workspace_credential("alice", "old-value", workspace)
            set_workspace_credential("alice", "private-value", workspace, service="smb", destination="//nas/media")
            set_workspace_credential("alice", "git-value", workspace, service="git", destination="https://nas")
            config = SetupConfig(
                host="target", username="alice", system_type="server_lite",
                smb_mounts=[["/mnt/docs", "nas", "alice", "docs", "/"]],
            )
            saved = config.to_dict()
            saved["share_credentials"] = [["alice", "old-inline-value"]]
            replay = SetupConfig.from_dict(config.host, config.system_type, saved)
            self.assertIsNone(replay.share_credentials)
            with self.assertRaisesRegex(ValueError, "credentials migrate alice --service smb --destination //nas/docs"):
                prepare_runtime_config(replay, workspace)

    def test_inline_generic_credential_rejects_multiple_or_missing_consumers(self):
        with tempfile.TemporaryDirectory() as workspace:
            config = SetupConfig(
                host="target.example.test", username="alice", system_type="server_web",
                git_access="read", git_credentials=[["https://git.example.test", "alice"]],
                gogs=[":3000", "/srv/gogs"], share_credentials=[["alice", "fixture-value"]],
            )
            for command in (prepare_runtime_config, store_cli_credentials):
                with self.assertRaisesRegex(ValueError, "multiple destinations or services") as error:
                    command(config, workspace)
                self.assertNotIn("fixture-value", str(error.exception))
            config.share_credentials = [["bob", "fixture-value"]]
            with self.assertRaisesRegex(ValueError, "has no declared consumer"):
                prepare_runtime_config(config, workspace)
            self.assertFalse(os.path.exists(get_credentials_path(workspace)))

    def test_set_workspace_credential_rejects_separator_username(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "Credential username must not contain ':'"):
                set_workspace_credential("bad:user", "secret1", tmpdir)

            with self.assertRaisesRegex(ValueError, "Credential username must not contain ','"):
                set_workspace_credential("bad,user", "secret1", tmpdir)


if __name__ == "__main__":
    unittest.main()
