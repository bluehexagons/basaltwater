"""Offline regressions for browser console lifecycle and viewport changes."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


class ConsoleBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser script checks")
    def test_console_client_behavior(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", str(root / "tests/js/guacamole_console.cjs")],
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
