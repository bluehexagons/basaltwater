"""CLI contracts for previewing and applying Podman image retention."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from lib.agent_storage_inventory import add_storage_parser, run_storage_command


class PodmanCleanupCommandTests(unittest.TestCase):
    def parse(self, *arguments):
        parser = argparse.ArgumentParser()
        add_storage_parser(parser.add_subparsers(required=True))
        return parser.parse_args(["storage", "clean-podman", *arguments])

    def test_default_and_explicit_preview_never_apply_cleanup(self):
        for arguments in ((), ("--dry-run",), ("--json",)):
            with self.subTest(arguments=arguments), \
                    patch("lib.podman_cleanup.clean_podman_images", return_value={"status": "preview"}) as clean, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_storage_command(self.parse(*arguments)), 0)
                clean.assert_called_once_with(apply=False)
                self.assertEqual(json.loads(output.getvalue())["status"], "preview")

    def test_apply_reports_cleanup_result(self):
        with patch("lib.podman_cleanup.clean_podman_images", return_value={"status": "cleaned"}) as clean, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_storage_command(self.parse("--apply", "--json")), 0)
            clean.assert_called_once_with(apply=True)
            self.assertEqual(json.loads(output.getvalue())["status"], "cleaned")

    def test_failure_and_deferral_exit_nonzero(self):
        for status in ("deferred_active", "deferred_unknown", "failed"):
            with self.subTest(status=status), \
                    patch("lib.podman_cleanup.clean_podman_images", return_value={"status": status}), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_storage_command(self.parse("--apply")), 1)
                self.assertEqual(json.loads(output.getvalue())["status"], status)

    def test_apply_error_does_not_claim_read_only_execution(self):
        with patch("lib.podman_cleanup.clean_podman_images", side_effect=OSError("probe failed")), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_storage_command(self.parse("--apply")), 1)
            self.assertFalse(json.loads(output.getvalue())["read_only"])

    def test_preview_and_apply_cannot_be_combined(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO), \
                self.assertRaises(SystemExit):
            self.parse("--dry-run", "--apply")


if __name__ == "__main__":
    unittest.main()
