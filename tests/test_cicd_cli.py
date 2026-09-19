"""Tests for controller-side CI/CD connection management."""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import ANY, patch

import basaltwater
from lib.cicd_cli import (
    _APP_KEY_INSTALL_SCRIPT,
    _BUILD_TARGET_INSTALL_SCRIPT,
    _load_role,
    _validate_base_dir,
    connect_build_to_app,
    run_cicd_command,
)
from lib.config import SetupConfig


def _build_config() -> SetupConfig:
    return SetupConfig(
        host="build.example.test",
        username="admin",
        system_type="server_web",
        is_build_server=True,
    )


def _app_config() -> SetupConfig:
    return SetupConfig(
        host="app.example.test",
        username="admin",
        system_type="server_web",
        is_app_server=True,
    )


class TestCicdParser(unittest.TestCase):
    def test_parser_accepts_connection_options(self):
        parser, _setup, _patch = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
            [
                "cicd",
                "connect",
                "build",
                "app",
                "--target-name",
                "production",
                "--port",
                "2222",
                "--base-dir",
                "/srv/apps",
                "--fingerprint",
                "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            ]
        )

        self.assertEqual(args.cicd_command, "connect")
        self.assertEqual(args.target_name, "production")
        self.assertEqual(args.port, 2222)
        self.assertEqual(args.base_dir, "/srv/apps")

    def test_parser_accepts_status_json(self):
        parser, _setup, _patch = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["cicd", "status", "build", "--json"])
        self.assertEqual(args.cicd_command, "status")
        self.assertTrue(args.json)


class TestCicdConnectionValidation(unittest.TestCase):
    def test_remote_scripts_compile(self):
        compile(_APP_KEY_INSTALL_SCRIPT, "<app-key-install>", "exec")
        compile(_BUILD_TARGET_INSTALL_SCRIPT, "<build-target-install>", "exec")

    def test_base_dir_must_be_normalized_absolute_child(self):
        for value in ("relative", "/", "/srv/../var/www", "/var/www/"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _validate_base_dir(value)

    @patch("lib.cicd_cli.load_setup_command")
    def test_role_mismatch_is_rejected(self, load):
        load.return_value = _app_config()
        with self.assertRaisesRegex(ValueError, "--build-server"):
            _load_role("app", build_server=True)

    @patch("lib.cicd_cli._test_target")
    @patch("lib.cicd_cli._install_build_target")
    @patch("lib.cicd_cli._install_app_public_key")
    @patch("lib.cicd_cli._fetch_build_public_key", return_value="ssh-ed25519 AAAA deploy")
    @patch(
        "lib.cicd_cli._ensure_app_trust",
        return_value=["app.example.test ssh-ed25519 AAAA"],
    )
    @patch("lib.cicd_cli.is_host_key_enrolled", return_value=True)
    @patch("lib.cicd_cli._load_role")
    def test_connect_orchestrates_trust_key_target_and_test(
        self,
        load_role,
        _enrolled,
        ensure_trust,
        fetch_public_key,
        install_app_key,
        install_build_target,
        test_target,
    ):
        build = _build_config()
        load_role.side_effect = [build, _app_config()]

        target = connect_build_to_app(
            "build",
            "app",
            target_name="production",
            base_dir="/srv/apps",
        )

        self.assertEqual(target, "production")
        ensure_trust.assert_called_once_with("app.example.test", 22, None)
        fetch_public_key.assert_called_once()
        install_app_key.assert_called_once_with(
            ANY,
            "ssh-ed25519 AAAA deploy",
            22,
            "/srv/apps",
        )
        self.assertEqual(install_build_target.call_args.kwargs["target_name"], "production")
        test_target.assert_called_once_with(build, "production")

    @patch("lib.cicd_cli._load_remote_targets")
    @patch("lib.cicd_cli._load_role", return_value=_build_config())
    def test_status_json_is_stable(self, _load_role_mock, load_targets):
        load_targets.return_value = {
            "production": {
                "host": "app.example.test",
                "user": "deploy",
                "ssh_port": 22,
                "base_dir": "/var/www",
            }
        }
        args = argparse.Namespace(
            cicd_command="status",
            build="build",
            json=True,
        )
        output = io.StringIO()

        with redirect_stdout(output):
            result = run_cicd_command(args)

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), load_targets.return_value)


if __name__ == "__main__":
    unittest.main()
