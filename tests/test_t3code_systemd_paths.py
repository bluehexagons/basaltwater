"""Guard Debian T3 reconciliation against an unsupported user-unit directory."""

from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import t3code_steps as t3


class T3SystemdPathTests(unittest.TestCase):
    def test_manager_paths_override_the_setup_shell_and_stop_unsupported_setup(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            home = Path(folder)
            default = str(home / ".config/systemd/user")
            custom = str(home / "custom config/systemd/user")
            stack.enter_context(patch.object(t3.pwd, "getpwnam",
                                            return_value=SimpleNamespace(pw_dir=folder)))
            stack.enter_context(patch.dict(t3.os.environ, {"XDG_CONFIG_HOME": str(home / "shell-config")}))
            result = subprocess.CompletedProcess([], 0, "", "")
            run = stack.enter_context(patch.object(t3, "run", return_value=result))
            for directory in (default, custom):
                with self.subTest(directory=directory):
                    result.stdout = json.dumps({"type": "as", "data": [
                        "/usr/lib/systemd/user", directory + ".control", directory,
                        "/etc/xdg/systemd/user.control", "/etc/xdg/systemd/user",
                    ]})
                    if directory == default:
                        t3._ensure_user_manager("fixture", 1000)
                        self.assertEqual(run.call_args.args[0][-1], "UnitPath")
                        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", run.call_args.args[0])
                    else:
                        stack.enter_context(patch.object(t3, "_load_t3_reconciliation", return_value=None))
                        stack.enter_context(patch.object(t3, "install_package", return_value=True))
                        node = stack.enter_context(patch.object(t3, "_node_bin_directory"))
                        drop_in = stack.enter_context(patch.object(t3, "_configure_t3_service_drop_in"))
                        updater = stack.enter_context(patch.object(t3, "_run_as_login_user"))
                        with self.assertRaisesRegex(RuntimeError, "Relocated systemd"):
                            t3._install_locked_t3_service(folder, "fixture", 1000, 1000,
                                                         folder, "127.0.0.1", 3773,
                                                         str(home / "reconcile.json"))
                        node.assert_not_called()
                        drop_in.assert_not_called()
                        updater.assert_not_called()

    def test_failed_or_unrecognized_discovery_does_not_guess_a_directory(self):
        for status, output in ((1, ""), (0, "{"), (0, "[]"),
                               (0, '{"data": [null]}'), (0, '{"data": []}'),
                               (0, '{"data": ["/other/systemd/user.control"]}')):
            with self.subTest(status=status, output=output), \
                    patch.object(t3, "run", return_value=subprocess.CompletedProcess([], status, output, "")), \
                    self.assertRaisesRegex(RuntimeError, "Could not verify"):
                t3._validate_t3_systemd_directory("fixture", 1000)
