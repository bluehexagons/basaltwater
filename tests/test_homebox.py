"""HomeBox setup contracts and failure recovery without host mutations."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from basaltwater import create_basaltwater_parser, _patch_preserve_keys
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.homebox_cli import run_homebox_command
from lib.homebox_config import (
    homebox_settings, parse_homebox_spec, validate_homebox_settings,
)
from web import homebox_steps as h


def config(**kwargs):
    return SetupConfig(host="inventory.example.com", username="operator", system_type="server_lite",
                       homebox=kwargs.pop("homebox", [":7745"]), **kwargs)


def state(**kwargs):
    value = {"schema": 1, "version": "v0.26.2", "archive_sha256": "a" * 64,
             "binary_sha256": hashlib.sha256(b"binary").hexdigest(), "domain": "", "port": 7745,
             "public_port": 7745, "data_path": "/srv/homebox", "email": "admin@homebox.local",
             "sources": [], "status": "ready", "mount": {"target": "/", "source": "/dev/test", "fstype": "ext4"}}
    value.update(kwargs)
    return value


class HomeBoxConfigTests(unittest.TestCase):
    def test_spec_and_defaults(self):
        self.assertEqual(parse_homebox_spec(":7745"), ("", 7745))
        self.assertEqual(parse_homebox_spec("Inventory.example.com"), ("inventory.example.com", 443))
        self.assertEqual(homebox_settings(config())["data_path"], "/var/lib/homebox")

    def test_invalid_input_before_mutation(self):
        for spec in ("", "7745", ":80", ":443", ":0", ":65536", "a.test:4x", "a.test:４４３",
                     "a.test:443\n", "https://a.test", "127.0.0.1", "a.test;id", "a.test/thing"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_homebox_spec(spec)
        for options in ({"homebox": ["a.test"]}, {"homebox": [":7745", "/etc"]},
                        {"homebox": [":7745", "/srv/a/../b"]}, {"homebox": [":7745", "/srv/a%H"]},
                        {"homebox_version": "../../evil"}, {"homebox_version": "v0.22.1"},
                        {"homebox_port": 8000}, {"enable_cloudflare": True},
                        {"machine_type": "oci"}, {"custom_steps": "setup_homebox"},
                        {"homebox": [":7745", "/srv/data"], "storage_mounts": [["data", "/srv/data", "ext4", "empty"]]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_homebox_settings(config(**options))

    def test_conflicts_and_storage_overlap(self):
        for options in ({"gogs": [":7745"]}, {"web_panel_port": 7745},
                        {"antistatic_server": ":7745"},
                        {"samba_shares": [["write", "inventory", "/var/lib/homebox/photos", "user"]]},
                        {"homebox": ["a.test"], "enable_ssl": True, "gogs": ["a.test:3000"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_homebox_settings(config(**options))
        validate_homebox_settings(config(homebox=["a.test"], enable_ssl=True, gogs=["git.test:3000"]))

    def test_cli_remote_cache_and_patch_roundtrip(self):
        parser, _, _ = create_basaltwater_parser()
        args = parser.parse_args(["setup", "server_lite", "inventory.example.com", "operator", "--homebox",
                                  "items.example.com:9443", "/srv/items", "--homebox-port", "7746", "--ssl",
                                  "--homebox-admin", "owner@example.com", "--homebox-version", "v0.26.2"])
        original = SetupConfig.from_args(args, args.system_type)
        validate_homebox_settings(original)
        cached = SetupConfig.from_dict(original.host, original.system_type, original.to_dict())
        remote = create_setup_argument_parser("remote", for_remote=True)
        remote_args = remote.parse_args(shlex.split(" ".join(cached.to_remote_args())))
        self.assertEqual(remote_args.homebox, original.homebox)
        self.assertEqual(remote_args.homebox_admin, original.homebox_admin)
        self.assertEqual(remote_args.homebox_port, 7746)
        rebuilt = parser.parse_args(shlex.split(" ".join(cached.to_setup_command()))[1:])
        self.assertEqual(rebuilt.homebox, original.homebox)
        patch_args = parser.parse_args(["patch", original.host, "--homebox-version", "v0.26.3"])
        merged = merge_setup_configs(cached, SetupConfig.from_args(patch_args, cached.system_type),
                                     preserve_keys=_patch_preserve_keys(patch_args))
        validate_homebox_settings(merged)
        self.assertEqual(merged.homebox_version, "v0.26.3")
        self.assertEqual(merged.homebox_admin, "owner@example.com")
        disable_args = parser.parse_args(["patch", original.host, "--no-homebox"])
        disabled = merge_setup_configs(cached, SetupConfig.from_args(disable_args, cached.system_type),
                                       preserve_keys=_patch_preserve_keys(disable_args))
        self.assertEqual(disabled.homebox, [])
        self.assertEqual(disabled._homebox_args(), ["--no-homebox"])

    def test_plugin_and_dry_run(self):
        from plugins.server import build_server_steps
        funcs = [func.__name__ for _, func in build_server_steps(config())]
        self.assertEqual(funcs.count("setup_homebox"), 1)
        self.assertEqual(funcs.count("configure_auto_update_homebox"), 1)
        disabled = [func.__name__ for _, func in build_server_steps(config(homebox=[]))]
        self.assertNotIn("configure_auto_update_homebox", disabled)
        with patch.object(h, "homebox_lock", side_effect=AssertionError("mutation")), contextlib.redirect_stdout(io.StringIO()):
            h.setup_homebox(config(dry_run=True))
        self.assertNotIn(7745, config().effective_web_ports())
        self.assertTrue({80, 9443}.issubset(config(homebox=["a.test:9443"], enable_ssl=True).effective_web_ports()))

    def test_panel_excludes_loopback_and_secrets(self):
        from common.web_panel_steps import build_web_panel_manifest
        manifest = build_web_panel_manifest(config(), ["inventory.example.com"])
        self.assertNotIn("HomeBox", json.dumps(manifest))
        manifest = build_web_panel_manifest(config(homebox=["a.test"], enable_ssl=True), ["inventory.example.com"])
        self.assertEqual(manifest["services"][0]["url"], "https://a.test/")
        self.assertEqual(
            manifest["services"][0]["probe"],
            {"kind": "homebox", "port": 7745},
        )
        self.assertNotIn("pepper", json.dumps(manifest))

    def test_auto_update_timer_uses_the_managed_binary(self):
        from common import common_steps

        with patch.object(common_steps, "configure_maintenance_timer", return_value=True) as configure:
            common_steps.configure_auto_update_homebox(config())
        configure.assert_called_once_with(
            service_name="auto-update-homebox",
            service_desc="Auto-update HomeBox inventory service",
            timer_desc="Auto-update HomeBox weekly",
            script_path="/opt/basaltwater/common/service_tools/auto_update_homebox.py",
            schedule="Sun *-*-* 06:00:00",
            check_path="/opt/homebox/current/homebox",
            check_name="HomeBox",
            purpose="auto-update",
        )


class HomeBoxFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, path in {"ROOT": "opt", "CONFIG": "config", "BACKUPS": "backups", "STATE": "state/homebox.json",
                           "UNIT": "units/homebox.service", "SITE": "nginx/site", "LINK": "nginx/enabled",
                           "LOCK": "lock"}.items():
            self.stack.enter_context(patch.object(h, name, self.root / path))
        for path in (h.ROOT, h.CONFIG, h.BACKUPS, h.UNIT.parent, h.SITE.parent):
            path.mkdir(parents=True, exist_ok=True)
        self.secret = {"pepper": "p" * 64, "password": "s" * 32}
        self.stack.enter_context(patch.object(h, "_private_file"))
        self.value = state(data_path=str(self.root / "data"))
        # Keep production path validation at the configuration boundary; this
        # fixture uses temporary data rather than a real /srv tree.
        self.stack.enter_context(patch.object(h, "validate_homebox_path", side_effect=lambda p: p))
        self.data = Path(self.value["data_path"])
        self.data.mkdir()
        with contextlib.closing(sqlite3.connect(self.data / "homebox.db")) as conn:
            conn.execute("CREATE TABLE users (email TEXT)")
            conn.execute("INSERT INTO users VALUES ('owner@example.com')")
            conn.commit()
        (self.data / "attachments").mkdir()
        (self.data / "attachments" / "photo.txt").write_text("original attachment")
        binary = h.release_path(self.value)
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"binary")
        (h.CONFIG / "secrets.json").write_text(json.dumps(self.secret))
        h._save_state(self.value)
        with patch.object(h, "_command"):
            h._activate_files(self.value)

class HomeBoxFilesTests(HomeBoxFixture, unittest.TestCase):
    def test_local_api_requests_use_literal_loopback_client(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"health": true}'
        with patch.object(h, "open_loopback", return_value=response) as open_client, \
                patch.object(h.urllib.request, "build_opener") as build_opener:
            self.assertEqual(
                h._request_json("http://127.0.0.1:7745/api/v1/status", local=True),
                {"health": True},
            )

        request = open_client.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:7745/api/v1/status")
        self.assertEqual(open_client.call_args.kwargs, {"timeout": 15})
        build_opener.assert_not_called()

    def test_database_probe_uses_owner_identity_for_wal_sidecars(self):
        owner = (self.data / "homebox.db").stat()
        with patch.object(h.os, "geteuid", return_value=owner.st_uid + 1), \
                patch.object(h.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"integrity":"ok","users":1}', "")) as probe, \
                patch.object(h.sqlite3, "connect") as root_probe:
            self.assertEqual(h._database(self.value)["users"], 1)
        root_probe.assert_not_called()
        self.assertEqual(probe.call_args.kwargs["user"], owner.st_uid)
        self.assertEqual(probe.call_args.kwargs["group"], owner.st_gid)
        self.assertEqual(probe.call_args.kwargs["extra_groups"], [])
        self.assertEqual(probe.call_args.kwargs["umask"], 0o077)
        self.assertEqual(set(probe.call_args.kwargs["env"]), {"PATH", "LANG"})

    def test_backup_refuses_damaged_binary_or_database(self):
        archive = h.BACKUPS / "damaged.tar.gz"
        h.release_path(self.value).write_bytes(b"damaged")
        with self.assertRaisesRegex(RuntimeError, "damaged HomeBox executable"):
            h.create_backup(self.value, archive)
        h.release_path(self.value).write_bytes(b"binary")
        (self.data / "homebox.db").write_bytes(b"damaged database")
        with self.assertRaises(sqlite3.DatabaseError):
            h.create_backup(self.value, archive)
        self.assertFalse(archive.exists())

    def test_stopped_backup_repairs_legacy_root_owned_sidecars(self):
        sidecars = [self.data / "homebox.db-wal", self.data / "homebox.db-shm"]
        for path in sidecars:
            path.touch()
        original_stat = Path.stat
        owner = (self.data / "homebox.db").stat()
        def observed_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            fields = list(info)
            if path in sidecars:
                fields[4] = 0
            elif path == self.data / "homebox.db":
                fields[4] = 1234
            return os.stat_result(fields)
        with patch.object(Path, "stat", observed_stat), patch.object(h.os, "chown") as chown, \
                patch.object(h, "_database", return_value={"users": 1, "integrity": "ok"}):
            h.create_backup(self.value, h.BACKUPS / "repaired.tar.gz")
        self.assertEqual([call.args for call in chown.call_args_list],
                         [(path, 1234, owner.st_gid) for path in sidecars])

    def test_hardlinked_attachments_produce_restorable_regular_members(self):
        os.link(self.data / "attachments/photo.txt", self.data / "attachments/copy.txt")
        archive = h.BACKUPS / "hardlinks.tar.gz"
        h.create_backup(self.value, archive)
        extracted = self.root / "unpacked"
        extracted.mkdir()
        h.unpack_backup(archive, extracted)
        self.assertEqual((extracted / "data/attachments/copy.txt").read_text(), "original attachment")

    def test_restore_keeps_maintenance_when_https_verification_fails(self):
        value = {**self.value, "domain": "inventory.example.com", "public_port": 443}
        archive = h.BACKUPS / "snapshot.tar.gz"
        h.create_backup(value, archive)
        with patch.object(h, "_check_storage"), patch.object(h, "_private_dir"), patch.object(h, "_stop_service"), \
                patch.object(h, "_ensure_account", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())), \
                patch.object(h, "_command"), patch.object(h, "wait_ready"), patch.object(h, "_write_site"), \
                patch.object(h, "_frontend_ready", side_effect=RuntimeError("TLS failed")) as frontend:
            with self.assertRaisesRegex(RuntimeError, "TLS failed"):
                h.operate_homebox("restore", str(archive), yes=True)
        self.assertTrue((h.CONFIG / "maintenance").exists())
        self.assertTrue(frontend.call_args.kwargs["maintenance"])

    def test_health_detects_configuration_drift_without_exposing_environment(self):
        with patch.object(h, "_active", return_value=True), patch.object(h, "_check_storage"):
            h.UNIT.write_text(h.render_unit(self.value) + "# unexpected change\n")
            health = h.health_homebox()
        self.assertFalse(health["healthy"])
        self.assertFalse(health["configuration_matches"])
        self.assertIn("drifted", health["error"])
        self.assertNotIn(self.secret["pepper"], json.dumps(health))

    def test_release_checks_publisher_digest_and_rejects_non_amd64_hosts(self):
        source = self.root / "release.tar.gz"
        with tarfile.open(source, "w:gz") as bundle:
            member = tarfile.TarInfo("homebox")
            member.size = 6
            bundle.addfile(member, io.BytesIO(b"binary"))
        digest = h._digest(source)
        filename = "x86_64"
        url = f"https://github.com/sysadminsmedia/homebox/releases/download/v0.26.2/homebox_Linux_{filename}.tar.gz"
        metadata = {"tag_name": "v0.26.2", "assets": [{"name": f"homebox_Linux_{filename}.tar.gz",
                    "digest": "sha256:" + digest, "browser_download_url": url}]}
        def download(*args):
            Path(args[args.index("--output") + 1]).write_bytes(source.read_bytes())
        with patch.object(h, "detect_release_arch", return_value="amd64"), \
                patch.object(h, "_request_json", return_value=metadata), patch.object(h, "_command", side_effect=download):
            verified = h.stage_release("v0.26.2")
            self.assertEqual(verified["archive_sha256"], digest)
            self.assertEqual(h.release_path(verified).read_bytes(), b"binary")
            metadata["assets"][0]["digest"] = "sha256:" + "f" * 64
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                h.stage_release("v0.26.2")
            metadata["assets"][0]["digest"] = None
            with self.assertRaises((ValueError, RuntimeError)):
                h.stage_release("v0.26.2")
        with patch.object(h, "detect_release_arch", return_value="arm64"), \
                patch.object(h, "_request_json") as request:
            with self.assertRaisesRegex(RuntimeError, "only on amd64"):
                h.stage_release("v0.26.2")
        request.assert_not_called()

    def test_latest_release_rejects_prereleases_and_invalid_tags(self):
        with patch.object(h, "_request_json", return_value={"tag_name": "v0.26.3"}):
            self.assertEqual(h.latest_homebox_version(), "v0.26.3")
        for response in (
            {"tag_name": "v0.26.3", "prerelease": True},
            {"tag_name": "latest"},
            [],
        ):
            with self.subTest(response=response), patch.object(h, "_request_json", return_value=response):
                with self.assertRaises(RuntimeError):
                    h.latest_homebox_version()

    def test_prune_keeps_recent_automatic_recovery_archives(self):
        archives = []
        for number in range(h.RECOVERY_ARCHIVE_RETENTION + 2):
            archive = h.BACKUPS / f"{number}-before-setup.tar.gz"
            archive.write_bytes(b"recovery")
            os.utime(archive, ns=(number + 1, number + 1))
            archives.append(archive)
        manual = h.BACKUPS / "manual.tar.gz"
        manual.write_bytes(b"manual")

        h.prune_recovery_archives()

        self.assertEqual(
            [path.name for path in archives if path.exists()],
            [path.name for path in archives[-h.RECOVERY_ARCHIVE_RETENTION:]],
        )
        self.assertTrue(manual.exists())

    def test_automatic_update_health_requires_a_scheduled_successful_check(self):
        update_state = self.root / "state/homebox_update.json"
        self.stack.enter_context(patch.object(h, "UPDATE_STATE", update_state))
        update_state.write_text(json.dumps({"schema_version": 1, "successful": True}))
        job = {"available": True, "ActiveState": "inactive", "Result": "success"}
        timer = {
            "available": True,
            "ActiveState": "active",
            "NextElapseUSecRealtime": "Sun 2026-09-14 06:00:00 UTC",
        }
        with patch.object(h, "_unit_properties", side_effect=(job, timer)):
            health = h._automatic_update_health()
        self.assertTrue(health["healthy"])
        self.assertTrue(health["timer"]["scheduled"])
        self.assertFalse(health["check"]["stale"])

    def test_automatic_update_health_reports_pending_before_first_check(self):
        update_state = self.root / "state/homebox_update.json"
        self.stack.enter_context(patch.object(h, "UPDATE_STATE", update_state))
        job = {"available": True, "ActiveState": "inactive", "Result": "success"}
        timer = {
            "available": True,
            "ActiveState": "active",
            "NextElapseUSecRealtime": "Sun 2026-09-14 06:00:00 UTC",
        }
        with patch.object(h, "_unit_properties", side_effect=(job, timer)):
            health = h._automatic_update_health()
        self.assertTrue(health["healthy"])
        self.assertEqual(health["check"]["status"], "pending")
        self.assertIsNone(health["check"]["successful"])
        self.assertFalse(health["check"]["stale"])

    def test_unit_and_proxy_isolation(self):
        value = state(domain="inventory.example.com", public_port=443, sources=["192.168.1.0/24"])
        env = h.render_environment(value, self.secret)
        self.assertIn('HBOX_OPTIONS_ALLOW_REGISTRATION="false"', env)
        self.assertIn('HBOX_WEB_HOST="127.0.0.1"', env)
        self.assertIn("HBOX_DATABASE_SQLITE_PATH", env)
        unit = h.render_unit(value)
        for setting in ("User=homebox", "ProtectSystem=strict", "ReadWritePaths=/srv/homebox", "RequiresMountsFor=/srv/homebox"):
            self.assertIn(setting, unit)
        self.assertNotIn(self.secret["pepper"], unit)
        proxy = h.render_nginx(value)
        self.assertIn("proxy_set_header Upgrade", proxy)
        self.assertIn("allow 192.168.1.0/24", proxy)
        self.assertIn("maintenance", proxy)
        challenge = h.render_nginx(value, challenge=True)
        self.assertNotIn("proxy_pass", challenge)
        self.assertIn("return 503", challenge)

    def test_archive_roundtrip_and_restoration(self):
        archive = h.BACKUPS / "snapshot.tar.gz"
        h.create_backup(self.value, archive)
        with self.assertRaises(FileExistsError):
            h.create_backup(self.value, archive)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        extracted = self.root / "unpacked"
        extracted.mkdir()
        restored = h.unpack_backup(archive, extracted)
        (self.data / "attachments" / "photo.txt").write_text("changed by failed migration")
        (self.data / "new-file").write_text("new schema artefact")
        h.release_path(self.value).write_bytes(b"damaged executable")
        with patch.object(h, "_check_storage"), patch.object(h, "_ensure_account", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())), \
                patch.object(h, "_command"), patch.object(h, "wait_ready"), patch.object(h, "_write_site"):
            h._restore_unpacked(extracted, restored)
        self.assertEqual((self.data / "attachments" / "photo.txt").read_text(), "original attachment")
        self.assertFalse((self.data / "new-file").exists())
        self.assertEqual(h._secrets(), self.secret)
        self.assertEqual(h.read_state(), self.value)
        self.assertEqual(h._database(restored)["users"], 1)
        self.assertEqual(h.release_path(restored).read_bytes(), b"binary")

    def test_explicit_restore_repairs_missing_secrets_and_corrupt_state(self):
        archive = h.BACKUPS / "snapshot.tar.gz"
        h.create_backup(self.value, archive)
        (h.CONFIG / "secrets.json").unlink()
        h.STATE.write_text("{")
        with patch.object(h, "_check_storage"), patch.object(h, "_storage", return_value=self.value["mount"]), \
                patch.object(h, "_private_dir"), patch.object(h, "_stop_service"), \
                patch.object(h, "_ensure_account", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())), \
                patch.object(h, "_command"), patch.object(h, "wait_ready"), patch.object(h, "_write_site"):
            result = h.operate_homebox("restore", str(archive), yes=True)
        self.assertTrue(result["success"])
        self.assertIn("damaged-before-restore", result["previous_state_archive"])
        self.assertTrue(Path(result["previous_state_archive"]).exists())
        self.assertEqual(h._secrets(), self.secret)
        self.assertEqual(h.read_state(), self.value)

    def test_restore_recovers_a_deleted_data_directory(self):
        archive = h.BACKUPS / "snapshot.tar.gz"
        h.create_backup(self.value, archive)
        shutil.rmtree(self.data)
        with patch.object(h, "_storage", return_value=self.value["mount"]), \
                patch.object(h, "_private_dir"), patch.object(h, "_stop_service"), \
                patch.object(h, "_ensure_account", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())), \
                patch.object(h, "_command"), patch.object(h, "wait_ready"), patch.object(h, "_write_site"):
            result = h.operate_homebox("restore", str(archive), yes=True)
        self.assertTrue(result["success"])
        self.assertEqual(h._database(self.value)["users"], 1)

    def test_restore_refuses_archive_that_replacement_would_delete(self):
        archive = h.BACKUPS / "snapshot.tar.gz"
        h.create_backup(self.value, archive)
        inside = self.data / "snapshot.tar.gz"
        shutil.copyfile(archive, inside)
        with patch.object(h, "_check_storage"), patch.object(h, "_private_dir"), \
                patch.object(h, "_stop_service") as stop:
            with self.assertRaisesRegex(ValueError, "outside HomeBox live data"):
                h.operate_homebox("restore", str(inside), yes=True)
        stop.assert_not_called()
        self.assertTrue(inside.exists())

    def test_unsafe_archives_and_symlink_paths(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("data/link", tarfile.SYMTYPE), ("data/hard", tarfile.LNKTYPE),
                           ("data/fifo", tarfile.FIFOTYPE)):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type = kind
                with self.assertRaises(RuntimeError):
                    h._validate_members([member])
        with self.assertRaises(RuntimeError):
            h._validate_members([tarfile.TarInfo("data/file"), tarfile.TarInfo("data/file")])
        link = self.data / "link"
        link.symlink_to(self.root)
        with self.assertRaises(RuntimeError):
            h.create_backup(self.value, h.BACKUPS / "unsafe.tar.gz")
        with self.assertRaises(RuntimeError):
            h._safe_path(link / "nested")

    def test_corrupt_state_and_missing_secrets_fail_closed(self):
        h.STATE.write_text("{")
        with self.assertRaisesRegex(RuntimeError, "corrupt"):
            h.read_state()
        (h.CONFIG / "secrets.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "without rotating"):
            h._secrets()

    def test_maintenance_requires_explicit_recovery(self):
        h._maintenance(h.BACKUPS / "recovery.tar.gz")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            h._require_idle()

    def test_ownership_refuses_unmanaged_files(self):
        h.UNIT.write_text("[Service]\nExecStart=/custom/service")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            h._owned_files()

    def test_existing_binary_tampering_rejected_before_activation(self):
        h.release_path(self.value).write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"), patch.object(h, "_command") as command:
            h._activate_files(self.value)
        command.assert_not_called()

    def test_readiness_checks_actual_homebox_contract(self):
        with patch.object(h, "_request_json", return_value={"health": True, "allowRegistration": False, "build": {"version": "0.26.2"}}):
            h.wait_ready(7745, "v0.26.2")
        with patch.object(h, "_request_json", return_value={"healthy": True}), patch.object(h.time, "sleep"):
            with self.assertRaises(RuntimeError):
                h.wait_ready(7745, "v0.26.2")

    def test_health_does_not_expose_credentials(self):
        with patch.object(h, "_active", return_value=True), patch.object(h, "_check_storage"), \
                patch.object(h, "_status", return_value={"health": True, "allowRegistration": False, "build": {"version": "0.26.2"}}), \
                patch.object(h, "_frontend_ready"), \
                patch.object(h, "_automatic_update_health", return_value={"healthy": True}):
            health = h.health_homebox()
        self.assertTrue(health["healthy"])
        self.assertNotIn(self.secret["password"], json.dumps(health))
        self.assertNotIn(self.secret["pepper"], json.dumps(health))

    def test_restore_requires_confirmation_and_compatible_path(self):
        with self.assertRaisesRegex(ValueError, "--yes"):
            h.operate_homebox("restore", "/srv/snapshot.tar.gz")
        with patch.object(h, "homebox_lock", side_effect=AssertionError("mutation")):
            self.assertTrue(h.operate_homebox("backup", "/srv/snapshot.tar.gz", dry_run=True)["dry_run"])

    def test_bootstrap_never_enables_registration_in_permanent_environment(self):
        calls = []
        def command(*args):
            calls.append(args)
            if args[0] == "systemd-run":
                text = (h.CONFIG / "bootstrap.env").read_text()
                self.assertIn('HBOX_OPTIONS_ALLOW_REGISTRATION="true"', text)
                self.assertIn("--property=RuntimeMaxSec=90", args)
        with patch.object(h, "_command", side_effect=command), patch.object(h, "wait_ready"), \
                patch.object(h, "_database", side_effect=[{"users": 0}, {"users": 1}]), \
                patch.object(h, "_request_json", side_effect=[None, {"token": "private-token"}]):
            h._bootstrap(self.value)
        self.assertFalse((h.CONFIG / "bootstrap.env").exists())
        self.assertEqual(calls[-1], ("systemctl", "stop", "basaltwater-homebox-bootstrap.service"))
        self.assertNotIn(self.secret["password"], str(calls))


class HomeBoxTransactionTests(HomeBoxFixture, unittest.TestCase):
    def fixture_setup(self, failure=None):
        desired_config = config(homebox=[":7745", self.value["data_path"]], homebox_version="v0.26.3")
        self.stack.enter_context(patch.object(h, "validate_homebox_settings"))
        self.stack.enter_context(patch.object(h, "can_manage_system_services", return_value=True))
        self.stack.enter_context(patch.object(h, "require_amd64"))
        self.stack.enter_context(patch.object(h, "_storage", return_value=self.value["mount"]))
        self.stack.enter_context(patch.object(h, "_check_storage"))
        self.stack.enter_context(patch.object(h, "_private_dir"))
        self.stack.enter_context(patch.object(h, "_ensure_account", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        self.stack.enter_context(patch.object(h, "install_package", return_value=True))
        self.stack.enter_context(patch.object(h, "_active", return_value=True))
        self.stack.enter_context(patch.object(h, "_owned_files"))
        self.stack.enter_context(patch.object(h, "_command"))
        self.stack.enter_context(patch.object(h, "_stop_service"))
        self.stack.enter_context(patch.object(h, "_remove_acme_rule"))
        self.stack.enter_context(patch.object(h, "stage_release", return_value={k: {**self.value, "version": "v0.26.3"}[k] for k in ("version", "archive_sha256", "binary_sha256")}))
        self.stack.enter_context(patch.object(h, "_activate_files"))
        self.stack.enter_context(patch.object(h, "_write_site"))
        self.stack.enter_context(patch.object(h, "wait_ready", side_effect=failure))
        self.stack.enter_context(patch.object(h, "_frontend_ready"))
        h.UNIT.write_text(h.MARKER)
        return desired_config

    def test_failed_activation_restores_full_snapshot_before_clearing_marker(self):
        cfg = self.fixture_setup(RuntimeError("bad migration"))
        events = []
        def recover(archive):
            self.assertTrue((h.CONFIG / "maintenance").exists())
            self.assertTrue(archive.exists())
            events.append("recover")
        with patch.object(h, "_recover", side_effect=recover), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "bad migration"):
                h.setup_homebox(cfg)
        self.assertEqual(events, ["recover"])
        self.assertFalse((h.CONFIG / "maintenance").exists())
        self.assertEqual(h.read_state()["version"], "v0.26.2")

    def test_failed_recovery_retains_marker_and_archive(self):
        cfg = self.fixture_setup(RuntimeError("bad migration"))
        with patch.object(h, "_recover", side_effect=RuntimeError("disk failed")), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                h.setup_homebox(cfg)
        marker = json.loads((h.CONFIG / "maintenance").read_text())
        self.assertTrue(Path(marker["backup"]).exists())

    def test_failure_after_reopening_ingress_does_not_roll_back_inventory(self):
        cfg = self.fixture_setup()
        with patch.object(h, "_frontend_ready", side_effect=[None, RuntimeError("TLS probe failed")]), \
                patch.object(h, "_recover") as recover, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "TLS probe failed"):
                h.setup_homebox(cfg)
        recover.assert_not_called()
        self.assertEqual(h.read_state()["version"], "v0.26.3")

    def test_failed_tls_before_ingress_restores_snapshot(self):
        cfg = self.fixture_setup()
        with patch.object(h, "_frontend_ready", side_effect=RuntimeError("invalid certificate")), \
                patch.object(h, "_recover") as recover:
            with self.assertRaisesRegex(RuntimeError, "invalid certificate"):
                h.setup_homebox(cfg)
        recover.assert_called_once()
        self.assertEqual(h.read_state()["version"], "v0.26.2")

    def test_healthy_rerun_retains_version_without_release_lookup(self):
        cfg = self.fixture_setup()
        cfg.homebox_version = None
        with patch.object(h, "_files_match", return_value=True), patch.object(h, "stage_release") as stage, \
                patch.object(h, "create_backup") as backup, contextlib.redirect_stdout(io.StringIO()):
            h.setup_homebox(cfg)
        stage.assert_not_called()
        backup.assert_not_called()
        self.assertEqual(h._secrets(), self.secret)

    def test_downgrade_requires_full_restore(self):
        cfg = self.fixture_setup()
        h._save_state({**self.value, "version": "v0.26.3"})
        cfg.homebox_version = "v0.26.2"
        with patch.object(h, "stage_release") as stage, self.assertRaisesRegex(ValueError, "downgrades"):
            h.setup_homebox(cfg)
        stage.assert_not_called()

    def test_scheduled_update_uses_the_release_transition_transaction(self):
        self.fixture_setup()
        with patch.object(h, "_transition_to_ready") as transition:
            version, changed = h.update_homebox("v0.26.3")
        self.assertEqual((version, changed), ("v0.26.3", True))
        previous, desired = transition.call_args.args
        self.assertEqual(previous["version"], "v0.26.2")
        self.assertEqual(desired["version"], "v0.26.3")
        self.assertFalse(transition.call_args.kwargs["bootstrap"])

    def test_scheduled_update_skips_disabled_and_current_services(self):
        self.fixture_setup()
        h._save_state({**self.value, "status": "disabled"})
        self.assertEqual(h.update_homebox("v0.26.3"), ("", False))
        h._save_state(self.value)
        with patch.object(h, "stage_release") as stage:
            self.assertEqual(h.update_homebox("v0.26.2"), ("v0.26.2", False))
        stage.assert_not_called()

    def test_scheduled_update_checks_current_binary_before_stopping_service(self):
        self.fixture_setup()
        h.release_path(self.value).write_bytes(b"tampered")
        with patch.object(h, "stage_release") as stage, \
                patch.object(h, "_transition_to_ready") as transition:
            with self.assertRaisesRegex(RuntimeError, "binary has changed"):
                h.update_homebox("v0.26.3")
        stage.assert_not_called()
        transition.assert_not_called()

    def test_latest_update_skips_incomplete_installation_without_a_release_check(self):
        self.fixture_setup()
        h._save_state({**self.value, "status": "prepared"})
        with patch.object(h, "latest_homebox_version") as latest:
            self.assertIsNone(h.update_homebox_to_latest())
        latest.assert_not_called()

    def test_disable_can_be_repeated_after_unit_removal(self):
        cfg = self.fixture_setup()
        cfg.homebox = []
        with patch.object(h, "_command") as command, \
                patch.object(h, "run", return_value=subprocess.CompletedProcess([], 0)), \
                contextlib.redirect_stdout(io.StringIO()):
            h.setup_homebox(cfg)
            self.assertFalse(h.UNIT.exists())
            command.reset_mock()
            h.setup_homebox(cfg)
        self.assertNotIn(("systemctl", "disable", h.SERVICE), [call.args for call in command.call_args_list])
        self.assertEqual(h.read_state()["status"], "disabled")
        self.assertTrue((self.data / "homebox.db").exists())

    def test_disabling_incomplete_installation_preserves_bootstrap_on_reenable(self):
        cfg = self.fixture_setup()
        h._save_state({**self.value, "status": "prepared"})
        (self.data / "homebox.db").unlink()
        cfg.homebox = []
        with patch.object(h, "run", return_value=subprocess.CompletedProcess([], 0)), \
                contextlib.redirect_stdout(io.StringIO()):
            h.setup_homebox(cfg)
        self.assertTrue(h.read_state()["bootstrap_pending"])
        cfg.homebox = [":7745", self.value["data_path"]]
        with patch.object(h, "_bootstrap") as bootstrap, \
                patch.object(h, "_database", return_value={"users": 1}), contextlib.redirect_stdout(io.StringIO()):
            h.setup_homebox(cfg)
        bootstrap.assert_called_once()
        self.assertEqual(h.read_state()["status"], "ready")
        self.assertFalse(h._needs_bootstrap(h.read_state()))

    def test_snapshot_failure_does_not_run_new_binary(self):
        cfg = self.fixture_setup()
        with patch.object(h, "create_backup", side_effect=OSError("disk full")), patch.object(h, "_activate_files") as activate:
            with self.assertRaises(OSError):
                h.setup_homebox(cfg)
        activate.assert_not_called()
        self.assertFalse((h.CONFIG / "maintenance").exists())

    def test_non_amd64_setup_stops_before_target_mutation(self):
        cfg = config()
        with patch.object(h, "can_manage_system_services", return_value=True), \
                patch.object(h, "require_amd64", side_effect=RuntimeError("amd64")), \
                patch.object(h, "homebox_lock", side_effect=AssertionError("mutation")):
            with self.assertRaisesRegex(RuntimeError, "amd64"):
                h.setup_homebox(cfg)


class HomeBoxCLITests(unittest.TestCase):
    def test_health_json_and_unhealthy_exit_code(self):
        args = argparse.Namespace(host="inventory.example.com", username="operator", ssh_key=None,
                                  homebox_command="health", json=True)
        with patch("lib.homebox_cli.load_setup_command", return_value=None), \
                patch("lib.homebox_cli.build_ssh_command", return_value=["ssh", "host"]) as ssh, \
                patch("lib.homebox_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 1, '{"healthy":false}', "")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_homebox_command(args), 1)
        self.assertFalse(json.loads(output.getvalue())["healthy"])
        self.assertIn("sudo -n python3 -m web.homebox_steps health", ssh.call_args.kwargs["remote_command"])

    def test_health_text_reports_automatic_update_state(self):
        args = argparse.Namespace(host="inventory.example.com", username=None, ssh_key=None,
                                  homebox_command="health", json=False)
        result = {
            "healthy": True,
            "automatic_update": {
                "configured": True,
                "timer": {"active": True, "scheduled": True},
                "check": {"stale": False, "successful": True},
            },
        }
        with patch("lib.homebox_cli.load_setup_command", return_value=None), \
                patch("lib.homebox_cli.build_ssh_command", return_value=["ssh", "host"]), \
                patch("lib.homebox_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(result), "")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_homebox_command(args), 0)
        self.assertIn("automatic update timer: ok", output.getvalue())
        self.assertIn("automatic update check: ok", output.getvalue())

    def test_health_text_reports_pending_automatic_update_check(self):
        args = argparse.Namespace(host="inventory.example.com", username=None, ssh_key=None,
                                  homebox_command="health", json=False)
        result = {
            "healthy": True,
            "automatic_update": {
                "configured": True,
                "timer": {"active": True, "scheduled": True},
                "check": {"status": "pending", "stale": False, "successful": None},
            },
        }
        with patch("lib.homebox_cli.load_setup_command", return_value=None), \
                patch("lib.homebox_cli.build_ssh_command", return_value=["ssh", "host"]), \
                patch("lib.homebox_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(result), "")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_homebox_command(args), 0)
        self.assertIn("automatic update check: pending", output.getvalue())

    def test_backup_dry_run_and_restore_consent_do_not_connect(self):
        for action, dry_run, expected in (("backup", True, 0), ("restore", False, 1)):
            args = argparse.Namespace(host="inventory.example.com", username=None, ssh_key=None, homebox_command=action,
                                      path="/srv/backup.tar.gz", dry_run=dry_run, yes=False, json=False)
            with patch("lib.homebox_cli.load_setup_command", return_value=None), patch("lib.homebox_cli.subprocess.run") as remote, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_homebox_command(args), expected)
                remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
