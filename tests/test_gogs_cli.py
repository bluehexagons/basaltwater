"""Tests for Gogs operator commands."""

from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib.gogs_cli import (
    _format_health,
    _remote_health_script,
    add_gogs_subparser,
    inspect_remote_gogs,
    run_gogs_command,
)


def _healthy_result() -> dict[str, object]:
    return {
        "healthy": True,
        "service_active": True,
        "sqlite_healthy": True,
        "storage": {
            "source": "/dev/vdb1",
            "filesystem": "ext4",
            "mount_target": "/srv/gogs",
            "free_bytes": 2_000_000_000,
            "free_inodes": 20_000,
            "usage_bytes": {"repositories": 100, "lfs_objects": 200},
        },
        "update_job": {"failed": False},
        "update_timer": {"active": True},
        "update_check": {"age_seconds": 60, "stale": False, "successful": True},
        "nginx_upload_limit_bytes": 536_870_912,
        "nginx": {"required": True, "active": True, "config_valid": True},
        "frontend": {"healthy": True, "mode": "tls"},
        "remote_lfs_endpoint_configured": True,
    }


class TestGogsHealthParser(unittest.TestCase):
    def test_parser_accepts_json_and_capacity_thresholds(self):
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest="command")
        add_gogs_subparser(commands)

        args = parser.parse_args(
            [
                "gogs",
                "health",
                "git.example.test",
                "--json",
                "--min-free-bytes",
                "2048",
                "--min-free-inodes",
                "50",
            ]
        )

        self.assertEqual(args.gogs_command, "health")
        self.assertTrue(args.json)
        self.assertEqual(args.min_free_bytes, 2048)
        self.assertEqual(args.min_free_inodes, 50)

    def test_parser_accepts_repository_configuration(self):
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest="command")
        add_gogs_subparser(commands)

        args = parser.parse_args(
            [
                "gogs",
                "repo-configure",
                "/srv/project",
                "--github-url",
                "https://github.com/team/project.git",
                "--gogs-url",
                "https://git.example.test/team/project.git",
                "--track",
                "assets/**",
                "--dry-run",
            ]
        )

        self.assertEqual(args.gogs_command, "repo-configure")
        self.assertEqual(args.track, ["assets/**"])
        self.assertTrue(args.dry_run)


class TestRemoteGogsHealth(unittest.TestCase):
    def _probe(
        self, *, missing=(), non_directories=(), denied=(), sqlite_path=None,
        sqlite_failed=False, enable_ssl=True, http_port="13000",
        curl_returncode=0, curl_status="200",
    ):
        from lib.config import SetupConfig
        from web.gogs_steps import generate_gogs_app_ini

        config = SetupConfig(
            host="10.100.0.100", username="agent", system_type="server_web",
            gogs=[":3000"], enable_ssl=enable_ssl, gogs_sources=["192.168.0.45/32"],
        )
        with patch("web.gogs_steps._load_or_create_gogs_secret_key", return_value="test-secret"):
            app_ini = generate_gogs_app_ini(
                config, git_home="/home/git", data_path="/srv/gogs",
                domain="", port=3000, backend_port=13000,
            )
        app_ini = app_ini.replace("HTTP_PORT = 13000", f"HTTP_PORT = {http_port}")
        database_exists = os.path.isfile(sqlite_path) if sqlite_path is not None else True
        files = {
            "/opt/basaltwater/state/gogs.json": json.dumps({
                "data_path": "/srv/gogs", "config_path": "/srv/gogs/custom/conf/app.ini",
            }),
            "/srv/gogs/custom/conf/app.ini": app_ini,
            "/etc/nginx/sites-enabled/gogs_test": (
                "listen 3000 ssl; ssl_certificate /test.crt; client_max_body_size 512M;"
            ),
        }

        def read_file(path, **kwargs):
            return io.StringIO(files[path])

        def run(args, **kwargs):
            stdout = ""
            if args[0] == "runuser" and args[-1] in denied:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:2] == ("systemctl", "is-active"):
                stdout = "active\n"
            elif args[0] == "findmnt":
                stdout = json.dumps({"filesystems": [{"source": "/dev/sdb", "fstype": "ext4", "target": "/srv/gogs"}]})
            elif args[0] == "sqlite3":
                self.assertEqual(args[1], "-readonly")
                if sqlite_failed:
                    return SimpleNamespace(returncode=1, stdout="", stderr="query failed")
                stdout = "ok\n2\n"
                if sqlite_path is not None:
                    with closing(sqlite3.connect(Path(sqlite_path).as_uri() + "?mode=ro", uri=True)) as database:
                        stdout = "\n".join(
                            str(row[0]) for query in args[3].split(";") if query.strip()
                            for row in database.execute(query)
                        ) + "\n"
            elif args[0] == "curl":
                return SimpleNamespace(returncode=curl_returncode, stdout=curl_status, stderr="")
            elif args[0] == "du":
                stdout = "1024 /srv/gogs\n"
            elif args[:3] == ("systemctl", "show", "auto-update-gogs.service"):
                stdout = "ActiveState=inactive\nResult=success\n"
            elif args[:3] == ("systemctl", "show", "auto-update-gogs.timer"):
                stdout = "ActiveState=active\nNextElapseUSecRealtime=tomorrow\n"
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        output = io.StringIO()
        with (
            patch("builtins.open", side_effect=read_file),
            patch("subprocess.run", side_effect=run) as runner,
            patch("glob.glob", return_value=["/etc/nginx/sites-enabled/gogs_test"]),
            patch("os.path.exists", side_effect=lambda path: not path.endswith("gogs_update.json")),
            patch("os.path.lexists", side_effect=lambda path: path not in missing),
            patch("os.path.isdir", side_effect=lambda path: path not in (*missing, *non_directories)),
            patch("os.path.isfile", return_value=database_exists),
            patch("os.path.islink", return_value=False),
            patch("os.path.getmtime", return_value=1000),
            patch("os.statvfs", return_value=SimpleNamespace(f_bavail=1000, f_frsize=4096, f_favail=1000)),
            patch("time.time", return_value=1000),
            redirect_stdout(output),
        ):
            exec(_remote_health_script(100, 10), {})
        return json.loads(output.getvalue()), runner

    def test_database_must_exist_and_have_core_gogs_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "gogs.db")
            result, runner = self._probe(sqlite_path=database_path)
            self.assertFalse(result["sqlite_healthy"])
            self.assertFalse(result["healthy"])
            self.assertFalse(os.path.exists(database_path))
            self.assertFalse(any(call.args[0][0] == "sqlite3" for call in runner.call_args_list))
            for schema, healthy in (
                ("", False),
                ("CREATE TABLE user (id INTEGER, name TEXT);", False),
                ("CREATE TABLE repository (id INTEGER, name TEXT);", True),
            ):
                with self.subTest(schema=schema):
                    with closing(sqlite3.connect(database_path)) as database:
                        database.executescript(schema)
                    before = Path(database_path).read_bytes()
                    result, _ = self._probe(sqlite_path=database_path)
                    self.assertEqual(Path(database_path).read_bytes(), before)
                    self.assertEqual(result["sqlite_healthy"], healthy)
                    self.assertEqual(result["healthy"], healthy)

    def test_failed_database_query_is_unhealthy(self):
        result, _ = self._probe(sqlite_failed=True)
        self.assertFalse(result["sqlite_healthy"])
        self.assertFalse(result["healthy"])

    def test_direct_http_requires_a_successful_200_response(self):
        for returncode, status in ((7, "000"), (22, "503"), (0, "302"), (0, "200")):
            with self.subTest(returncode=returncode, status=status):
                result, runner = self._probe(
                    enable_ssl=False, http_port="4321", curl_returncode=returncode,
                    curl_status=status,
                )
                healthy = returncode == 0 and status == "200"
                self.assertEqual(result["frontend"], {"healthy": healthy, "mode": "direct"})
                self.assertEqual(result["healthy"], healthy)
                self.assertEqual(result["service_active"], True)
                command = next(call.args[0] for call in runner.call_args_list if call.args[0][0] == "curl")
                self.assertEqual(command[-1], "http://127.0.0.1:4321/")
                self.assertIn("--noproxy", command)
                self.assertEqual(command[command.index("--max-time") + 1], "5")
                self.assertIn("Public web endpoint: " + ("ok" if healthy else "FAILED"), _format_health(result, "host"))

    def test_invalid_direct_backend_port_does_not_probe_http(self):
        for port in ("", "invalid", "0", "65536", "3000;id"):
            with self.subTest(port=port):
                result, runner = self._probe(enable_ssl=False, http_port=port)
                self.assertFalse(result["frontend"]["healthy"])
                self.assertFalse(result["healthy"])
                self.assertFalse(any(call.args[0][0] == "curl" for call in runner.call_args_list))

    def test_probe_reads_generated_sectionless_ini(self):
        result, runner = self._probe()
        self.assertTrue(result["healthy"])
        self.assertEqual(result["external_url"], "https://10.100.0.100:3000/")
        self.assertTrue(result["remote_lfs_endpoint_configured"])
        self.assertEqual(runner.call_args.args[0][-1], result["external_url"])

    def test_removed_lfs_staging_directory_checks_existing_parent(self):
        temporary = "/srv/gogs/data/tmp/lfs-objects"
        result, runner = self._probe(missing=(temporary, "/srv/gogs/data/tmp"))

        self.assertTrue(result["healthy"])
        self.assertTrue(result["storage"]["paths_healthy"][temporary])
        self.assertEqual(result["storage"]["lfs_temp_access_path"], "/srv/gogs/data")
        self.assertTrue(any(
            call.args[0] == (
                "runuser", "-u", "git", "--", "test", "-r", "/srv/gogs/data",
                "-a", "-w", "/srv/gogs/data", "-a", "-x", "/srv/gogs/data",
            ) for call in runner.call_args_list
        ))

    def test_staging_and_persistent_directory_failures_remain_unhealthy(self):
        temporary = "/srv/gogs/data/tmp/lfs-objects"
        cases = [
            {"denied": (temporary,)},
            {"missing": (temporary,), "denied": ("/srv/gogs/data/tmp",)},
            {"missing": (temporary,), "non_directories": ("/srv/gogs/data/tmp",)},
            {"non_directories": (temporary,)},
            {"missing": ("/srv/gogs/data/lfs-objects",)},
        ]
        for options in cases:
            with self.subTest(options=options):
                result, _ = self._probe(**options)
                self.assertFalse(result["healthy"])

    def test_remote_probe_uses_noninteractive_sudo_and_parses_json(self):
        payload = _healthy_result()
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with (
            patch("lib.gogs_cli.validate_host", return_value=True),
            patch("lib.gogs_cli.validate_username", return_value=True),
            patch("lib.gogs_cli._resolve_connection", return_value=("deploy", None)),
            patch("lib.gogs_cli.build_ssh_command", return_value=["ssh-command"]) as build,
            patch("lib.gogs_cli.subprocess.run", return_value=completed),
        ):
            result = inspect_remote_gogs("host", None, None, 100, 10)

        self.assertEqual(result, payload)
        self.assertTrue(build.call_args.kwargs["remote_command"].startswith("sudo -n python3 -c "))

    def test_remote_script_checks_git_access_timer_and_lfs_configuration(self):
        script = _remote_health_script(100, 10)

        compile(script, "<gogs-health>", "exec")
        self.assertIn('runuser", "-u", "git"', script)
        self.assertIn('auto-update-gogs.timer', script)
        self.assertIn('timer_scheduled', script)
        self.assertIn('remote_lfs_endpoint_configured', script)
        self.assertIn('update_check_stale', script)
        self.assertIn('client_max_body_size', script)
        self.assertIn('run("nginx", "-t")', script)
        self.assertIn('frontend_healthy', script)
        self.assertIn('"--cacert", cert_path', script)

    def test_negative_threshold_is_rejected_before_ssh(self):
        with patch("lib.gogs_cli.subprocess.run") as runner:
            with self.assertRaisesRegex(ValueError, "non-negative"):
                inspect_remote_gogs("host", "root", None, -1, 0)
        runner.assert_not_called()


class TestGogsHealthOutput(unittest.TestCase):
    def test_text_output_contains_operator_facts(self):
        output = _format_health(_healthy_result(), "git.example.test")

        self.assertIn("Gogs health for git.example.test: healthy", output)
        self.assertIn("/dev/vdb1 (ext4)", output)
        self.assertIn("Remote LFS endpoint: configured", output)
        self.assertIn("Nginx: ok", output)
        self.assertIn("Public web endpoint: ok", output)

    def test_unhealthy_json_returns_nonzero(self):
        value = _healthy_result()
        value["healthy"] = False
        args = argparse.Namespace(
            gogs_command="health",
            host="host",
            username=None,
            ssh_key=None,
            min_free_bytes=1,
            min_free_inodes=1,
            json=True,
        )
        output = io.StringIO()
        with (
            patch("lib.gogs_cli.inspect_remote_gogs", return_value=value),
            redirect_stdout(output),
        ):
            result = run_gogs_command(args)

        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["healthy"])


if __name__ == "__main__":
    unittest.main()
