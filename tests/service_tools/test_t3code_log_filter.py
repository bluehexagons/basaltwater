"""Credential filtering tests for managed T3 Code services."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import cachyos_t3
from common import t3code_steps
from common.service_tools import t3code_log_filter as log_filter


class T3CodeLogFilterTests(unittest.TestCase):
    def test_redacts_plain_and_structured_credentials_and_qr_rows(self) -> None:
        source = (
            "T3 Code server is ready.\n"
            "Token: fixture-token-value\n"
            "Pairing URL: https://example.test/pair#token=fixture-token-value\n"
            "  █▀▄ ▀█\n"
            '{"pairingUrl":"https://example.test/pair#token=fixture",'
            '"authorization":"Bearer fixture-token-value",'
            '"startupCredential":"fixture-credential",'
            '"accessToken":"fixture-access-token", "ready":true}\n'
            "unlabeled auth detail: Bearer fixture-bearer-token\n"
            "Server health check passed\n"
        )

        output = log_filter.redact_line(source.splitlines(keepends=True)[0])
        self.assertEqual(output, "T3 Code server is ready.\n")
        safe = "".join(
            log_filter.redact_line(line)
            for line in source.splitlines(keepends=True)
        )

        self.assertNotIn("fixture-token-value", safe)
        self.assertNotIn("https://example.test/pair", safe)
        self.assertNotIn("█", safe)
        self.assertIn('"authorization":"[redacted]"', safe)
        self.assertNotIn("fixture-credential", safe)
        self.assertNotIn("fixture-access-token", safe)
        self.assertNotIn("fixture-bearer-token", safe)
        self.assertIn('"ready":true', safe)
        self.assertIn("Server health check passed", safe)
        structured = safe.splitlines()[3]
        self.assertTrue(json.loads(structured)["ready"])
        self.assertEqual(log_filter.redact_line("Token: [redacted]\n"), "Token: [redacted]\n")

    def test_stream_filter_preserves_non_sensitive_diagnostics(self) -> None:
        output = io.BytesIO()
        log_filter.filter_stream(
            io.BytesIO(
                b"native module load failed: node-pty\n"
                b"Token: fixture-token-value\n"
                b"service is listening\n"
            ),
            output,
        )

        safe = output.getvalue().decode()
        self.assertIn("native module load failed: node-pty", safe)
        self.assertIn("Token: [redacted]", safe)
        self.assertNotIn("fixture-token-value", safe)
        self.assertIn("service is listening", safe)

    def test_formatted_credentials_and_complete_header_values_are_redacted(self) -> None:
        lines = (
            "\x1b[36mToken\x1b[0m: fixture-secret\n",
            "Authorization=Basic fixture-secret\n",
            "Cookie=session=fixture-secret; refresh=fixture-refresh\n",
            "\x1b]8;;https://example.test/pair#fixture-secret\x1b\\"
            "Pairing URL\x1b]8;;\x1b\\: https://example.test/pair#fixture-secret\n",
            "\x1b]8;;https://example.test/pair#fixture-secret\x07"
            "open pairing\x1b]8;;\x07\n",
        )
        for line in lines:
            with self.subTest(line=line):
                safe = log_filter.redact_line(line)
                self.assertNotIn("fixture-", safe)
                self.assertNotIn("https://example.test/pair", safe)
                self.assertNotIn("\x1b", safe)

        self.assertEqual(
            log_filter.redact_line("\x1b[30;47m  █▀▄▀█  \x1b[0m\n"),
            "",
        )
        self.assertEqual(
            log_filter.redact_line("\x1b[32mServer healthy\x1b[0m\n"),
            "Server healthy\n",
        )

    def test_live_and_saved_logs_use_the_same_formatted_output_filter(self) -> None:
        source = (
            b"\x1b[36mToken\x1b[0m: fixture-secret\n"
            b"Authorization=Basic fixture-secret\n"
            b"Cookie=session=fixture-secret; refresh=fixture-refresh\n"
        )
        output = io.BytesIO()
        log_filter.filter_stream(io.BytesIO(source), output)
        self.assertNotIn(b"fixture-", output.getvalue())
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "boot-service.log"
            log_path.write_bytes(source)
            self.assertTrue(log_filter.sanitize_existing_log(log_path))
            self.assertEqual(log_path.read_bytes(), output.getvalue())

    def test_log_scrubbing_is_in_place_idempotent_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "boot-service.log"
            log_path.write_text(
                "Server started\n"
                "Token: fixture-token-value\n"
                "Pairing URL: https://example.test/pair#token=fixture\n"
                " █▀▄▀█ \n"
                "native module warning\n",
                encoding="utf-8",
            )
            log_path.chmod(0o640)
            original_inode = log_path.stat().st_ino

            self.assertTrue(log_filter.sanitize_existing_log(log_path))
            safe = log_path.read_text(encoding="utf-8")
            self.assertIn("Server started", safe)
            self.assertIn("native module warning", safe)
            self.assertIn("Token: [redacted]", safe)
            self.assertNotIn("fixture-token-value", safe)
            self.assertNotIn("https://example.test/pair", safe)
            self.assertNotIn("█", safe)
            self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o640)
            self.assertEqual(log_path.stat().st_ino, original_inode)
            self.assertFalse(log_filter.sanitize_existing_log(log_path))
            self.assertEqual(list(Path(temporary).glob(".boot-service.log-redact-*")), [])

    def test_managed_scrub_marker_avoids_scanning_new_filtered_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            logs_dir = Path(temporary) / ".t3/userdata/logs"
            logs_dir.mkdir(parents=True)
            log_path = logs_dir / "boot-service.log"
            log_path.write_text("Token: fixture-token-value\n", encoding="utf-8")
            rotated_log = logs_dir / "boot-service.log.1"
            rotated_log.write_text(
                "Pairing URL: https://example.test/pair#token=fixture\n",
                encoding="utf-8",
            )

            self.assertTrue(log_filter.sanitize_managed_log(temporary))
            marker = logs_dir / ".basaltwater-t3-log-filter"
            self.assertEqual(
                marker.read_text(encoding="ascii"),
                f"{log_filter._FILTER_REVISION}\n",
            )
            self.assertNotIn("https://example.test/pair", rotated_log.read_text())
            with patch.object(
                log_filter,
                "sanitize_existing_log",
                side_effect=AssertionError("already-scrubbed logs should not be rescanned"),
            ):
                self.assertFalse(log_filter.sanitize_managed_log(temporary))

    def test_log_scrubbing_does_not_follow_symlinks_or_touch_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.log"
            target.write_text("Token: fixture-token-value\n", encoding="utf-8")
            link = root / "boot-service.log"
            link.symlink_to(target)

            self.assertFalse(log_filter.sanitize_existing_log(link))
            self.assertEqual(target.read_text(encoding="utf-8"), "Token: fixture-token-value\n")

    def test_upstream_execstart_is_read_from_current_unit_on_each_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            first = home / "first launcher 100%.mjs"
            second = home / "second launcher"
            first.write_text(
                "#!/bin/sh\nprintf 'Token: fixture-token-value\\n'\n",
                encoding="utf-8",
            )
            second.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            for executable in (first, second):
                executable.chmod(0o755)
            unit_path = home / ".config/systemd/user/t3code.service"
            unit_path.parent.mkdir(parents=True)

            def write_unit(executable: Path) -> None:
                unit_path.write_text(
                    "[Unit]\nDescription=T3\n[Service]\n"
                    "ExecStart="
                    f"{json.dumps(str(executable).replace('%', '%%'))} "
                    "__service-launcher\n",
                    encoding="utf-8",
                )

            write_unit(first)
            self.assertEqual(
                log_filter.resolve_upstream_command(home),
                [str(first), "__service-launcher"],
            )
            first_command = log_filter.resolve_upstream_command(home)
            stdout = io.BytesIO()
            self.assertEqual(
                log_filter._run_filtered_command(
                    first_command,
                    stdout=stdout,
                    stderr=io.BytesIO(),
                ),
                0,
            )
            self.assertNotIn("fixture-token-value", stdout.getvalue().decode())
            self.assertIn("Token: [redacted]", stdout.getvalue().decode())
            write_unit(second)
            self.assertEqual(
                log_filter.resolve_upstream_command(home),
                [str(second), "__service-launcher"],
            )

    def test_unit_parser_rejects_multiple_upstream_commands(self) -> None:
        with self.assertRaisesRegex(log_filter.T3ServiceError, "single upstream"):
            log_filter.parse_systemd_exec_start(
                "[Service]\nExecStart=/usr/bin/t3 serve\n"
                "ExecStart=/usr/bin/t3 __service-launcher\n"
            )

    def test_unit_parser_decodes_literal_dollars_and_percent_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "launcher $literal 100%"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            unit = (
                "[Service]\nExecStart=" + cachyos_t3._unit_exec_quote(str(binary))
                + ' __service-launcher "$$data%%" "$${LITERAL}"\n'
            )
            self.assertEqual(
                log_filter.parse_systemd_exec_start(unit),
                [str(binary), "__service-launcher", "$data%", "${LITERAL}"],
            )

    def test_unavailable_upstream_executable_produces_a_safe_service_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = f'[Service]\nExecStart="{temporary}/missing" __service-launcher\n'
            with self.assertRaisesRegex(log_filter.T3ServiceError, "invalid or unavailable"):
                log_filter.parse_systemd_exec_start(unit)

    def test_supervisor_filters_both_streams_and_returns_child_status(self) -> None:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        command = [
            sys.executable,
            "-c",
            "import sys; "
            "print('Token: fixture-token-value'); "
            "print('\\x1b[36mToken\\x1b[0m: fixture-formatted-secret'); "
            "print('Authorization=Basic fixture-header-secret', file=sys.stderr); "
            "print('Pairing URL: https://example.test/pair#token=fixture'); "
            "print('healthy diagnostic'); "
            "print('{\\\"pairingUrl\\\":\\\"https://example.test/pair\\\"}', file=sys.stderr); "
            "sys.exit(7)",
        ]

        status = log_filter._run_filtered_command(
            command,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(status, 7)
        safe_stdout = stdout.getvalue().decode()
        safe_stderr = stderr.getvalue().decode()
        self.assertNotIn("fixture-token-value", safe_stdout)
        self.assertNotIn("fixture-formatted-secret", safe_stdout)
        self.assertNotIn("https://example.test/pair", safe_stdout)
        self.assertIn("Token: [redacted]", safe_stdout)
        self.assertIn("healthy diagnostic", safe_stdout)
        self.assertNotIn("https://example.test/pair", safe_stderr)
        self.assertNotIn("fixture-header-secret", safe_stderr)
        self.assertIn('"pairingUrl":"[redacted]"', safe_stderr)

    def test_debian_dropin_scrubs_old_log_and_wraps_upstream_execstart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = temporary
            changed = t3code_steps._configure_t3_service_drop_in(
                home,
                os.path.join(home, "repos"),
                "127.0.0.1",
                3773,
                "/usr/bin",
                os.path.join(home, ".local/share/basaltwater/t3-npm/bin"),
                os.getuid(),
                os.getgid(),
            )
            drop_in = Path(t3code_steps._t3_service_drop_in(home))
            content = drop_in.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn(
                f"# T3 log filter revision {log_filter._FILTER_REVISION}",
                content,
            )
            self.assertIn("ExecStartPre=/usr/bin/python3 -I ", content)
            self.assertIn("t3code_log_filter.py sanitize-log", content)
            self.assertIn("ExecStart=\n", content)
            self.assertIn("t3code_log_filter.py upstream", content)

    def test_cachyos_execstart_uses_the_same_filter_and_keeps_t3_arguments(self) -> None:
        line = cachyos_t3._filtered_exec_start(
            "/home/user/.local/share/basaltwater/cachyos-t3/releases/1.2.3/bin/t3",
            ("serve", "--host", "127.0.0.1", "--no-browser"),
        )

        self.assertTrue(line.startswith("ExecStart=/usr/bin/python3 -I "))
        # The CI container may not provide the deployment's /usr/bin/python3.
        with patch.object(log_filter, "validate_filesystem_path"):
            command = log_filter.parse_systemd_exec_start(f"[Service]\n{line}\n")
        self.assertIn("t3code_log_filter.py", command[2])
        self.assertEqual(command[3], "exec")
        self.assertIn("serve", command)
        self.assertIn("--host", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("--no-browser", command)


if __name__ == "__main__":
    unittest.main()
