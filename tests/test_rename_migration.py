"""The retired migration command gives guidance without mutations."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from lib.rename_migration import migrate


class RetiredMigrationTests(unittest.TestCase):
    def test_every_action_refuses_without_system_or_filesystem_calls(self):
        for options in (
            {"system": False, "apply": False},
            {"system": False, "apply": True},
            {"system": True, "apply": True},
            {"system": True, "apply": False, "recovery": True},
            {"system": False, "apply": True, "installation": "/old/source"},
        ):
            with self.subTest(options=options), patch("builtins.open") as opened, \
                 patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(migrate(**options), 1)
            opened.assert_not_called()
            run.assert_not_called()
            self.assertIn("retired", output.getvalue())
            self.assertIn("97da179", output.getvalue())
            self.assertIn("docs/BASALTWATER_MIGRATION.md", output.getvalue())


if __name__ == "__main__":
    unittest.main()
