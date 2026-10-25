"""Focused tests for the target-side user migration helper."""

from __future__ import annotations

import json
import os
import pwd
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from lib import user_rename


class TestUserRenameHelpers(unittest.TestCase):
    def test_subordinate_ids_update_only_exact_first_fields_and_preserve_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = {name: Path(tmpdir, name) for name in ("subuid", "subgid")}
            original = "user:100000:65536\notheruser:200000:65536\nuser:300000:65536\nnew:400000:65536"
            expected = "new:100000:65536\notheruser:200000:65536\nnew:300000:65536\nnew:400000:65536"
            for path in paths.values():
                path.write_text(original)
                path.chmod(0o640)
            real_open, real_stat = open, os.stat

            def mapped(path):
                return paths[os.path.basename(path)] if path in {"/etc/subuid", "/etc/subgid"} else path

            atomic_write = user_rename.write_text_atomic
            with (
                patch("builtins.open", side_effect=lambda path, *a, **kw: real_open(mapped(path), *a, **kw)),
                patch.object(user_rename.os, "stat", side_effect=lambda path, **kw: real_stat(mapped(path), **kw)),
                patch.object(user_rename.os.path, "islink", return_value=False),
                patch.object(user_rename, "write_text_atomic", side_effect=lambda path, content, **kw: atomic_write(mapped(path), content, **kw)) as write,
            ):
                user_rename._rewrite_subordinate_id_files("user", "new")
                user_rename._rewrite_subordinate_id_files("user", "new")
                user_rename._verify_subordinate_id_files("user")
            self.assertEqual(write.call_count, 2)
            for call in write.call_args_list:
                self.assertEqual(call.kwargs, dict(mode=0o640, uid=os.getuid(), gid=os.getgid()))
            for path in paths.values():
                self.assertEqual(path.read_text(), expected)
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_overlapping_names_rewrite_units_home_files_and_credentials_idempotently(self):
        for new_username in ("alice2", "alice-admin"):
            with self.subTest(new_username=new_username), tempfile.TemporaryDirectory() as tmpdir:
                old_home = str(Path(tmpdir, "alice"))
                new_home = str(Path(tmpdir, new_username))
                launcher = Path(new_home, ".local/bin/launch")
                service = Path(new_home, ".config/systemd/user/t3code.service")
                for path in (launcher, service):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"exec {old_home}/bin/t3\n# {old_home}other/unchanged\n")
                credentials = Path(tmpdir, "credentials")
                credentials.mkdir()
                old_credential = credentials / "credentials-mnt-alice"
                old_credential.write_text("username=remote-alice\n")
                unit = Path(tmpdir, "basaltwater-t3code.service")
                unit.write_text(
                    f"User=alice\nGroup=alice\nWorkingDirectory={old_home}\n"
                    f"Options=uid=alice,gid=alice,credentials={old_credential}\n"
                    "# User=otheralice alice2 alice-admin\n"
                )
                manifest = {"old_username": "alice", "new_username": new_username,
                            "new_home": new_home, "managed_units": [{"path": str(unit)}]}
                with patch.object(user_rename, "SMB_CREDENTIAL_DIR", str(credentials)):
                    for _ in range(2):
                        user_rename._rename_managed_credentials(manifest)
                        user_rename._rewrite_managed_units(manifest, old_home, new_home)
                        user_rename._rewrite_managed_home_files(old_home, new_home)
                        user_rename._verify_managed_rewrites(manifest, old_home)
                self.assertEqual(unit.read_text(),
                    f"User={new_username}\nGroup={new_username}\nWorkingDirectory={new_home}\n"
                    f"Options=uid={new_username},gid={new_username},credentials={credentials}/credentials-mnt-{new_username}\n"
                    "# User=otheralice alice2 alice-admin\n")
                for path in (launcher, service):
                    self.assertEqual(path.read_text(), f"exec {new_home}/bin/t3\n# {old_home}other/unchanged\n")
                self.assertTrue((credentials / f"credentials-mnt-{new_username}").is_file())

    def test_sudoers_overlap_retries_do_not_rename_other_principals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = Path(tmpdir, "basaltwater-alice")
            new_path = Path(tmpdir, "basaltwater-alice2")
            old_path.write_text("alice ALL=(ALL) NOPASSWD: ALL\notheralice ALL=(ALL) ALL\n")
            real_join = os.path.join
            with (
                patch.object(user_rename.os.path, "join", side_effect=lambda root, *parts: real_join(tmpdir if root == "/etc/sudoers.d" else root, *parts)),
                patch.object(user_rename, "_run", return_value=Mock(returncode=0)),
            ):
                user_rename._rewrite_sudoers("alice", "alice2")
                user_rename._rewrite_sudoers("alice", "alice2")
            self.assertEqual(new_path.read_text(), "alice2 ALL=(ALL) NOPASSWD: ALL\notheralice ALL=(ALL) ALL\n")

    def test_all_managed_home_files_protect_overlapping_destinations_on_two_passes(self):
        for suffix in (" archive", "@archive"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmpdir:
                old_home = str(Path(tmpdir, "alice"))
                new_home = old_home + suffix
                paths = [Path(new_home, relative) for relative in (
                    ".local/bin/launch",
                    ".local/share/basaltwater/nested/launch",
                    ".local/share/t3code/nested/launch",
                    ".config/systemd/user/t3code.service",
                    ".config/systemd/user/t3code.service.d/basaltwater.conf",
                )]
                original = (f'exec "{old_home}/bin/t3"\n'
                            f'# existing "{new_home}/bin/t3"\n'
                            f'# {old_home}other/unchanged\n')
                expected = original.replace(f'"{old_home}/bin/t3"', f'"{new_home}/bin/t3"')
                for path in paths:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(original)
                manifest = {"old_username": "alice", "new_username": "alice2", "new_home": new_home}
                with patch.object(user_rename.os, "chown"):
                    self.assertEqual(set(user_rename._rewrite_managed_home_files(old_home, new_home)),
                                     {str(path) for path in paths})
                    for path in paths:
                        self.assertEqual(path.read_text(), expected)
                    user_rename._verify_managed_rewrites(manifest, old_home)
                    self.assertEqual(user_rename._rewrite_managed_home_files(old_home, new_home), [])
                    for path in paths:
                        self.assertEqual(path.read_text(), expected)
                    user_rename._verify_managed_rewrites(manifest, old_home)

    def test_verification_rejects_exact_stale_identity_but_accepts_overlaps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir, "unit.service")
            manifest = {"old_username": "alice", "new_username": "alice2",
                        "new_home": "/home/alice2", "managed_units": [{"path": str(path)}]}
            for stale in ("User=alice\n", "Group=alice\n", "WorkingDirectory=/home/alice/bin\n"):
                with self.subTest(stale=stale):
                    path.write_text("User=alice2\n" + stale)
                    with self.assertRaisesRegex(user_rename.RenameError, "still references"):
                        user_rename._verify_managed_rewrites(manifest, "/home/alice")

    def test_mountpoint_changes_are_rejected_without_touching_mount_pair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mount = Path(tmpdir, "home-alice-share.mount")
            automount = Path(tmpdir, "home-alice-share.automount")
            mount.write_text(
                "[Mount]\nWhere=/home/alice/share\n"
                "Options=uid=alice,credentials=/root/.smb/credentials-home-alice-share\n"
            )
            automount.write_text("[Automount]\nWhere=/home/alice/share\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed, unmanaged = user_rename._managed_unit_files("alice", "/home/alice")
            self.assertEqual(managed, [str(automount), str(mount)])
            self.assertEqual(unmanaged, [])
            with self.assertRaisesRegex(user_rename.RenameError, "--keep-home.*mount/automount"):
                user_rename._reject_mount_moves([str(mount)], "alice", "alice2", "/home/alice", "/home/alice2")
            user_rename._reject_mount_moves(managed, "alice", "alice2", "/home/alice", "/home/alice")
            self.assertIn("Where=/home/alice/share", mount.read_text())
            self.assertEqual(automount.read_text(), "[Automount]\nWhere=/home/alice/share\n")

    def test_unmanaged_systemd_dropins_vendor_fragments_and_symlinks_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local, vendor = Path(tmpdir, "local"), Path(tmpdir, "vendor")
            local.mkdir()
            vendor.mkdir()
            expected = []
            for root, name, content in (
                (local, "custom.service.d/override.conf", "[Service]\nUser=alice\n"),
                (local, "basaltwater-t3code.service.d/local.conf", "Group=alice\n"),
                (vendor, "service.d/defaults.conf", "WorkingDirectory=/home/alice/repos\n"),
                (vendor, "custom.service", "User=alice\n"),
            ):
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_text(content)
                expected.append(str(path))
            safe = local / "safe.service.d/safe.conf"
            safe.parent.mkdir()
            safe.write_text("User=alice2\nWorkingDirectory=/home/alice2\n")
            linked = local / "linked.service"
            linked.symlink_to(vendor / "custom.service")
            expected.append(str(linked))
            with patch.object(user_rename, "SYSTEMD_DIR", str(local)), patch.object(user_rename, "SYSTEMD_EXTRA_DIRS", (str(vendor),)):
                self.assertEqual(user_rename._unsupported_systemd_references("alice", "/home/alice"), sorted(expected))

    def test_preflight_rejects_mounts_dropins_and_rootless_storage_before_cutover(self):
        for blocker in ("mount", "dropin", "rootless"):
            with self.subTest(blocker=blocker), tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
                root = Path(tmpdir)
                home, units = root / "alice", root / "units"
                home.mkdir()
                units.mkdir()
                if blocker == "mount":
                    (units / "home-alice-share.mount").write_text(
                        f"Where={home}/share\nOptions=uid=alice,credentials=/root/.smb/credentials-home-alice-share\n"
                    )
                    expected_error = "mountpoint would change"
                elif blocker == "dropin":
                    dropin = units / "custom.service.d/override.conf"
                    dropin.parent.mkdir()
                    dropin.write_text("User=alice\n")
                    expected_error = "Unmanaged systemd.*override.conf"
                else:
                    database = home / ".local/share/containers/storage/libpod/bolt_state.db"
                    database.parent.mkdir(parents=True)
                    database.touch()
                    expected_error = "Rootless container storage.*--keep-home"
                account = pwd.struct_passwd(("alice", "x", 1000, 1000, "", str(home), "/bin/bash"))
                real_stat, real_exists = os.stat, os.path.exists
                for name, value in (("SYSTEMD_DIR", str(units)), ("SYSTEMD_EXTRA_DIRS", ())):
                    stack.enter_context(patch.object(user_rename, name, value))
                stack.enter_context(patch.object(user_rename, "_require_root"))
                stack.enter_context(patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", ()))
                stack.enter_context(patch.object(user_rename, "_reject_concurrent_operation"))
                stack.enter_context(patch.object(user_rename, "_account", return_value=account))
                stack.enter_context(patch.object(user_rename, "_is_local_account", return_value=True))
                stack.enter_context(patch.object(user_rename.pwd, "getpwnam", side_effect=KeyError))
                stack.enter_context(patch.object(user_rename.grp, "getgrnam", side_effect=KeyError))
                stack.enter_context(patch.object(user_rename.grp, "getgrgid", return_value=Mock(gr_name="alice")))
                stack.enter_context(patch.object(user_rename.os, "stat", side_effect=lambda path, **kw: Mock(st_uid=1000, st_mode=real_stat(path, **kw).st_mode) if path == str(home) else real_stat(path, **kw)))
                stack.enter_context(patch.object(user_rename.os.path, "ismount", return_value=False))
                stack.enter_context(patch.object(user_rename.os.path, "exists", side_effect=lambda path: path == "/run/systemd/system" or real_exists(path)))
                stack.enter_context(patch.object(user_rename.shutil, "which", return_value="/usr/bin/systemctl"))
                run = stack.enter_context(patch.object(user_rename, "_run"))
                backup = stack.enter_context(patch.object(user_rename, "_backup_files"))
                marker = stack.enter_context(patch.object(user_rename, "_write_marker"))
                with self.assertRaisesRegex(user_rename.RenameError, expected_error):
                    user_rename._preflight({"operation_id": "0123456789abcdef", "old_username": "alice", "new_username": "alice2"})
                run.assert_not_called()
                backup.assert_not_called()
                marker.assert_not_called()

    def test_rootless_storage_absent_and_empty_allowed_but_any_entry_blocks_move(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", ()):
            home = Path(tmpdir, "alice")
            home.mkdir()
            store = home / ".local/share/containers/storage"
            guard = lambda: user_rename._reject_rootless_container_home_move(str(home), str(Path(tmpdir, "alice2")), "alice")
            with patch.object(user_rename, "_run") as run:
                guard()
                store.mkdir(parents=True)
                guard()
                for name in ("libpod", "db.sql", "overlay-images", "storage.lock", "unknown-metadata"):
                    with self.subTest(name=name):
                        entry = store / name
                        entry.mkdir() if name == "libpod" else entry.touch()
                        with self.assertRaisesRegex(user_rename.RenameError, "Rootless container storage.*--keep-home"):
                            guard()
                        user_rename._reject_rootless_container_home_move(str(home), str(home), "alice")
                        entry.rmdir() if entry.is_dir() else entry.unlink()
                run.assert_not_called()

    def test_rootless_storage_configured_paths_are_checked_without_reading_app_data(self):
        cases = ("user-graphroot", "system-rootless", "xdg-data", "xdg-config", "explicit-config", "external-store")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmpdir:
                home = Path(tmpdir, "alice")
                home.mkdir()
                store = home / "custom-store"
                configs = []
                environment = home / ".config/environment.d/10-storage.conf"
                config = home / ".config/containers/storage.conf"
                contents = '[storage]\ngraphroot = "${HOME}/custom-store"\n'
                if case == "system-rootless":
                    config = Path(tmpdir, "system/storage.conf")
                    configs = [str(config)]
                    contents = f'[storage]\nrootless_storage_path = "{store}"\n'
                elif case == "xdg-data":
                    environment.parent.mkdir(parents=True)
                    environment.write_text('XDG_DATA_HOME="${HOME}/data"\n')
                    store = home / "data/containers/storage"
                elif case == "xdg-config":
                    environment.parent.mkdir(parents=True)
                    environment.write_text('XDG_CONFIG_HOME=$HOME/settings\n')
                    config = home / "settings/containers/storage.conf"
                elif case == "explicit-config":
                    environment.parent.mkdir(parents=True)
                    environment.write_text('CONTAINERS_STORAGE_CONF=$HOME/custom/storage.conf\n')
                    config = home / "custom/storage.conf"
                elif case == "external-store":
                    store = Path(tmpdir, "external-store")
                    contents = f'[storage]\ngraphroot = "{store}"\n'
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text(contents if case != "xdg-data" else "")
                store.mkdir(parents=True)
                (store / "db.sql").touch()
                auth = home / ".config/containers/auth.json"
                auth.parent.mkdir(parents=True, exist_ok=True)
                auth.touch()
                real_open = os.open
                opened = []

                def observed_open(path, flags, *args, **kwargs):
                    opened.append(os.fspath(path))
                    self.assertNotIn(os.path.basename(path), {"auth.json", "db.sql"})
                    return real_open(path, flags, *args, **kwargs)

                with (
                    patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", tuple(configs)),
                    patch.object(user_rename.os, "open", side_effect=observed_open),
                    patch.object(user_rename, "_run") as run,
                ):
                    if case == "external-store":
                        user_rename._reject_rootless_container_home_move(str(home), str(home) + "2", "alice")
                    else:
                        with self.assertRaisesRegex(user_rename.RenameError, "Rootless container storage.*custom-store|Rootless container storage.*data/containers/storage"):
                            user_rename._reject_rootless_container_home_move(str(home), str(home) + "2", "alice")
                    run.assert_not_called()
                self.assertIn("storage.conf", opened)

    def test_ambiguous_rootless_configuration_fails_closed_and_keep_home_skips_reads(self):
        for case in ("toml", "oversize", "relative", "unknown-variable", "environment", "environment-escape", "environment-limit", "environment-conflict", "engine", "dropins", "symlink", "fifo", "store-symlink", "auth-override", "unreadable"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmpdir:
                home = Path(tmpdir, "alice")
                config = home / ".config/containers/storage.conf"
                config.parent.mkdir(parents=True)
                contents = {
                    "toml": "[storage", "oversize": "#" * (64 * 1024 + 1),
                    "relative": '[storage]\ngraphroot="relative"',
                    "unknown-variable": '[storage]\ngraphroot="$UNKNOWN/store"',
                }
                if case in contents:
                    config.write_text(contents[case])
                elif case in {"environment", "environment-escape", "environment-limit", "environment-conflict", "auth-override"}:
                    environment = home / ".config/environment.d/10-storage.conf"
                    environment.parent.mkdir()
                    environment.write_text({
                        "environment": 'XDG_DATA_HOME=$(command)\n',
                        "environment-escape": 'XDG_DATA_HOME=$HOME/path\\ name\n',
                        "environment-limit": "",
                        "environment-conflict": 'XDG_DATA_HOME=$HOME/first\nXDG_DATA_HOME=$HOME/second\n',
                        "auth-override": 'CONTAINERS_STORAGE_CONF=$HOME/.config/containers/auth.json\n',
                    }[case])
                    if case == "environment-limit":
                        for index in range(64):
                            (environment.parent / f"extra-{index}.conf").touch()
                elif case == "engine":
                    config.with_name("containers.conf").write_text("[engine]\n")
                elif case == "dropins":
                    config.with_name("storage.conf.d").mkdir()
                elif case == "symlink":
                    config.symlink_to(home / "missing")
                elif case == "fifo":
                    os.mkfifo(config)
                elif case == "store-symlink":
                    store = home / ".local/share/containers/storage"
                    store.parent.mkdir(parents=True)
                    store.symlink_to(home / "missing")
                with (
                    patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", ()),
                    patch.object(user_rename, "_run") as run,
                    ExitStack() as stack,
                ):
                    if case == "unreadable":
                        stack.enter_context(patch.object(user_rename, "_container_config_text", side_effect=PermissionError("unreadable config")))
                    with self.assertRaisesRegex(user_rename.RenameError, "Cannot safely rule out.*--keep-home"):
                        user_rename._reject_rootless_container_home_move(str(home), str(home) + "2", "alice")
                    with patch.object(user_rename, "_container_config_text", side_effect=AssertionError("keep-home must skip inspection")):
                        user_rename._reject_rootless_container_home_move(str(home), str(home), "alice")
                    run.assert_not_called()

    def test_interrupted_pre_cutover_resume_preserves_original_shell_units_and_backups(self):
        for phase in ("prepared", "quiescing", "identity-starting"):
            with self.subTest(phase=phase):
                self._exercise_resume(phase)

    def test_resume_failure_restores_durable_original_shell(self):
        self._exercise_resume("identity-starting", fail_identity=True)

    def test_fresh_login_and_home_cutover_uses_one_native_command(self):
        self._exercise_resume(None, move_home=True)

    def test_partial_cutover_requiesces_live_renamed_account_and_only_moves_missing_home(self):
        for phase in ("identity-renamed", "identity-starting"):
            for home_moved in (False, True):
                with self.subTest(phase=phase, home_moved=home_moved):
                    self._exercise_resume(phase, move_home=True, renamed=True, home_moved=home_moved)

    def test_partial_cutover_rootless_storage_blocks_pending_move_but_allows_historical_move(self):
        for phase in ("identity-renamed", "identity-starting"):
            for home_moved in (False, True):
                with self.subTest(phase=phase, home_moved=home_moved):
                    self._exercise_resume(phase, move_home=True, renamed=True, home_moved=home_moved, container_store=True)
        self._exercise_resume("identity-renamed", renamed=True, container_store=True)

    def test_partial_cutover_checks_both_user_expansions_before_pending_home_move(self):
        for store_username in ("alice", "alice2"):
            with self.subTest(store_username=store_username), tempfile.TemporaryDirectory() as tmpdir:
                home, new_home = Path(tmpdir, "alice"), Path(tmpdir, "alice2")
                config = home / ".config/containers/storage.conf"
                config.parent.mkdir(parents=True)
                config.write_text('[storage]\ngraphroot = "$HOME/store-$USER"\n')
                store = home / f"store-{store_username}"
                store.mkdir()
                (store / "db.sql").touch()
                account = pwd.struct_passwd(("alice2", "x", os.getuid(), os.getgid(), "", str(home), "/bin/bash"))
                manifest = dict(old_username="alice", new_username="alice2", old_uid=os.getuid(),
                                old_gid=os.getgid(), old_shell="/bin/bash", old_home=str(home), new_home=str(new_home))
                real_open = os.open

                def observed_open(path, flags, *args, **kwargs):
                    self.assertNotEqual(os.path.basename(path), "db.sql", "storage guard must not read databases")
                    return real_open(path, flags, *args, **kwargs)

                def lookup(username):
                    if username == "alice2":
                        return account
                    raise user_rename.RenameError("missing")

                with (
                    patch.object(user_rename, "_account", side_effect=lookup),
                    patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", ()),
                    patch.object(user_rename.os, "open", side_effect=observed_open),
                    patch.object(user_rename, "_run") as run,
                ):
                    with self.assertRaisesRegex(user_rename.RenameError, f"Rootless container storage would move.*store-{store_username}"):
                        user_rename._validate_renamed_account(manifest)
                    run.assert_not_called()
                self.assertTrue(home.is_dir())
                self.assertFalse(new_home.exists())

    def test_rejected_external_shell_drift_does_not_run_usermod_or_stop_services(self):
        for renamed in (False, True):
            with self.subTest(renamed=renamed):
                self._exercise_resume("identity-renamed" if renamed else "prepared", renamed=renamed, drift="shell")

    def test_partial_identity_or_home_drift_is_rejected_before_mutation(self):
        for drift in ("uid", "gid", "home", "both-homes", "missing-home"):
            with self.subTest(drift=drift):
                self._exercise_resume("identity-renamed", move_home=True, renamed=True, drift=drift)

    def test_later_resume_rejects_identity_drift_before_any_service_restoration(self):
        for phase in ("configuration-updated", "services-reconciled", "verified"):
            for drift in ("uid", "gid", "home", "shell", "temporary-shell", "old-name",
                          "old-home", "missing-home", "home-owner", "machine", "setup",
                          "missing-machine", "missing-setup"):
                with self.subTest(phase=phase, drift=drift):
                    self._exercise_resume(phase, move_home=True, renamed=True, home_moved=True, drift=drift)

    def test_later_resume_with_matching_identity_preserves_service_snapshot(self):
        for phase in ("configuration-updated", "services-reconciled", "verified"):
            with self.subTest(phase=phase):
                self._exercise_resume(phase, move_home=True, renamed=True, home_moved=True)

    def _exercise_resume(self, phase, *, fail_identity=False, move_home=False, renamed=False, home_moved=False, drift=None, container_store=False):
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            root = Path(tmpdir)
            home = root / "home"
            home.mkdir()
            new_home = root / "alice2" if move_home else home
            if home_moved:
                home.rename(new_home)
            if container_store:
                store = (new_home if home_moved else home) / ".local/share/containers/storage/libpod"
                store.mkdir(parents=True)
                (store / "bolt_state.db").touch()
            if drift == "both-homes":
                new_home.mkdir()
            if drift == "missing-home":
                (new_home if home_moved else home).rmdir()
            operation_id = "0123456789abcdef"
            units = [{"path": str(root / "unit.service"), "unit": "unit.service", "kind": "service", "enabled": True, "active": True}]
            facts = dict(old_uid=os.getuid(), old_gid=os.getgid(), old_shell="/bin/bash", old_home=str(home), new_home=str(new_home),
                         primary_group="users", rename_primary_group=False, managed_units=units,
                         machine_state_present=False, setup_config_present=False, linger_enabled=False)
            manifest = dict(operation_id=operation_id, old_username="alice", new_username="alice2", keep_home=not move_home, **facts)
            later_phase = phase in {"configuration-updated", "services-reconciled", "verified"}
            if later_phase:
                manifest.update(machine_state_present=True, setup_config_present=True)
                for name, missing, changed in (("machine", "missing-machine", "machine"),
                                                ("setup", "missing-setup", "setup")):
                    if drift != missing:
                        (root / f"{name}.json").write_text(json.dumps({
                            "username": "alice" if drift == changed else "alice2",
                        }))
            events = []
            initial_name = "alice2" if renamed else "alice"
            accounts = {initial_name: pwd.struct_passwd((
                initial_name, "x", os.getuid() + int(drift == "uid"), os.getgid() + int(drift == "gid"), "",
                str(root / "external") if drift == "home" else str(home if drift == "old-home" else new_home if home_moved else home),
                "/bin/zsh" if drift == "shell" else "/usr/sbin/nologin" if drift == "temporary-shell" else "/bin/bash" if renamed or phase is None else "/usr/sbin/nologin",
            ))}
            if drift == "old-name":
                accounts["alice"] = pwd.struct_passwd(("alice", *accounts[initial_name][1:]))
            live = True

            def account(username):
                if username not in accounts:
                    raise user_rename.RenameError("missing")
                return accounts[username]

            def run(argv, **_kwargs):
                nonlocal live
                events.append(tuple(argv))
                if argv[:2] == ["systemctl", "stop"] and argv[-1].startswith("user@"):
                    live = False
                if argv[:2] == ["usermod", "-l"]:
                    self.assertFalse(live, "native rename must follow user manager termination")
                    if fail_identity:
                        raise user_rename.RenameError("identity interrupted")
                    old = accounts.pop("alice")
                    accounts["alice2"] = pwd.struct_passwd(("alice2", *old[1:]))
                if argv[:1] == ["usermod"] and "-d" in argv:
                    self.assertFalse(live, "native home move must follow user manager termination")
                    home.rename(new_home)
                    old = accounts["alice2"]
                    accounts["alice2"] = pwd.struct_passwd((*old[:5], str(new_home), old.pw_shell))
                if argv[:2] == ["usermod", "-s"]:
                    old = accounts[argv[-1]]
                    accounts[argv[-1]] = pwd.struct_passwd((*old[:-1], argv[2]))
                return Mock(returncode=0)

            for name, value in (("RENAME_ROOT", str(root / "operations")), ("STATE_FILE", str(root / "machine.json")),
                                ("SETUP_CONFIG_FILE", str(root / "setup.json")), ("LINGER_DIR", str(root / "linger"))):
                stack.enter_context(patch.object(user_rename, name, value))
            Path(user_rename._operation_dir(operation_id)).mkdir(parents=True)
            if phase is not None:
                user_rename._write_marker(manifest, phase)
            stack.enter_context(patch.object(user_rename, "_require_root"))
            stack.enter_context(patch.object(user_rename, "_CONTAINER_SYSTEM_STORAGE_CONFIGS", ()))
            stack.enter_context(patch.object(user_rename, "_account", side_effect=account))
            stack.enter_context(patch.object(user_rename, "_run", side_effect=run))
            if drift == "home-owner":
                real_stat = os.stat

                def changed_owner(path, **kwargs):
                    metadata = real_stat(path, **kwargs)
                    if path == str(new_home):
                        fields = list(metadata)
                        fields[4] = os.getuid() + 1
                        return os.stat_result(fields)
                    return metadata

                stack.enter_context(patch.object(user_rename.os, "stat", side_effect=changed_owner))
            current = {**facts, "old_shell": accounts[initial_name].pw_shell,
                       "managed_units": [{**units[0], "active": False, "enabled": False}] if phase else units}
            preflight = stack.enter_context(patch.object(user_rename, "_preflight", side_effect=lambda _m: events.append("preflight") or current))
            backup = stack.enter_context(patch.object(user_rename, "_backup_files"))
            stack.enter_context(patch.object(user_rename, "_processes_for_uid", side_effect=lambda _uid: [12345] if live else []))
            stack.enter_context(patch.object(user_rename.os, "kill", side_effect=AssertionError("manager stop must drain fixture processes")))
            effects = {}
            for name in ("_rename_cron_and_mail", "_rename_linger", "_rewrite_group_memberships", "_rewrite_state_files",
                         "_rewrite_subordinate_id_files", "_rewrite_sudoers", "_rename_managed_credentials", "_rewrite_managed_units",
                         "_rewrite_managed_home_files", "_restore_t3_user_service", "_verify_managed_rewrites", "_verify_group_memberships",
                         "_verify_subordinate_id_files", "_cleanup_operation"):
                effects[name] = stack.enter_context(patch.object(user_rename, name))
            storage_blocked = container_store and move_home and not home_moved
            self.assertEqual(user_rename._run_migration_locked(manifest if phase is None else {"operation_id": operation_id}), int(fail_identity or bool(drift) or storage_blocked))
            if renamed:
                preflight.assert_not_called()
            else:
                preflight.assert_called_once()
            self.assertEqual(backup.call_count, int(phase is None))
            saved = user_rename._read_json(user_rename._marker_path(operation_id))
            self.assertEqual(saved["old_shell"], "/bin/bash")
            self.assertEqual(saved["managed_units"], units)
            if drift or storage_blocked:
                self.assertEqual(events, [] if renamed else ["preflight"])
                effects["_restore_t3_user_service"].assert_not_called()
                effects["_cleanup_operation"].assert_not_called()
                self.assertEqual(accounts[initial_name].pw_shell, current["old_shell"])
                if later_phase:
                    status = user_rename._read_json(user_rename._status_path(operation_id))
                    self.assertEqual(status["status"], "failed")
                    expected_error = {
                        "uid": "UID changed", "gid": "GID changed",
                        "home": "home does not match", "old-home": "home does not match",
                        "shell": "shell changed", "temporary-shell": "shell changed",
                        "old-name": "Old account name still exists",
                        "missing-home": "home is not a regular directory",
                        "home-owner": "home is not owned",
                        "machine": "machine.json was not updated", "missing-machine": "machine.json was not updated",
                        "setup": "setup.json was not updated", "missing-setup": "setup.json was not updated",
                    }[drift]
                    self.assertIn(expected_error, status["error"])
                if storage_blocked:
                    status = user_rename._read_json(user_rename._status_path(operation_id))
                    self.assertIn("Rootless container storage would move", status["error"])
                return
            if later_phase:
                expected = [("systemctl", "daemon-reload"), ("systemctl", "enable", "unit.service"),
                            ("systemctl", "start", "unit.service")] if phase == "configuration-updated" else []
                self.assertEqual(events, expected)
                self.assertEqual(effects["_restore_t3_user_service"].call_count, int(phase == "configuration-updated"))
                effects["_cleanup_operation"].assert_called_once_with(operation_id)
                return
            if not renamed:
                self.assertLess(events.index("preflight"), events.index(("systemctl", "stop", "unit.service")))
            shell_change = ("usermod", "-s", "/usr/sbin/nologin", initial_name)
            manager_stop = ("systemctl", "stop", f"user@{os.getuid()}.service")
            self.assertLess(events.index(("systemctl", "stop", "unit.service")), events.index(shell_change))
            self.assertLess(events.index(shell_change), events.index(manager_stop))
            identity_commands = [event for event in events if isinstance(event, tuple) and event[0] == "usermod" and ("-l" in event or "-d" in event)]
            expected = [] if renamed else [("usermod", "-l", "alice2", *(["-d", str(new_home), "-m"] if move_home else []), "alice")]
            if renamed and move_home and not home_moved:
                expected = [("usermod", "-d", str(new_home), "-m", "alice2")]
            self.assertEqual(identity_commands, expected)
            for command in identity_commands:
                self.assertLess(events.index(manager_stop), events.index(command))
            final_name = "alice" if fail_identity else "alice2"
            self.assertEqual(accounts[final_name].pw_shell, "/bin/bash")
            if not fail_identity:
                self.assertIn(("systemctl", "enable", "unit.service"), events)
                self.assertIn(("systemctl", "start", "unit.service"), events)

    def test_resume_revalidation_rejects_identity_or_inventory_drift(self):
        original = dict(old_uid=1000, old_shell="/bin/bash", managed_units=[])
        for current in ({**original, "old_uid": 2000}, {**original, "managed_units": [{"path": "/tmp/new", "unit": "new.service", "kind": "service"}]}):
            with self.subTest(current=current), patch.object(user_rename, "_preflight", return_value=current):
                with self.assertRaises(user_rename.RenameError):
                    user_rename._revalidate_pre_cutover(original)
            self.assertEqual(original, dict(old_uid=1000, old_shell="/bin/bash", managed_units=[]))

    def test_resume_attempt_is_durable_and_worker_status_acknowledges_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = {"operation_id": "0123456789abcdef"}
            with (
                patch.object(user_rename, "RENAME_ROOT", tmpdir),
                patch.object(user_rename, "_require_root"),
                patch.object(user_rename, "_run", return_value=Mock(returncode=3)),
            ):
                user_rename._write_status(manifest["operation_id"], "identity-renamed", "failed")
                user_rename._prepare_resume(manifest, "fedcba9876543210")
                queued = user_rename._read_json(user_rename._status_path(manifest["operation_id"]))
                self.assertEqual((queued["attempt_id"], queued["status"]), ("fedcba9876543210", "in_progress"))
                user_rename._write_status(manifest["operation_id"], "starting", "in_progress")
                user_rename._write_status(manifest["operation_id"], "complete", "success")
                complete = user_rename._read_json(user_rename._status_path(manifest["operation_id"]))
                self.assertEqual(complete["attempt_id"], "fedcba9876543210")

    def test_resume_cannot_replace_an_active_workers_attempt_or_status(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(user_rename, "RENAME_ROOT", tmpdir):
            with patch.object(user_rename, "_require_root"), patch.object(user_rename, "_run", return_value=Mock(returncode=0)):
                with self.assertRaisesRegex(user_rename.RenameError, "already active"):
                    user_rename._prepare_resume({"operation_id": "0123456789abcdef"}, "fedcba9876543210")
            self.assertFalse(Path(user_rename._attempt_path("0123456789abcdef")).exists())

    def test_second_worker_cannot_overwrite_running_status(self):
        with (
            patch.object(user_rename, "_require_root"),
            patch.object(user_rename, "_acquire_target_lock", side_effect=user_rename.RenameError("already running")),
            patch.object(user_rename, "_write_status") as write,
        ):
            self.assertEqual(user_rename._run_migration({"operation_id": "0123456789abcdef"}), 1)
        write.assert_not_called()

    def test_rewrite_setup_config_updates_username_and_home_paths(self):
        config = {
            "username": "olduser",
            "agent_workspace": "/home/olduser/repos",
            "sync_specs": [["/home/olduser/data", "/srv/backup", "daily"]],
            "share_credentials": [["olduser", "secret"]],
            "notify_specs": [["webhook", "/home/olduser/should-not-change"]],
            "gogs": ["git.example.com:3000", "/home/olduser/gogs"],
        }

        updated = user_rename._rewrite_setup_config(
            config,
            "olduser",
            "newuser",
            "/home/olduser",
            "/home/newuser",
        )

        self.assertEqual(updated["username"], "newuser")
        self.assertEqual(updated["agent_workspace"], "/home/newuser/repos")
        self.assertEqual(updated["sync_specs"][0][0], "/home/newuser/data")
        self.assertEqual(updated["share_credentials"][0][0], "olduser")
        self.assertEqual(updated["notify_specs"][0][1], "/home/olduser/should-not-change")
        self.assertEqual(updated["gogs"][1], "/home/newuser/gogs")

    def test_managed_unit_detection_rejects_unknown_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            managed = os.path.join(tmpdir, "auto-update-node.service")
            unmanaged = os.path.join(tmpdir, "custom.service")
            with open(managed, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with open(unmanaged, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [managed])
        self.assertEqual(unmanaged_paths, [unmanaged])

    def test_syncthing_service_is_a_managed_unit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            managed = os.path.join(tmpdir, "basaltwater-syncthing.service")
            with open(managed, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [managed])
        self.assertEqual(unmanaged_paths, [])

    def test_codex_auth_service_and_timer_are_managed_together(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = os.path.join(tmpdir, "codex-auth-maintenance.service")
            timer = os.path.join(tmpdir, "codex-auth-maintenance.timer")
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write("User=olduser\nEnvironment=HOME=/home/olduser\n")
            with open(timer, "w", encoding="utf-8") as file_obj:
                file_obj.write("OnCalendar=daily\n")
            with patch.object(user_rename, "SYSTEMD_DIR", tmpdir):
                managed_paths, unmanaged_paths = user_rename._managed_unit_files(
                    "olduser",
                    "/home/olduser",
                )

        self.assertEqual(managed_paths, [service, timer])
        self.assertEqual(unmanaged_paths, [])

    def test_managed_unit_rewrite_protects_old_username_in_new_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            unit_path = os.path.join(tmpdir, "auto-update-node.service")
            with open(unit_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("WorkingDirectory=/srv/olduser-data\nUser=olduser\n")
            manifest = {
                "old_username": "olduser",
                "new_username": "newuser",
                "new_home": "/srv/olduser-data",
                "managed_units": [{"path": unit_path}],
            }

            user_rename._rewrite_managed_units(
                manifest,
                "/home/olduser",
                "/srv/olduser-data",
            )
            with open(unit_path, encoding="utf-8") as file_obj:
                content = file_obj.read()
            user_rename._verify_managed_rewrites(manifest, "/home/olduser")

        self.assertIn("WorkingDirectory=/srv/olduser-data", content)
        self.assertIn("User=newuser", content)

    def test_t3_user_service_paths_follow_a_moved_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_home = "/home/olduser"
            new_home = os.path.join(tmpdir, "newuser")
            service_dir = os.path.join(new_home, ".config", "systemd", "user")
            drop_in_dir = os.path.join(service_dir, "t3code.service.d")
            os.makedirs(drop_in_dir)
            service = os.path.join(service_dir, "t3code.service")
            drop_in = os.path.join(drop_in_dir, "basaltwater.conf")
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write(f"ExecStart={old_home}/.t3/runtime/service-launcher.mjs\n")
            with open(drop_in, "w", encoding="utf-8") as file_obj:
                file_obj.write(f"WorkingDirectory={old_home}/repos\n")

            changed = user_rename._rewrite_managed_home_files(old_home, new_home)

            self.assertEqual(set(changed), {service, drop_in})
            for path in (service, drop_in):
                with open(path, encoding="utf-8") as file_obj:
                    content = file_obj.read()
                self.assertIn(new_home, content)
                self.assertNotIn(old_home, content)

    def test_t3_user_service_restarts_through_renamed_account(self):
        with tempfile.TemporaryDirectory() as home:
            service = os.path.join(
                home,
                ".config",
                "systemd",
                "user",
                "t3code.service",
            )
            os.makedirs(os.path.dirname(service))
            with open(service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# upstream managed\n")
            completed = type("Completed", (), {"returncode": 0})()
            with patch.object(user_rename, "_run", return_value=completed) as run:
                user_rename._restore_t3_user_service("newuser", 1000, home)

            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0], ["systemctl", "start", "user@1000.service"])
            self.assertIn("newuser", commands[1])
            self.assertEqual(
                commands[-1][-3:],
                ["is-active", "--quiet", "t3code.service"],
            )

    def test_home_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_home = os.path.join(tmpdir, "real-home")
            linked_home = os.path.join(tmpdir, "linked-home")
            os.mkdir(real_home)
            os.symlink(real_home, linked_home)
            account = pwd.struct_passwd(
                ("olduser", "x", os.getuid(), os.getgid(), "", linked_home, "/bin/bash")
            )

            with self.assertRaisesRegex(user_rename.RenameError, "symlink"):
                user_rename._home_for_manifest(
                    {
                        "old_username": "olduser",
                        "new_username": "newuser",
                        "keep_home": True,
                    },
                    account,
                )

    def test_managed_smb_credentials_stop_at_mount_option_comma(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credential_dir = os.path.join(tmpdir, "credentials")
            os.mkdir(credential_dir)
            old_credential = os.path.join(credential_dir, "credentials-home-olduser")
            new_credential = os.path.join(credential_dir, "credentials-home-newuser")
            with open(old_credential, "w", encoding="utf-8") as file_obj:
                file_obj.write("username=shareuser\npassword=secret\n")
            unit_path = os.path.join(tmpdir, "home-olduser.mount")
            with open(unit_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    f"Options=credentials={credential_dir}/credentials-home-olduser,"
                    "uid=olduser,gid=olduser\n"
                )
            manifest = {
                "old_username": "olduser",
                "new_username": "newuser",
                "managed_units": [{"path": unit_path}],
            }

            with patch.object(user_rename, "SMB_CREDENTIAL_DIR", credential_dir):
                user_rename._rename_managed_credentials(manifest)

            self.assertFalse(os.path.exists(old_credential))
            self.assertTrue(os.path.isfile(new_credential))

    def test_cron_and_mail_rename_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cron_dir = os.path.join(tmpdir, "cron")
            mail_dir = os.path.join(tmpdir, "mail")
            os.mkdir(cron_dir)
            os.mkdir(mail_dir)
            for directory in (cron_dir, mail_dir):
                with open(os.path.join(directory, "olduser"), "w", encoding="utf-8"):
                    pass

            with (
                patch.object(user_rename, "CRON_DIR", cron_dir),
                patch.object(user_rename, "MAIL_DIRS", (mail_dir,)),
                patch.object(user_rename.os, "chown"),
            ):
                user_rename._rename_cron_and_mail("olduser", "newuser", os.getuid())
                user_rename._rename_cron_and_mail("olduser", "newuser", os.getuid())

            self.assertTrue(os.path.isfile(os.path.join(cron_dir, "newuser")))
            self.assertTrue(os.path.isfile(os.path.join(mail_dir, "newuser")))

    def test_crontab_collision_is_rejected_before_cutover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cron_dir = os.path.join(tmpdir, "cron")
            os.mkdir(cron_dir)
            with open(os.path.join(cron_dir, "newuser"), "w", encoding="utf-8"):
                pass

            with (
                patch.object(user_rename, "CRON_DIR", cron_dir),
                patch.object(user_rename, "MAIL_DIRS", ()),
            ):
                with self.assertRaisesRegex(user_rename.RenameError, "already exists"):
                    user_rename._preflight_cron_and_mail("olduser", "newuser")

    def test_failure_restores_shell_using_new_account_name(self):
        renamed_account = pwd.struct_passwd(
            ("newuser", "x", 1000, 1000, "", "/home/newuser", "/usr/sbin/nologin")
        )

        def account(username):
            if username == "newuser":
                return renamed_account
            raise user_rename.RenameError("missing")

        with (
            patch.object(user_rename, "_account", side_effect=account),
            patch.object(user_rename, "_run") as run,
        ):
            user_rename._restore_login_shell_after_failure(
                {
                    "old_username": "olduser",
                    "new_username": "newuser",
                    "old_uid": 1000,
                    "old_gid": 1000,
                    "old_shell": "/bin/bash",
                }
            )

        run.assert_called_once_with(
            ["usermod", "-s", "/bin/bash", "newuser"],
            check=False,
        )

    def test_failure_shell_restore_preserves_external_shell_or_identity_changes(self):
        for uid, gid, shell in ((1000, 1000, "/bin/zsh"), (2000, 1000, "/usr/sbin/nologin"), (1000, 2000, "/usr/sbin/nologin")):
            with self.subTest(uid=uid, gid=gid, shell=shell):
                account = pwd.struct_passwd(("newuser", "x", uid, gid, "", "/home/newuser", shell))
                with (
                    patch.object(user_rename, "_account", return_value=account),
                    patch.object(user_rename, "_run") as run,
                ):
                    user_rename._restore_login_shell_after_failure({
                        "old_username": "olduser", "new_username": "newuser", "old_uid": 1000,
                        "old_gid": 1000, "old_shell": "/bin/bash",
                    })
                run.assert_not_called()

    def test_preflight_rejects_root(self):
        account = pwd.struct_passwd(
            ("root", "x", 0, 0, "root", "/root", "/bin/bash")
        )
        with (
            patch.object(user_rename, "_require_root"),
            patch.object(user_rename, "_reject_concurrent_operation"),
            patch.object(user_rename, "_account", return_value=account),
        ):
            with self.assertRaisesRegex(user_rename.RenameError, "root"):
                user_rename._preflight(
                    {
                        "operation_id": "0123456789abcdef",
                        "old_username": "root",
                        "new_username": "newuser",
                    }
                )

    def test_status_is_atomic_and_non_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                user_rename._write_status(
                    "0123456789abcdef",
                    "prepared",
                    "in_progress",
                    details={"old_home": "/home/olduser"},
                )
                status_path = os.path.join(
                    tmpdir,
                    "0123456789abcdef",
                    "status.json",
                )
                with open(status_path, encoding="utf-8") as file_obj:
                    status = json.load(file_obj)

        self.assertEqual(status["phase"], "prepared")
        self.assertNotIn("password", status)

    def test_linger_marker_moves_and_resume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "LINGER_DIR", tmpdir):
                open(os.path.join(tmpdir, "olduser"), "w", encoding="utf-8").close()
                user_rename._rename_linger("olduser", "newuser", True)
                user_rename._rename_linger("olduser", "newuser", True)
                self.assertTrue(os.path.isfile(os.path.join(tmpdir, "newuser")))
                self.assertFalse(os.path.exists(os.path.join(tmpdir, "olduser")))

    def test_concurrent_operation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            operation_id = "0123456789abcdef"
            other_id = "fedcba9876543210"
            other_dir = os.path.join(tmpdir, other_id)
            os.makedirs(other_dir)
            with open(os.path.join(other_dir, "status.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"status": "in_progress"}, file_obj)
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                with self.assertRaisesRegex(user_rename.RenameError, "unfinished"):
                    user_rename._reject_concurrent_operation(operation_id)

    def test_target_lock_serializes_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(user_rename, "RENAME_ROOT", tmpdir):
                first = user_rename._acquire_target_lock()
                try:
                    with self.assertRaisesRegex(user_rename.RenameError, "already running"):
                        user_rename._acquire_target_lock()
                finally:
                    user_rename._release_target_lock(first)


if __name__ == "__main__":
    unittest.main()
