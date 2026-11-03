"""Run the disposable project adapter's owned-process acceptance checks."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the session adapter fixture")
class NodeSessionAdapterTests(unittest.TestCase):
    def test_synthetic_readiness_profiles_failures_and_cleanup(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", "examples/game-testing/node-session/adapter.test.mjs"],
            cwd=root, capture_output=True, text=True, timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
