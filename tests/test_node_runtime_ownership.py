"""Runtime provenance cannot grant cleanup permission to arbitrary NVM files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest

from lib.node_install import node_install_guard
from lib.node_runtime_ownership import MARKER, mark_runtime, runtime_directory, runtime_lock, runtime_owner


class RuntimeOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.nvm = Path(temporary.name)
        self.runtime = runtime_directory(self.nvm, "26.10.0")
        (self.runtime / "bin").mkdir(parents=True)
        (self.runtime / "bin/node").write_text("fixture")

    def test_project_claim_survives_automatic_claims(self):
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "v26.10.0"), "automatic")
        mark_runtime(self.nvm, "26.10.0", owner="project")
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "26.10.0"), "project")
        self.assertEqual((self.runtime / MARKER).stat().st_mode & 0o777, 0o600)

    def test_missing_malformed_or_unsafe_markers_never_authorize_cleanup(self):
        marker = self.runtime / MARKER
        for value in ("broken", "[]", '{"schema_version":2,"version":"v26.10.0","owner":"automatic"}'):
            marker.write_text(value)
            marker.chmod(0o600)
            self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
            with self.assertRaisesRegex(ValueError, "Unsafe or invalid"):
                mark_runtime(self.nvm, "26.10.0", owner="project")
        marker.unlink()
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        marker.chmod(0o666)
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        marker.unlink()
        personal = self.nvm / "personal.json"
        personal.write_text(json.dumps({"schema_version": 1, "version": "v26.10.0", "owner": "automatic"}))
        marker.symlink_to(personal)
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        with self.assertRaises(ValueError):
            mark_runtime(self.nvm, "26.10.0", owner="project")
        self.assertIn("automatic", personal.read_text())

    def test_runtime_paths_and_lock_reject_symlinks_and_non_versions(self):
        for version in ("26", "../../personal", "v26.10.0\n", "26.10.0-beta"):
            with self.assertRaises(ValueError):
                runtime_directory(self.nvm, version)
        directory = self.nvm / "versions/node/v22.23.2"
        directory.symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            mark_runtime(self.nvm, "22.23.2", owner="project")
        personal = self.nvm / "personal"
        personal.write_text("keep")
        lock = self.nvm / ".basaltwater-runtime.lock"
        lock.unlink()
        lock.symlink_to(personal)
        with self.assertRaises(OSError):
            mark_runtime(self.nvm, "26.10.0", owner="project")
        self.assertEqual(personal.read_text(), "keep")

    def test_lock_contention_fails_without_overwriting_project_protection(self):
        mark_runtime(self.nvm, "26.10.0", owner="project")
        with runtime_lock(self.nvm), self.assertRaisesRegex(ValueError, "operation is active"):
            mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "26.10.0"), "project")

    def test_install_preserves_incomplete_prefix_and_serializes_extraction(self):
        (self.runtime / "lib").mkdir()
        (self.runtime / "lib/global-tool").write_text("keep personal tools")
        with node_install_guard(self.nvm, "26.10.0") as existed:
            self.assertTrue(existed)
            self.assertFalse(self.runtime.exists())
            with self.assertRaisesRegex(ValueError, "operation is active"):
                with node_install_guard(self.nvm, "26.10.0"):
                    self.fail("concurrent installation entered")
        recovery, = self.nvm.glob(".basaltwater-incomplete-*")
        self.assertEqual(recovery.stat().st_mode & 0o777, 0o700)
        self.assertEqual((recovery / "v26.10.0/lib/global-tool").read_text(), "keep personal tools")
        with node_install_guard(self.nvm, "26.10.0") as existed:
            self.assertFalse(existed)

    def test_complete_runtime_is_reused_but_missing_npm_is_recovered(self):
        node = self.runtime / "bin/node"
        node.chmod(0o755)
        npm = self.runtime / "bin/npm"
        npm.symlink_to("../lib/npm-cli.js")
        (self.runtime / "lib").mkdir()
        script = self.runtime / "lib/npm-cli.js"
        script.write_text("fixture")
        script.chmod(0o755)
        with node_install_guard(self.nvm, "26.10.0") as existed:
            self.assertTrue(existed)
            self.assertEqual(node.read_text(), "fixture")
        self.assertEqual(list(self.nvm.glob(".basaltwater-incomplete-*")), [])
        script.unlink()
        with node_install_guard(self.nvm, "26.10.0"):
            self.assertFalse(self.runtime.exists())
        self.assertEqual(len(list(self.nvm.glob(".basaltwater-incomplete-*"))), 1)


class NodeInstallCancellationTests(unittest.TestCase):
    def test_setup_cancellation_stops_children_and_allows_binary_retry(self):
        root = Path(__file__).resolve().parent.parent
        installer = root / "lib/node_install.py"
        for extracted_node in (False, True):
            with self.subTest(extracted_node=extracted_node), tempfile.TemporaryDirectory() as temporary:
                nvm = Path(temporary)
                (nvm / "alias").mkdir()
                (nvm / "alias/default").write_text("v24.22.1")
                target = runtime_directory(nvm, "26.13.0")
                (target / "lib").mkdir(parents=True)
                (target / "lib/global-tool").write_text("retained before canceled retry")
                # A fake extraction blocks in a descendant after writing part
                # of the target. No network or real NVM installation is used.
                (nvm / "nvm.sh").write_text(
                    'nvm() {\n'
                    '  [ "$1" = install ] && [ "$2" = -b ] || return 91\n'
                    '  python3 - "$NVM_DIR" <<\'PY\'\n'
                    'import os, pathlib, signal, sys\n'
                    'target = pathlib.Path(sys.argv[1]) / "versions/node/v26.13.0"\n'
                    'target.joinpath("bin").mkdir(parents=True)\n'
                    'target.joinpath("lib").mkdir()\n'
                    'target.joinpath("lib/partial").write_text("incomplete archive")\n'
                    + ('target.joinpath("bin/node").write_text("#!/bin/sh\\necho v26.13.0\\n")\n'
                       'target.joinpath("bin/node").chmod(0o755)\n' if extracted_node else '')
                    + 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                    'print("READY " + str(os.getpid()), flush=True)\n'
                    'signal.pause()\nPY\n}\n'
                )
                supervisor = (
                    'import sys\nfrom lib.remote_utils import run\n'
                    'try:\n run(sys.argv[1:], timeout=30)\n'
                    'except KeyboardInterrupt:\n sys.exit(130)\n'
                )
                environment = {"PATH": os.defpath, "HOME": temporary, "PYTHONPATH": str(root)}
                process = subprocess.Popen(
                    [sys.executable, "-c", supervisor, sys.executable, "-I", str(installer), str(nvm), "v26.13.0"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    env=environment, cwd=root, start_new_session=True,
                )
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 15)[0], "extraction never started")
                    ready = process.stdout.readline().strip()
                    if not ready.startswith("READY "):
                        _, stderr = process.communicate(timeout=10)
                        self.fail(ready + stderr)
                    child = int(ready.split()[1])
                    process.send_signal(signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 130, stdout + stderr)
                    state = Path(f"/proc/{child}/stat")
                    if state.exists():
                        self.assertEqual(state.read_text().split()[2], "Z", "installer child is still running")
                    with runtime_lock(nvm):
                        self.assertEqual((target / "lib/partial").read_text(), "incomplete archive")
                    self.assertEqual((nvm / "alias/default").read_text(), "v24.22.1")
                    (nvm / "nvm.sh").write_text(
                        'nvm() {\n'
                        '  [ "$1" = install ] && [ "$2" = -b ] || return 91\n'
                        '  target="$NVM_DIR/versions/node/$3"\n'
                        '  [ ! -e "$target" ] || return 92\n'
                        '  mkdir -p "$target/bin" || return\n'
                        '  printf "#!/bin/sh\\nexit 0\\n" > "$target/bin/node"\n'
                        '  cp "$target/bin/node" "$target/bin/npm"\n'
                        '  chmod 755 "$target/bin/node" "$target/bin/npm"\n}\n'
                    )
                    result = subprocess.run(
                        [sys.executable, "-I", str(installer), str(nvm), "v26.13.0"],
                        env=environment, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(os.access(target / "bin/node", os.X_OK))
                    retained = list(nvm.glob(".basaltwater-incomplete-*/v26.13.0/lib/*"))
                    self.assertEqual(sorted(path.name for path in retained), ["global-tool", "partial"])
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                        process.communicate(timeout=10)
                    for stream in (process.stdout, process.stderr):
                        stream.close()


if __name__ == "__main__":
    unittest.main()
