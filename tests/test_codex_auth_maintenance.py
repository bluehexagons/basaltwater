"""Tests for expiry-aware Codex authentication maintenance."""

from __future__ import annotations

import io
import json
import os
import shlex
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common.agent_steps import (
    _copy_secret_file,
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
    def test_command_can_renew_a_private_staged_home(self) -> None:
        with (
            patch.object(sys, "argv", ["maintenance", "--home", "/private/staged", "--codex-path", "/bin/codex"]),
            patch.object(codex_auth_maintenance, "maintain_codex_auth", return_value=0) as maintain,
        ):
            self.assertEqual(codex_auth_maintenance.main(), 0)
        maintain.assert_called_once_with(home="/private/staged", codex_path="/bin/codex")

    def test_missing_account_with_renewed_file_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self._credential_home(home)
            with (
                patch.object(codex_auth_maintenance, "inspect_codex_auth_file", side_effect=(
                    _metadata("refresh_due"), _metadata("current"),
                )),
                patch.object(codex_auth_maintenance, "refresh_codex_auth", side_effect=
                    codex_auth_maintenance.AuthRefreshError("no_account_returned")),
            ):
                self.assertEqual(codex_auth_maintenance.maintain_codex_auth(
                    home=home, codex_path=sys.executable,
                ), 0)

    def test_transient_failure_retries_but_rejected_token_does_not(self) -> None:
        for reason, expected_calls, expected_result in (
            ("transient_refresh_failure", 2, 0),
            ("refresh_token_reused", 1, 1),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as home:
                self._credential_home(home)
                with (
                    patch.object(codex_auth_maintenance, "inspect_codex_auth_file", side_effect=(
                        _metadata("refresh_due"), _metadata("refresh_due"), _metadata("current"),
                    )),
                    patch.object(codex_auth_maintenance, "refresh_codex_auth", side_effect=(
                        codex_auth_maintenance.AuthRefreshError(reason), True,
                    )) as refresh,
                ):
                    result = codex_auth_maintenance.maintain_codex_auth(home=home, codex_path=sys.executable)
                self.assertEqual(result, expected_result)
                self.assertEqual(refresh.call_count, expected_calls)

    def test_vendor_diagnostics_retain_only_fixed_categories(self) -> None:
        raw = b"secret-token " * 10000 + b'Failed to refresh token: 401 {"code":"refresh_token_reused"}'
        diagnostics = codex_auth_maintenance._RefreshDiagnostics(io.BytesIO(raw))
        diagnostics.drain()
        self.assertEqual(diagnostics.failure(), "refresh_token_reused")
        self.assertEqual(diagnostics.reasons, {"refresh_token_reused"})

    def test_refresh_failures_report_safe_categories(self) -> None:
        cases = (
            ({"error": {"code": -32000, "message": "secret-token"}}, "account_read_rpc_error", -32000),
            ({"error": {"code": "secret-token"}}, "account_read_rpc_error", None),
            ({"result": {"account": None}}, "no_account_returned", None),
            ({"result": {}}, "invalid_account_response", None),
            ({"result": {"account": {"type": "secret-token"}}}, "unexpected_account_type", None),
        )
        for response, reason, code in cases:
            with self.subTest(reason=reason, code=code), tempfile.TemporaryDirectory() as home:
                self._credential_home(home)
                with (
                    patch.object(codex_auth_maintenance.subprocess, "Popen", return_value=MagicMock()),
                    patch.object(codex_auth_maintenance, "_read_response", side_effect=({"result": {}}, response)),
                    patch.object(codex_auth_maintenance, "inspect_codex_auth_file", return_value=_metadata("refresh_due")),
                    patch.object(codex_auth_maintenance, "log_event") as log,
                ):
                    result = codex_auth_maintenance.maintain_codex_auth(home=home, codex_path=sys.executable)
                self.assertEqual(result, 1)
                self.assertEqual(log.call_args.kwargs["reason"], reason)
                self.assertEqual(log.call_args.kwargs["rpc_error_code"], code)
                self.assertNotIn("secret-token", str(log.call_args_list))

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

            assert sys.argv[1:] == ["-c", 'cli_auth_credentials_store="file"', "app-server"]
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
        for marker, expected in (
            ("", None),
            ("failed to refresh token while getting account: network error", "transient_refresh_failure"),
            ('{"code":"refresh_token_reused"}', "refresh_token_reused"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as home:
                fake_codex = os.path.join(home, "codex")
                # Exceed pipe capacity to verify stderr is drained concurrently;
                # the successful RPC deliberately hides the refresh failure.
                source = fake_source.replace(
                    "import json",
                    "import sys\n"
                    f"sys.stderr.write('secret-token ' * 10000 + {marker!r})\n"
                    "sys.stderr.flush()\nimport json",
                    1,
                )
                with open(fake_codex, "w", encoding="utf-8") as file_obj:
                    file_obj.write(source)
                os.chmod(fake_codex, 0o700)
                if expected is None:
                    self.assertTrue(codex_auth_maintenance.refresh_codex_auth(fake_codex, home))
                else:
                    with self.assertRaises(codex_auth_maintenance.AuthRefreshError) as raised:
                        codex_auth_maintenance.refresh_codex_auth(fake_codex, home)
                    self.assertEqual(raised.exception.reason, expected)
                    self.assertNotIn("secret-token", str(raised.exception))


class TestCodexAuthMaintenanceSetup(unittest.TestCase):
    def test_setup_renews_stale_source_without_risking_live_credentials(self) -> None:
        for outcome in ("renewed", "rejected", "same_token", "concurrent_login"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as home:
                source = os.path.join(home, "source.json")
                destination = os.path.join(home, "auth.json")
                source_auth = {
                    "auth_mode": "chatgpt", "last_refresh": "2020-01-01T00:00:00Z",
                    "tokens": {"refresh_token": "candidate-secret", "access_token": "old-access"},
                }
                target_auth = dict(source_auth, tokens={"refresh_token": "target-secret"})
                if outcome == "same_token":
                    target_auth = source_auth
                for path, content in ((source, source_auth), (destination, target_auth)):
                    with open(path, "w", encoding="utf-8") as stream:
                        json.dump(content, stream)

                def renew(_user: str, _home: str, command: str, **_kwargs: object) -> SimpleNamespace:
                    args = shlex.split(command)
                    staged_home = args[args.index("--home") + 1]
                    candidate = os.path.join(staged_home, ".codex", "auth.json")
                    with open(destination, encoding="utf-8") as stream:
                        self.assertEqual(json.load(stream), target_auth)
                    self.assertEqual(os.stat(candidate).st_mode & 0o777, 0o600)
                    if outcome != "rejected":
                        renewed = dict(source_auth, last_refresh=datetime.now(timezone.utc).isoformat())
                        with open(candidate, "w", encoding="utf-8") as stream:
                            json.dump(renewed, stream)
                        if outcome == "concurrent_login":
                            with open(destination, "w", encoding="utf-8") as stream:
                                json.dump(dict(target_auth, last_refresh=renewed["last_refresh"]), stream)
                    return SimpleNamespace(returncode=int(outcome == "rejected"), stdout="", stderr="")

                config = SetupConfig(host="host", username="agent", system_type="server_dev")
                output = io.StringIO()
                with (
                    patch("common.agent_steps._user_home", return_value=home),
                    patch("common.agent_steps._chown_path"),
                    patch("common.agent_steps._chown_user_directory_chain"),
                    patch("common.agent_steps._tool_path", return_value=sys.executable),
                    patch("common.agent_steps._run_as_login_user", side_effect=renew) as run,
                    redirect_stdout(output),
                ):
                    changed = _copy_secret_file(config, source, destination, "Codex", credential_tool="codex")
                self.assertEqual(changed, outcome == "renewed")
                self.assertEqual(run.call_count, 0 if outcome == "same_token" else 1)
                with open(source, encoding="utf-8") as stream:
                    self.assertEqual(json.load(stream), source_auth)
                with open(destination, encoding="utf-8") as stream:
                    expected = source_auth if outcome == "renewed" else target_auth
                    self.assertEqual(json.load(stream)["tokens"], expected["tokens"])
                self.assertEqual(sorted(os.listdir(home)), ["auth.json", "source.json"])
                self.assertNotIn("candidate-secret", output.getvalue())
                self.assertNotIn("target-secret", output.getvalue())

    def test_setup_replaces_due_credentials_with_current_supplied_source(self) -> None:
        for target_status in ("refresh_due", "expires_soon", "current"):
            with self.subTest(target_status=target_status), tempfile.TemporaryDirectory() as home:
                source = os.path.join(home, "source.json")
                destination = os.path.join(home, "auth.json")
                for path, content in ((source, "new"), (destination, "old")):
                    with open(path, "w", encoding="utf-8") as stream:
                        stream.write(content)
                config = SetupConfig(host="host", username="agent", system_type="server_dev")
                with (
                    patch("common.agent_steps._user_home", return_value=home),
                    patch("common.agent_steps._chown_path"),
                    patch("common.agent_steps._chown_user_directory_chain"),
                    patch("common.agent_steps.inspect_codex_auth_file", side_effect=(
                        _metadata(target_status), _metadata("current"),
                    )),
                ):
                    changed = _copy_secret_file(config, source, destination, "Codex", credential_tool="codex")
                with open(destination, encoding="utf-8") as stream:
                    self.assertEqual(stream.read(), "old" if target_status == "current" else "new")
                self.assertEqual(changed, target_status != "current")

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
