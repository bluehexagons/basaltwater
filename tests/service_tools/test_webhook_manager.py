"""Tests for web.service_tools.webhook_manager."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from web.service_tools import webhook_manager


def _args(**values: object) -> SimpleNamespace:
    """Build a command namespace without coupling tests to argparse internals."""
    defaults = {
        "url": "https://example.com/repository.git",
        "branches": None,
        "install": None,
        "build": None,
        "test": None,
        "deploy": None,
        "service": None,
        "follow": False,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class TestConfigurationStorage(unittest.TestCase):
    def test_missing_config_defaults_to_empty_repository_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "webhook-config.json")
            with patch.object(webhook_manager, "CONFIG_FILE", config_path):
                self.assertEqual(webhook_manager.load_config(), {"repositories": []})

    def test_config_round_trip_uses_atomic_writer(self):
        config = {"repositories": [{"url": "https://example.com/repo.git"}]}
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "webhook-config.json")
            with patch.object(webhook_manager, "CONFIG_FILE", config_path), patch(
                'web.service_tools.cicd_config.grp.getgrnam', return_value=SimpleNamespace(gr_gid=1234)
            ), patch('lib.atomic_io.os.fchown'):
                webhook_manager.save_config(config)
                self.assertEqual(webhook_manager.load_config(), config)

    def test_malformed_config_is_reported_by_json_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "webhook-config.json")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write("not-json")
            with patch.object(webhook_manager, "CONFIG_FILE", config_path):
                with self.assertRaises(json.JSONDecodeError):
                    webhook_manager.load_config()


class TestRepositoryCommands(unittest.TestCase):
    def test_list_repositories_reports_branches_and_scripts(self):
        config = {
            "repositories": [
                {
                    "url": "https://example.com/repo.git",
                    "branches": ["main", "release"],
                    "scripts": {"build": "./build.sh", "test": "./test.sh"},
                }
            ]
        }
        output = StringIO()
        with patch.object(webhook_manager, "load_config", return_value=config), redirect_stdout(output):
            result = webhook_manager.list_repositories(_args())

        self.assertEqual(result, 0)
        self.assertIn("Configured repositories (1)", output.getvalue())
        self.assertIn("Branches: main, release", output.getvalue())
        self.assertIn("Scripts: build, test", output.getvalue())

    def test_list_repositories_reports_empty_state(self):
        output = StringIO()
        with patch.object(webhook_manager, "load_config", return_value={}), redirect_stdout(output):
            result = webhook_manager.list_repositories(_args())

        self.assertEqual(result, 0)
        self.assertIn("No repositories configured", output.getvalue())

    def test_add_repository_defaults_branch_and_keeps_selected_scripts(self):
        config = {"repositories": []}
        args = _args(
            branches=None,
            install="./install.sh",
            build="./build.sh",
            test="./test.sh",
            deploy="./deploy.sh",
        )
        with patch.object(webhook_manager, "load_config", return_value=config), patch.object(
            webhook_manager, "save_config"
        ) as save_config:
            result = webhook_manager.add_repository(args)

        self.assertEqual(result, 0)
        saved = save_config.call_args.args[0]
        self.assertEqual(saved["repositories"][0]["branches"], ["main"])
        self.assertEqual(
            saved["repositories"][0]["scripts"],
            {
                "install": "./install.sh",
                "build": "./build.sh",
                "test": "./test.sh",
                "deploy": "./deploy.sh",
            },
        )

    def test_add_repository_accepts_custom_branches(self):
        config = {"repositories": []}
        args = _args(branches=["main", "develop"])
        with patch.object(webhook_manager, "load_config", return_value=config), patch.object(
            webhook_manager, "save_config"
        ) as save_config:
            self.assertEqual(webhook_manager.add_repository(args), 0)

        self.assertEqual(
            save_config.call_args.args[0]["repositories"][0]["branches"],
            ["main", "develop"],
        )

    def test_add_repository_rejects_duplicate_without_saving(self):
        config = {"repositories": [{"url": "https://example.com/repository.git"}]}
        with patch.object(webhook_manager, "load_config", return_value=config), patch.object(
            webhook_manager, "save_config"
        ) as save_config:
            self.assertEqual(webhook_manager.add_repository(_args()), 1)

        save_config.assert_not_called()

    def test_remove_repository_handles_present_and_missing_urls(self):
        cases = (
            (
                "https://example.com/repository.git",
                0,
                [{"url": "https://other.example/repository.git"}],
            ),
            ("https://missing.example/repository.git", 1, None),
        )
        for url, expected_result, expected_repositories in cases:
            with self.subTest(url=url):
                config = {
                    "repositories": [
                        {"url": "https://example.com/repository.git"},
                        {"url": "https://other.example/repository.git"},
                    ]
                }
                with patch.object(webhook_manager, "load_config", return_value=config), patch.object(
                    webhook_manager, "save_config"
                ) as save_config:
                    result = webhook_manager.remove_repository(_args(url=url))

                self.assertEqual(result, expected_result)
                if expected_repositories is None:
                    save_config.assert_not_called()
                else:
                    self.assertEqual(
                        save_config.call_args.args[0]["repositories"],
                        expected_repositories,
                    )

    def test_test_configuration_reports_absolute_and_relative_script_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing_script = os.path.join(tmp, "build.sh")
            with open(existing_script, "w", encoding="utf-8"):
                pass
            config = {
                "repositories": [
                    {
                        "url": "https://example.com/repository.git",
                        "scripts": {
                            "build": existing_script,
                            "deploy": os.path.join(tmp, "missing.sh"),
                            "test": "./test.sh",
                        },
                    }
                ]
            }
            output = StringIO()
            with patch.object(webhook_manager, "load_config", return_value=config), redirect_stdout(output):
                result = webhook_manager.test_configuration(_args())

        self.assertEqual(result, 0)
        self.assertIn("build.sh ✓", output.getvalue())
        self.assertIn("missing.sh ✗ (not found)", output.getvalue())
        self.assertIn("./test.sh ⚠️", output.getvalue())

    def test_test_configuration_rejects_unknown_repository(self):
        output = StringIO()
        with patch.object(webhook_manager, "load_config", return_value={}), redirect_stdout(output):
            result = webhook_manager.test_configuration(_args())

        self.assertEqual(result, 1)
        self.assertIn("Repository not configured", output.getvalue())


class TestServiceCommands(unittest.TestCase):
    def test_show_secret_reads_and_strips_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = os.path.join(tmp, "webhook_secret")
            with open(secret_path, "w", encoding="utf-8") as secret_file:
                secret_file.write("secret-value\n")
            output = StringIO()
            with patch.object(webhook_manager, "SECRET_FILE", secret_path), redirect_stdout(output):
                result = webhook_manager.show_secret(_args())

        self.assertEqual(result, 0)
        self.assertIn("secret-value", output.getvalue())
        self.assertEqual(output.getvalue().count("secret-value"), 1)

    def test_show_secret_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            with patch.object(
                webhook_manager, "SECRET_FILE", os.path.join(tmp, "missing-secret")
            ), redirect_stdout(output):
                result = webhook_manager.show_secret(_args())

        self.assertEqual(result, 1)
        self.assertIn("Secret file not found", output.getvalue())

    def test_show_logs_builds_follow_command(self):
        with patch.object(webhook_manager.subprocess, "run") as run_command:
            result = webhook_manager.show_logs(
                _args(service="cicd-executor", follow=True)
            )

        self.assertEqual(result, 0)
        run_command.assert_called_once_with(
            ["journalctl", "-u", "cicd-executor.service", "-n", "50", "-f"]
        )

    def test_show_logs_returns_cleanly_for_interrupt_and_error(self):
        for failure in (KeyboardInterrupt(), RuntimeError("journal unavailable")):
            with self.subTest(failure=type(failure).__name__):
                output = StringIO()
                with patch.object(webhook_manager.subprocess, "run", side_effect=failure), redirect_stdout(output):
                    result = webhook_manager.show_logs(_args())

                self.assertEqual(result, 0 if isinstance(failure, KeyboardInterrupt) else 1)
                if isinstance(failure, RuntimeError):
                    self.assertIn("Error: journal unavailable", output.getvalue())

    def test_show_logs_rejects_unknown_service(self):
        output = StringIO()
        with redirect_stdout(output):
            result = webhook_manager.show_logs(_args(service="unknown"))

        self.assertEqual(result, 1)
        self.assertIn("Available services", output.getvalue())

    def test_show_status_reports_service_output_and_health(self):
        service_result = SimpleNamespace(stdout="line one\nline two\n", returncode=0)
        health_response = MagicMock()
        health_response.__enter__.return_value.status = 200
        output = StringIO()
        with patch.object(webhook_manager.subprocess, "run", return_value=service_result) as run_command, patch(
            "web.service_tools.webhook_manager.open_loopback", return_value=health_response
        ) as open_loopback, redirect_stdout(output):
            result = webhook_manager.show_status(_args())

        self.assertEqual(result, 0)
        self.assertEqual(run_command.call_count, 2)
        open_loopback.assert_called_once_with("http://127.0.0.1:8765/health", timeout=2)
        self.assertIn("✓ Webhook receiver is responding", output.getvalue())

    def test_show_status_reports_non_200_and_health_error(self):
        service_result = SimpleNamespace(stdout="status output\n", returncode=1)
        for health_result, expected_text in (
            (SimpleNamespace(status=503), "returned status 503"),
            (OSError("connection refused"), "is not responding: connection refused"),
        ):
            with self.subTest(expected_text=expected_text):
                output = StringIO()
                if isinstance(health_result, BaseException):
                    open_loopback_patch = patch(
                        "web.service_tools.webhook_manager.open_loopback",
                        side_effect=health_result,
                    )
                else:
                    response = MagicMock()
                    response.__enter__.return_value = health_result
                    open_loopback_patch = patch(
                        "web.service_tools.webhook_manager.open_loopback",
                        return_value=response,
                    )
                with patch.object(
                    webhook_manager.subprocess, "run", return_value=service_result
                ), open_loopback_patch, redirect_stdout(output):
                    result = webhook_manager.show_status(_args())

                self.assertEqual(result, 0)
                self.assertIn(expected_text, output.getvalue())


class TestMainDispatch(unittest.TestCase):
    def test_main_requires_root_for_privileged_commands(self):
        with patch.object(sys, "argv", ["webhook-manager.py", "status"]), patch.object(
            webhook_manager.os, "geteuid", return_value=1000
        ):
            self.assertEqual(webhook_manager.main(), 1)

    def test_main_dispatches_unprivileged_test_command(self):
        with patch.object(
            sys,
            "argv",
            ["webhook-manager.py", "test", "https://example.com/repository.git"],
        ), patch.object(
            webhook_manager, "test_configuration", return_value=0
        ) as test_configuration:
            result = webhook_manager.main()

        self.assertEqual(result, 0)
        test_configuration.assert_called_once()
        self.assertEqual(
            test_configuration.call_args.args[0].url,
            "https://example.com/repository.git",
        )

    def test_main_rejects_missing_command(self):
        with patch.object(sys, "argv", ["webhook-manager.py"]), patch(
            "argparse.ArgumentParser.print_help"
        ) as print_help:
            self.assertEqual(webhook_manager.main(), 1)

        print_help.assert_called_once()


if __name__ == "__main__":
    unittest.main()
