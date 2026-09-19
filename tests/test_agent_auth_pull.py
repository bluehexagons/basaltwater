"""Tests for pulling agent credentials from an existing VM."""

from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_auth
from lib.agent_cli import add_agent_subparser, run_agent_command


def _codex_payload(*, expired: bool) -> bytes:
    claims = {"exp": 1 if expired else 4_102_444_800}
    token_payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    token = f"e30.{token_payload}.signature"
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "last_refresh": "2000-01-01T00:00:00Z" if expired else "2099-01-01T00:00:00Z",
            "tokens": {"access_token": token, "refresh_token": "fixture"},
        }
    ).encode("utf-8")


class AgentAuthPullTests(unittest.TestCase):
    def test_parser_exposes_pull_options(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)

        args = parser.parse_args(
            [
                "agent",
                "auth",
                "pull",
                "vm.example",
                "agent",
                "--output-dir",
                "/run/user/1000/credentials",
                "--tool",
                "codex",
                "--port",
                "2222",
                "--overwrite",
            ]
        )

        self.assertEqual(args.agent_auth_command, "pull")
        self.assertEqual(args.agent_auth_tools, ["codex"])
        self.assertEqual(args.port, 2222)
        self.assertTrue(args.overwrite)

    def test_parser_defaults_to_active_user_paths(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)

        args = parser.parse_args(["agent", "auth", "pull", "vm.example", "agent"])

        self.assertIsNone(args.output_dir)

    def test_dispatches_pull(self) -> None:
        args = argparse.Namespace(
            agent_command="auth",
            agent_auth_command="pull",
        )
        with patch("lib.agent_auth.run_agent_auth_pull", return_value=7) as pull:
            self.assertEqual(run_agent_command(args), 7)
        pull.assert_called_once_with(args)

    def test_remote_reader_accepts_a_private_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            credential_dir = home / ".codex"
            credential_dir.mkdir(mode=0o700)
            credential = credential_dir / "auth.json"
            credential.write_bytes(b'{"fixture": true}\n')
            credential.chmod(0o600)
            script = agent_auth._remote_pull_script("codex")
            result = subprocess.run(
                ["python3", "-c", script],
                check=False,
                capture_output=True,
                env={**os.environ, "HOME": directory},
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'{"fixture": true}\n')

    def test_remote_reader_rejects_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            credential_dir = home / ".codex"
            credential_dir.mkdir(mode=0o700)
            source = home / "source.json"
            source.write_bytes(b'{"fixture": true}\n')
            (credential_dir / "auth.json").symlink_to(source)
            result = subprocess.run(
                ["python3", "-c", agent_auth._remote_pull_script("codex")],
                check=False,
                capture_output=True,
                env={**os.environ, "HOME": directory},
            )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_pull_writes_private_files_and_skips_absent_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "credentials"

            def run_remote(_host, _username, _key, script, payload=None, port=None):
                if ".codex/auth.json" in script:
                    return subprocess.CompletedProcess([], 0, b'{"fixture": true}\n', b"")
                return subprocess.CompletedProcess([], 3, b"", b"")

            with (
                redirect_stdout(io.StringIO()),
                patch.object(agent_auth, "_run_remote_script", side_effect=run_remote),
            ):
                result = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=list(agent_auth.AGENT_AUTH_TOOLS),
                    output_dir=str(output),
                    ssh_key=None,
                )

            self.assertEqual(result, 0)
            destination = output / "codex-auth.json"
            self.assertEqual(destination.read_bytes(), b'{"fixture": true}\n')
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_explicit_missing_credential_fails(self) -> None:
        missing = subprocess.CompletedProcess([], 3, b"", b"")
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
            patch.object(agent_auth, "_run_remote_script", return_value=missing),
        ):
            result = agent_auth.pull_agent_credentials(
                host="vm.example",
                username="agent",
                tools=["codex"],
                output_dir=os.path.join(directory, "credentials"),
                ssh_key=None,
                explicit_tools=True,
            )

        self.assertEqual(result, 1)

    def test_failure_never_echoes_remote_output(self) -> None:
        failure = subprocess.CompletedProcess(
            [], 4, b"credential material", b"credential material"
        )
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stderr(output),
            patch.object(agent_auth, "_run_remote_script", return_value=failure),
        ):
            result = agent_auth.pull_agent_credentials(
                host="vm.example",
                username="agent",
                tools=["codex"],
                output_dir=os.path.join(directory, "credentials"),
                ssh_key=None,
            )

        self.assertEqual(result, 1)
        self.assertNotIn("credential material", output.getvalue())

    def test_transport_failure_stops_without_echoing_exception_details(self) -> None:
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stderr(output),
            patch.object(
                agent_auth,
                "_run_remote_script",
                side_effect=subprocess.TimeoutExpired(["secret command"], 60),
            ) as remote,
        ):
            result = agent_auth.pull_agent_credentials(
                host="vm.example",
                username="agent",
                tools=["codex", "claude"],
                output_dir=os.path.join(directory, "credentials"),
                ssh_key=None,
            )

        self.assertEqual(result, 1)
        remote.assert_called_once()
        self.assertNotIn("secret command", output.getvalue())

    def test_existing_destination_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "credentials"
            output.mkdir(mode=0o700)
            destination = output / "codex-auth.json"
            destination.write_bytes(b"old")
            destination.chmod(0o600)
            success = subprocess.CompletedProcess([], 0, b"new", b"")
            with (
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                patch.object(agent_auth, "_run_remote_script", return_value=success),
            ):
                refused = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=str(output),
                    ssh_key=None,
                )
                overwritten = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=str(output),
                    ssh_key=None,
                    overwrite=True,
                )

            self.assertEqual(refused, 1)
            self.assertEqual(overwritten, 0)
            self.assertEqual(destination.read_bytes(), b"new")

    def test_default_destination_is_the_active_user_credential_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / ".codex" / "auth.json"
            success = subprocess.CompletedProcess([], 0, b"new", b"")
            with (
                redirect_stdout(io.StringIO()),
                patch.object(agent_auth, "_run_remote_script", return_value=success),
                patch.object(
                    agent_auth,
                    "_active_source_path",
                    return_value=str(destination),
                ),
            ):
                result = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=None,
                    ssh_key=None,
                )

            self.assertEqual(result, 0)
            self.assertEqual(destination.read_bytes(), b"new")
            self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_default_destination_accepts_owned_nonwritable_vendor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / ".codex"
            parent.mkdir(mode=0o755)
            destination = parent / "auth.json"
            success = subprocess.CompletedProcess([], 0, b"new", b"")
            with (
                redirect_stdout(io.StringIO()),
                patch.object(agent_auth, "_run_remote_script", return_value=success),
                patch.object(
                    agent_auth,
                    "_active_source_path",
                    return_value=str(destination),
                ),
            ):
                result = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=None,
                    ssh_key=None,
                )

            self.assertEqual(result, 0)
            self.assertEqual(destination.read_bytes(), b"new")

    def test_current_codex_pull_automatically_refreshes_expired_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "credentials"
            output.mkdir(mode=0o700)
            destination = output / "codex-auth.json"
            destination.write_bytes(_codex_payload(expired=True))
            destination.chmod(0o600)
            current = _codex_payload(expired=False)
            success = subprocess.CompletedProcess([], 0, current, b"")
            with (
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                patch.object(agent_auth, "_run_remote_script", return_value=success),
            ):
                result = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=str(output),
                    ssh_key=None,
                )

            self.assertEqual(result, 0)
            self.assertEqual(destination.read_bytes(), current)

    def test_both_expired_codex_credentials_report_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "credentials"
            output.mkdir(mode=0o700)
            destination = output / "codex-auth.json"
            expired = _codex_payload(expired=True)
            destination.write_bytes(expired)
            destination.chmod(0o600)
            error = io.StringIO()
            success = subprocess.CompletedProcess([], 0, expired, b"")
            with (
                redirect_stdout(io.StringIO()),
                redirect_stderr(error),
                patch.object(agent_auth, "_run_remote_script", return_value=success),
            ):
                result = agent_auth.pull_agent_credentials(
                    host="vm.example",
                    username="agent",
                    tools=["codex"],
                    output_dir=str(output),
                    ssh_key=None,
                    overwrite=True,
                )

            self.assertEqual(result, 1)
            self.assertIn("both expired", error.getvalue())
            self.assertEqual(destination.read_bytes(), expired)

    def test_rejects_unsafe_target_and_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Invalid IP"):
                agent_auth.pull_agent_credentials(
                    host="bad host",
                    username="agent",
                    tools=["codex"],
                    output_dir=directory,
                    ssh_key=None,
                )
            output = Path(directory) / "credentials"
            output.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "mode 0700"):
                agent_auth._private_output_directory(str(output))

    def test_declined_codex_replacement_explains_freshness_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            destination = output / 'codex-auth.json'
            current = _codex_payload(expired=False)
            destination.write_bytes(current)
            destination.chmod(0o600)
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error), \
                 patch.object(agent_auth, '_run_remote_script', return_value=subprocess.CompletedProcess([], 0, current, b'')):
                result = agent_auth.pull_agent_credentials(host='vm.example', username='agent',
                    tools=['codex'], output_dir=directory, ssh_key=None)
            self.assertEqual(result, 1)
            self.assertIn('source freshness: current; destination freshness: current', error.getvalue())
            self.assertIn('--overwrite', error.getvalue())
            self.assertNotIn('signature', error.getvalue())
            self.assertEqual(destination.read_bytes(), current)


if __name__ == "__main__":
    unittest.main()
