"""Default workspace lookup never migrates retired client data."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.workspace import get_workspace_dir


class RetiredClientWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        environment = {**os.environ, "HOME": str(self.home)}
        environment.pop("BASALTWATER_WORKSPACE", None)
        self.enterContext(patch.dict(os.environ, environment, clear=True))

    def test_default_workspace_is_resolved_without_creating_it(self):
        workspace = self.home / ".config/basaltwater"
        self.assertEqual(get_workspace_dir(), str(workspace))
        self.assertFalse(workspace.exists())

    def test_old_workspace_is_preserved_and_requires_intermediate_version(self):
        for name in ("infra_tools", "infra-tools"):
            with self.subTest(name=name):
                old = self.home / ".config" / name
                old.mkdir(parents=True)
                secret = old / "credentials.json"
                secret.write_text("private")
                before = secret.read_bytes(), secret.stat().st_ino
                with self.assertRaisesRegex(ValueError, "Retired.*intermediate"):
                    get_workspace_dir()
                self.assertEqual((secret.read_bytes(), secret.stat().st_ino), before)
                self.assertFalse((old.parent / "basaltwater").exists())
                secret.unlink()
                old.rmdir()

    def test_explicit_workspace_does_not_consume_old_default_data(self):
        old = self.home / ".config/infra_tools"
        old.mkdir(parents=True)
        custom = self.home / "custom"
        os.environ["BASALTWATER_WORKSPACE"] = str(custom)
        self.assertEqual(get_workspace_dir(), str(custom))
        self.assertTrue(old.is_dir())


if __name__ == "__main__":
    unittest.main()
