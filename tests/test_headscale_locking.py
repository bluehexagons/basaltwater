"""Portal and SSH administration share locks across temporary namespaces."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.privilege_broker_steps import BROKER, render_units
from lib import concurrency


class SharedRootLockTests(unittest.TestCase):
    def test_root_lock_ignores_tmpdir_and_excludes_another_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            root = str(Path(directory) / "root-locks")
            original_lstat, original_fstat = os.lstat, os.fstat

            def root_owned(info):
                return SimpleNamespace(st_uid=0, st_mode=info.st_mode)

            # Model root ownership without touching privileged host paths. The
            # directories, descriptors and contending flock calls remain real.
            with patch.object(concurrency, "ROOT_LOCK_DIRECTORY", root), \
                    patch.object(concurrency.os, "getuid", return_value=0), \
                    patch.object(concurrency.os, "lstat", side_effect=lambda p: root_owned(original_lstat(p))), \
                    patch.object(concurrency.os, "fstat", side_effect=lambda fd: root_owned(original_fstat(fd))):
                with patch.object(concurrency.tempfile, "gettempdir", return_value=directory + "/ssh") as ssh_tmp:
                    first = concurrency.resource_lock_path("unbound-binding", "/private/binding.json")
                    with concurrency.resource_lock("unbound-binding", "/private/binding.json"):
                        with patch.object(concurrency.tempfile, "gettempdir", return_value=directory + "/broker") as broker_tmp:
                            self.assertEqual(first, concurrency.resource_lock_path("unbound-binding", "/private/binding.json"))
                            with self.assertRaises(concurrency.ResourceBusyError):
                                with concurrency.resource_lock("unbound-binding", "/private/binding.json"):
                                    self.fail("Both root callers obtained the same resource")
                            broker_tmp.assert_not_called()
                    ssh_tmp.assert_not_called()
                with concurrency.resource_lock("unbound-binding", "/private/binding.json"):
                    pass

    def test_broker_exposes_shared_lock_directory_without_widening_headscale_access(self):
        unit = render_units(standalone=False)[BROKER]
        self.assertIn("StateDirectory=" + BROKER + " basaltwater-root-locks\n", unit)
        self.assertIn("StateDirectoryMode=0700\n", unit)
        for setting in ("PrivateTmp=true", "ProtectSystem=strict", "RestrictAddressFamilies=AF_UNIX"):
            self.assertIn(setting + "\n", unit)
        self.assertNotIn("ReadWritePaths=", unit)


if __name__ == "__main__":
    unittest.main()
