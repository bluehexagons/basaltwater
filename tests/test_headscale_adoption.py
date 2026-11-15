"""Bounded adoption checks with private fixtures and mocked host observations."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import headscale_adoption as adoption
from lib import headscale_backup as backup
from lib import headscale_server as server
from lib.atomic_io import write_json_atomic
from tests.test_headscale_server import configuration, fixture_paths
from web import headscale_steps as steps


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = fixture_paths(self.root)
        self.config = configuration()
        for path in (self.paths.etc, self.paths.data, self.paths.nginx.parent):
            path.mkdir(mode=0o700)
        self.paths.etc.chmod(0o750)
        self.paths.config.write_text(server.render_config(self.config, self.paths))
        (self.paths.etc / "policy.hujson").write_text('{"acls": []}')
        for name in ("noise_private.key", "derp_server_private.key"):
            (self.paths.data / name).write_bytes(b"existing-native-key-" + name.encode())
        connection = sqlite3.connect(self.paths.data / "db.sqlite")
        connection.execute("create table fixture (value text)")
        connection.execute("insert into fixture values ('original device identity')")
        connection.commit()
        connection.close()
        self.paths.nginx.write_text(server.render_nginx(self.config.headscale_domain))
        for name, path in backup.snapshot_files(self.paths).items():
            if name != "manifest.json":
                path.chmod(0o640 if name in {"config.yaml", "policy.hujson"} else 0o600)
        self.paths.nginx.chmod(0o644)
        self.original = {p: p.read_bytes() for p in (self.paths.config, self.paths.nginx,
            self.paths.etc / "policy.hujson", *self.paths.data.iterdir())}
        self.addCleanup(patch.stopall)
        patch.object(adoption, "require_capabilities").start()
        self.stopped = patch.object(adoption, "stopped").start()
        patch.object(adoption, "ServerPaths", wraps=server.ServerPaths).start().side_effect = (
            lambda *args: server.ServerPaths(*args) if args else self.paths)
        patch.object(adoption, "_ancestors").start()
        self.package = patch.object(adoption, "_package_identity", return_value={"pin": server.VERSION}).start()
        account = SimpleNamespace(pw_uid=max(os.getuid(), 1000), pw_gid=max(os.getgid(), 1000))
        patch.object(adoption.pwd, "getpwnam", return_value=account).start()
        original_stat = Path.stat

        def observed_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path in {self.paths.etc, self.paths.data}:
                return SimpleNamespace(st_uid=0 if path == self.paths.etc else account.pw_uid,
                                       st_gid=account.pw_gid, st_mode=info.st_mode)
            return info
        patch.object(Path, "stat", observed_stat).start()
        identity = adoption._identity
        patch.object(adoption, "_identity", side_effect=lambda path, uid, gid, mode:
            identity(path, uid=os.getuid(), gid=os.getgid(), mode=mode)).start()
        patch.object(adoption, "run", return_value=SimpleNamespace(stdout=
            "Status: active\n80/tcp ALLOW Anywhere\n443/tcp ALLOW Anywhere\n3478/udp ALLOW Anywhere\n")).start()
        patch.object(server.os, "fchown").start()
        patch.object(backup, "stopped").start()
        patch.object(backup, "configtest").start()
        self.native_test = patch.object(adoption, "configtest").start()
        self.preflight = patch.object(adoption, "preflight").start()
        self.review = adoption.inspect_adoption(self.config, self.root / "pinned.deb")
        self.review["authority"] = True
        self.review_file = self.root / "review.json"
        write_json_atomic(str(self.review_file), self.review)

    def adopt(self, **kwargs):
        return adoption.adopt_headscale(self.config, self.root / "pinned.deb", self.review_file,
                                       self.root / "snapshot", **kwargs)

    def test_preserves_identity_and_backup_then_setup_rerun(self):
        manifest = self.adopt()
        self.assertEqual(server.load_manifest(self.paths), manifest)
        self.assertFalse((self.paths.private / "operation.json").exists())
        for path, content in self.original.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual((self.root / "snapshot/policy.hujson").read_bytes(), b'{"acls": []}')
        self.native_test.assert_called_once_with(self.paths.config,
            expected_database_schema=self.paths.data / "db.sqlite")
        self.preflight.assert_called_once()
        with patch.object(steps, "ServerPaths", return_value=self.paths), \
                patch.object(steps, "require_capabilities"), patch.object(steps, "preflight", return_value=manifest), \
                patch.object(steps, "run", return_value=SimpleNamespace(stdout="Status: active")), \
                patch.object(steps, "configtest"), patch.object(steps, "health"), \
                patch.object(steps, "install_deb") as install:
            steps.configure_headscale(self.config)
            install.assert_not_called()
        for path, content in self.original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_dry_run_does_not_write_or_run_native_validation(self):
        before = set(self.root.rglob("*"))
        self.adopt(dry_run=True)
        self.assertEqual(set(self.root.rglob("*")), before)
        self.native_test.assert_not_called()

    def test_wrong_config_dns_pin_digest_and_running_refuse(self):
        for field in ("config", "dns", "pin", "digest", "authority", "running"):
            with self.subTest(field=field):
                self.paths.config.write_bytes(self.original[self.paths.config])
                review = json.loads(json.dumps(self.review))
                self.package.side_effect = None
                self.stopped.side_effect = None
                if field in {"config", "dns"}:
                    value = json.loads(self.paths.config.read_text())
                    if field == "config":
                        value["listen_addr"] = "0.0.0.0:8080"
                    else:
                        value["dns"]["nameservers"]["global"] = ["192.0.2.1"]
                    self.paths.config.write_text(json.dumps(value))
                elif field == "pin":
                    self.package.side_effect = ValueError("wrong pin")
                elif field == "digest":
                    review["files"]["noise.key"]["sha256"] = "0" * 64
                elif field == "authority":
                    review["authority"] = False
                else:
                    self.stopped.side_effect = RuntimeError("service running")
                write_json_atomic(str(self.review_file), review)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.adopt()
                self.assertFalse(self.paths.manifest.exists())

    def test_sidecar_and_foreign_policy_path_refused(self):
        sidecar = self.paths.data / "db.sqlite-wal"
        sidecar.write_bytes(b"uncheckpointed")
        with self.assertRaisesRegex(ValueError, "sidecars"):
            self.adopt()
        sidecar.unlink()
        value = json.loads(self.paths.config.read_text())
        value["policy"]["path"] = "/foreign/policy.hujson"
        self.paths.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "manual migration"):
            self.adopt()

    def test_failure_retains_backup_and_blocks_retry(self):
        self.native_test.side_effect = RuntimeError("uncertain validation")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.adopt()
        self.assertTrue((self.root / "snapshot/backup.json").exists())
        self.assertTrue((self.paths.private / "operation.json").exists())
        self.assertFalse(self.paths.manifest.exists())
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.adopt()

    def test_cas_rechecks_after_backup(self):
        capture = adoption._backup
        def changed(*args):
            capture(*args)
            (self.paths.data / "noise_private.key").write_bytes(b"changed key")
        with patch.object(adoption, "_backup", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "CAS"):
                self.adopt()
        self.assertFalse(self.paths.manifest.exists())

    def test_uncertain_publication_keeps_blocking_evidence(self):
        publish = adoption.write_json_atomic
        def uncertain(path, *args, **kwargs):
            publish(path, *args, **kwargs)
            if path == str(self.paths.manifest):
                raise OSError("uncertain fsync")
        with patch.object(adoption, "write_json_atomic", side_effect=uncertain):
            with self.assertRaisesRegex(OSError, "uncertain"):
                self.adopt()
        self.assertTrue(self.paths.manifest.exists())
        self.assertTrue((self.paths.private / "operation.json").exists())
        self.assertTrue((self.root / "snapshot/backup.json").exists())


class NativeIdentityTests(unittest.TestCase):
    def test_unit_overrides_running_stale_and_foreign_fragment_refused(self):
        fields = dict(FragmentPath="/usr/lib/systemd/system/headscale.service", DropInPaths="",
            NeedDaemonReload="no", LoadState="loaded", Environment="", EnvironmentFiles="",
            UnitFileState="disabled", ActiveState="inactive", SubState="dead", MainPID="0", ControlPID="0")
        with patch.object(adoption, "run") as run:
            run.return_value = SimpleNamespace(stdout="\n".join(f"{k}={v}" for k, v in fields.items()))
            self.assertEqual(adoption._unit_state(Path(fields["FragmentPath"])), fields)
            for key, value in (("FragmentPath", "/foreign/unit"), ("DropInPaths", "/etc/override"),
                    ("Environment", "HEADSCALE_LISTEN_ADDR=0.0.0.0:8080"), ("NeedDaemonReload", "yes"),
                    ("MainPID", "123"), ("UnitFileState", "enabled"), ("ActiveState", "active")):
                with self.subTest(key=key):
                    altered = dict(fields, **{key: value})
                    run.return_value.stdout = "\n".join(f"{k}={v}" for k, v in altered.items())
                    with self.assertRaisesRegex(ValueError, "package unit"):
                        adoption._unit_state(Path(fields["FragmentPath"]))

    def test_wrong_owner_mode_and_links_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "key"
            path.write_bytes(b"private")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                adoption._identity(path, uid=os.getuid() + 1, gid=os.getgid(), mode=0o600)
            with self.assertRaises(ValueError):
                adoption._identity(path, uid=os.getuid(), gid=os.getgid(), mode=0o640)
            os.link(path, Path(temporary) / "hardlink")
            with self.assertRaises(ValueError):
                adoption._identity(path, uid=os.getuid(), gid=os.getgid(), mode=0o600)
            linked = Path(temporary) / "symlink"
            linked.symlink_to(path)
            with self.assertRaises(OSError):
                adoption._identity(linked, uid=os.getuid(), gid=os.getgid(), mode=0o600)

    def test_wrong_package_pin_precedes_extraction(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(adoption, "run") as run:
            path = Path(temporary) / "package.deb"
            path.write_bytes(b"foreign package")
            path.chmod(0o600)
            run.return_value = SimpleNamespace(stdout="amd64")
            with self.assertRaisesRegex(ValueError, "pinned package"):
                adoption._package_identity(path)
            self.assertEqual(run.call_count, 1)

    def test_binary_and_unit_must_match_verified_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "package.deb"
            package.write_bytes(b"fixture-reviewed-package")
            package.chmod(0o600)
            original_read = adoption.regular_bytes
            changed = set()
            def read(path):
                if str(path) in {"/usr/bin/headscale", "/usr/lib/systemd/system/headscale.service"}:
                    return b"changed" if str(path) in changed else b"pinned"
                return original_read(path)
            def execute(command, **kwargs):
                if command[0] == "dpkg":
                    output = "amd64"
                elif command[0] == "dpkg-query":
                    output = "hold ok installed|0.29.7|amd64"
                elif command[0] == "apt-mark":
                    output = "headscale\n"
                else:
                    output = ""
                    payload = Path(command[-1])
                    for name in ("usr/bin/headscale", "usr/lib/systemd/system/headscale.service"):
                        target = payload / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"pinned")
                        target.chmod(0o600)
                return SimpleNamespace(stdout=output)
            with patch.object(adoption, "run", side_effect=execute), \
                    patch.object(adoption, "regular_bytes", side_effect=read), \
                    patch.dict(adoption.SHA256, {"amd64": adoption._digest(package.read_bytes())}), \
                    patch.object(adoption, "verify_package"), patch.object(adoption, "_ancestors"), \
                    patch.object(adoption, "_identity", return_value={"sha256": "fixture"}), \
                    patch.object(adoption, "_unit_state", return_value={}), \
                    patch.object(adoption, "installed_version", return_value="0.29.7"), \
                    patch.object(adoption.shutil, "which", return_value="/usr/bin/headscale"):
                self.assertEqual(adoption._package_identity(package)["architecture"], "amd64")
                for path in ("/usr/bin/headscale", "/usr/lib/systemd/system/headscale.service"):
                    changed.clear()
                    changed.add(path)
                    with self.assertRaisesRegex(ValueError, "differs from pinned"):
                        adoption._package_identity(package)

    def test_foreign_schema_refused_without_live_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = fixture_paths(temporary)
            paths.etc.mkdir()
            paths.data.mkdir()
            paths.config.write_text(server.render_config(configuration(), paths))
            paths.config.chmod(0o600)
            database = paths.data / "db.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("create table foreign_schema (id integer)")
            connection.close()
            database.chmod(0o600)
            before = database.read_bytes()
            def validate(command, **kwargs):
                candidate = json.loads(Path(command[2]).read_text())
                scratch = sqlite3.connect(candidate["database"]["sqlite"]["path"])
                scratch.execute("create table native_schema (id integer)")
                scratch.close()
                return subprocess.CompletedProcess(command, 0)
            with patch.object(server, "run", side_effect=validate):
                with self.assertRaisesRegex(ValueError, "schema differs"):
                    server.configtest(paths.config, expected_database_schema=database)
            self.assertEqual(database.read_bytes(), before)
