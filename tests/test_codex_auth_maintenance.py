"""Tests for expiry-aware Codex authentication maintenance."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from common.agent_steps import (
    configure_codex_auth_maintenance,
    run_codex_auth_maintenance,
)
from common.service_tools import codex_auth_maintenance
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


def _metadata(
    status: str,
    *,
    auth_mode: str | None = "chatgpt",
    refresh_token: bool = True,
) -> dict[str, object]:
    return {
        "status": status,
        "auth_mode": auth_mode,
        "refresh_token_present": refresh_token,
        "warnings": [],
    }


class TestCodexAuthMaintenance(unittest.TestCase):
    def _credential_home(self, directory: str) -> str:
        codex_home = os.path.join(directory, ".codex")
        os.mkdir(codex_home)
        os.chmod(codex_home, 0o700)
        with open(os.path.join(codex_home, "auth.json"), "w", encoding="utf-8") as file_obj:
            file_obj.write("{}")
        os.chmod(os.path.join(codex_home, "auth.json"), 0o600)
        return directory

    def test_missing_credentials_are_an_expected_noop(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = codex_auth_maintenance.maintain_codex_auth(home=home)

        self.assertEqual(result, 0)

    def test_current_and_api_key_credentials_are_not_refreshed(self) -> None:
        for metadata in (
            _metadata("current"),
            _metadata("current", auth_mode="api_key", refresh_token=False),
        ):
            with (
                self.subTest(auth_mode=metadata["auth_mode"]),
                tempfile.TemporaryDirectory() as home,
            ):
                self._credential_home(home)
                with (
                    patch(
                        "common.service_tools.codex_auth_maintenance."
                        "inspect_codex_auth_file",
                        return_value=metadata,
                    ),
                    patch(
                        "common.service_tools.codex_auth_maintenance."
                        "refresh_codex_auth",
                    ) as refresh,
                ):
                    result = codex_auth_maintenance.maintain_codex_auth(home=home)

            self.assertEqual(result, 0)
            refresh.assert_not_called()

    def test_stale_chatgpt_credentials_are_refreshed_and_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._credential_home(home)
            with (
                patch(
                    "common.service_tools.codex_auth_maintenance."
                    "inspect_codex_auth_file",
                    side_effect=(
                        _metadata("refresh_required"),
                        _metadata("current"),
                    ),
                ) as inspect,
                patch(
                    "common.service_tools.codex_auth_maintenance."
                    "refresh_codex_auth",
                    return_value=True,
                ) as refresh,
            ):
                result = codex_auth_maintenance.maintain_codex_auth(
                    home=home,
                    codex_path="/usr/bin/codex-test",
                )

        self.assertEqual(result, 0)
        self.assertEqual(inspect.call_count, 2)
        refresh.assert_called_once_with("/usr/bin/codex-test", home)

    def test_invalid_or_unrefreshable_credentials_fail_visibly(self) -> None:
        for metadata in (
            _metadata("invalid", auth_mode=None, refresh_token=False),
            _metadata("refresh_required", refresh_token=False),
            _metadata("unknown", auth_mode=None, refresh_token=False),
        ):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as home:
                self._credential_home(home)
                with patch(
                    "common.service_tools.codex_auth_maintenance."
                    "inspect_codex_auth_file",
                    return_value=metadata,
                ):
                    result = codex_auth_maintenance.maintain_codex_auth(home=home)

            self.assertEqual(result, 1)

    def test_insecure_credential_permissions_are_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._credential_home(home)
            auth_path = os.path.join(home, ".codex", "auth.json")
            os.chmod(auth_path, 0o640)
            with patch(
                "common.service_tools.codex_auth_maintenance."
                "inspect_codex_auth_file",
            ) as inspect:
                result = codex_auth_maintenance.maintain_codex_auth(home=home)

        self.assertEqual(result, 1)
        inspect.assert_not_called()

    def test_writable_credential_directory_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._credential_home(home)
            os.chmod(os.path.join(home, ".codex"), 0o775)
            with patch(
                "common.service_tools.codex_auth_maintenance."
                "inspect_codex_auth_file",
            ) as inspect:
                result = codex_auth_maintenance.maintain_codex_auth(home=home)

        self.assertEqual(result, 1)
        inspect.assert_not_called()

    def test_refresh_must_leave_current_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._credential_home(home)
            with (
                patch(
                    "common.service_tools.codex_auth_maintenance."
                    "inspect_codex_auth_file",
                    side_effect=(
                        _metadata("expires_soon"),
                        _metadata("expires_soon"),
                    ),
                ),
                patch(
                    "common.service_tools.codex_auth_maintenance."
                    "refresh_codex_auth",
                    return_value=True,
                ),
            ):
                result = codex_auth_maintenance.maintain_codex_auth(
                    home=home,
                    codex_path="/usr/bin/codex-test",
                )

        self.assertEqual(result, 1)

    def test_app_server_protocol_requests_managed_refresh(self) -> None:
        # The maintenance process intentionally uses a restricted PATH. Use the
        # active test interpreter directly so this fixture also works when CI's
        # Python lives outside the standard system binary directories.
        fake_source = f"#!{sys.executable}\n" + textwrap.dedent(
            """\
            import json
            import sys

            initialize = json.loads(sys.stdin.buffer.readline())
            assert initialize["method"] == "initialize"
            print(json.dumps({"id": 99, "result": {"ignored": True}}), flush=True)
            print(json.dumps({"id": initialize["id"], "result": {}}), flush=True)
            initialized = json.loads(sys.stdin.buffer.readline())
            assert initialized["method"] == "initialized"
            account = json.loads(sys.stdin.buffer.readline())
            assert account["method"] == "account/read"
            assert account["params"] == {"refreshToken": True}
            print(json.dumps({
                "id": account["id"],
                "result": {
                    "account": {"type": "chatgpt"},
                    "requiresOpenaiAuth": True,
                },
            }), flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as home:
            fake_codex = os.path.join(home, "codex")
            with open(fake_codex, "w", encoding="utf-8") as file_obj:
                file_obj.write(fake_source)
            os.chmod(fake_codex, 0o700)

            refreshed = codex_auth_maintenance.refresh_codex_auth(
                fake_codex,
                home,
            )

        self.assertTrue(refreshed)


class TestCodexAuthMaintenanceSetup(unittest.TestCase):
    def test_configures_non_root_persistent_timer(self) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            install_codex=True,
        )
        with (
            patch(
                "common.agent_steps.pwd.getpwnam",
                return_value=SimpleNamespace(pw_dir="/home/agent"),
            ),
            patch(
                "common.agent_steps.configure_maintenance_timer",
                return_value=True,
            ) as configure,
        ):
            configure_codex_auth_maintenance(config)

        arguments = configure.call_args.kwargs
        self.assertEqual(arguments["service_name"], "codex-auth-maintenance")
        self.assertEqual(arguments["schedule"], "daily")
        self.assertEqual(arguments["on_boot_sec"], "15min")
        self.assertEqual(arguments["user"], "agent")
        self.assertEqual(arguments["environment"]["HOME"], "/home/agent")
        self.assertEqual(arguments["environment"]["CODEX_HOME"], "/home/agent/.codex")
        self.assertTrue(arguments["sandbox_user_service"])
        self.assertEqual(arguments["writable_paths"], ("/home/agent/.codex",))
        self.assertEqual(arguments["timeout"], "2min")

    def test_setup_runs_one_auth_freshness_check_as_target_user(self) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            install_codex=True,
        )
        result = SimpleNamespace(
            returncode=0,
            stdout="Codex authentication is current\n",
            stderr="",
        )
        with (
            patch(
                "common.agent_steps._user_home",
                return_value="/home/agent",
            ),
            patch(
                "common.agent_steps._run_as_login_user",
                return_value=result,
            ) as run_as_user,
            redirect_stdout(io.StringIO()),
        ):
            run_codex_auth_maintenance(config)

        run_as_user.assert_called_once()
        self.assertEqual(run_as_user.call_args.args[:2], ("agent", "/home/agent"))
        self.assertIn("codex_auth_maintenance.py", run_as_user.call_args.args[2])
        self.assertTrue(run_as_user.call_args.kwargs["capture_output"])

    def test_setup_auth_check_warns_but_does_not_mask_update_failure(self) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            install_codex=True,
        )
        result = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Codex authentication refresh failed\n",
        )
        error_output = io.StringIO()
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch("common.agent_steps._run_as_login_user", return_value=result),
            redirect_stderr(error_output),
        ):
            run_codex_auth_maintenance(config)

        self.assertIn("remains unhealthy", error_output.getvalue())

    def test_agent_setup_places_maintenance_after_auth_payload(self) -> None:
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            install_codex=True,
            agent_tools=["codex"],
            agent_payload=True,
        )

        step_names = [name for name, _function in get_steps_for_system_type(config)]

        maintenance = "Configuring Codex authentication maintenance"
        self.assertIn(maintenance, step_names)
        self.assertLess(
            step_names.index("Copying agent tool configuration"),
            step_names.index(maintenance),
        )
        self.assertLess(
            step_names.index("Configuring Codex security policy"),
            step_names.index(maintenance),
        )
        freshness = "Checking Codex authentication freshness"
        self.assertIn(freshness, step_names)
        self.assertLess(step_names.index(maintenance), step_names.index(freshness))
        self.assertLess(
            step_names.index(freshness),
            step_names.index("Updating managed agent tools"),
        )


if __name__ == "__main__":
    unittest.main()
