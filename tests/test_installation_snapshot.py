"""Tests for target setup source provenance and snapshot channel behavior."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import infra_tools
from lib.channel_manager import (
    ChannelError,
    get_channel_info,
    managed_repository_path,
    switch_channel,
    upgrade_channel,
)
from lib.installation_info import (
    INSTALLATION_METADATA_FILENAME,
    installation_version,
    read_installation_metadata,
    write_setup_snapshot_metadata,
)


def _git(root: str, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", root, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class TestInstallationSnapshot(unittest.TestCase):
    def test_both_commands_read_existing_workspace_without_moving_state(self) -> None:
        from lib.orchestrator_bootstrap import install_launcher

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / ".config" / "infra_tools"
            (workspace / "setups").mkdir(parents=True)
            saved = workspace / "setups" / "server.json"
            saved.write_text(json.dumps({
                "host": "server", "system_type": "server_lite",
                "args": {"username": "agent"}, "command": "infra-tools setup server_lite server",
            }), encoding="utf-8")
            credentials = workspace / "credentials.json"
            credentials.write_text('{"fixture": "private"}', encoding="utf-8")
            credentials.chmod(0o600)
            before = {p: (p.read_bytes(), p.stat().st_mode) for p in (saved, credentials)}
            bin_dir = root / "bin"
            install_launcher(infra_tools.__file__, target_dir=str(bin_dir))
            environment = {**os.environ, "HOME": temp_dir, "INFRA_TOOLS_WORKSPACE": str(workspace)}
            for name in ("infra-tools", "basaltw"):
                for arguments in (["--help"], ["list", "--json"], ["cmd"]):
                    result = subprocess.run(
                        [str(bin_dir / name), *arguments], env=environment,
                        capture_output=True, text=True, check=True, timeout=10,
                    )
                    if arguments == ["cmd"]:
                        self.assertIn("basaltw setup server_lite server agent", result.stdout)
            after = {p: (p.read_bytes(), p.stat().st_mode) for p in (saved, credentials)}
            self.assertEqual(after, before)
            self.assertEqual(list((root / ".config").iterdir()), [workspace])

    def _source_repository(self, root: str) -> str:
        source = os.path.join(root, "source")
        os.mkdir(source)
        with open(
            os.path.join(source, "pyproject.toml"),
            "w",
            encoding="utf-8",
        ) as file_obj:
            file_obj.write('[project]\nname = "infra_tools"\nversion = "2.0.0"\n')
        with open(
            os.path.join(source, "infra_tools.py"),
            "w",
            encoding="utf-8",
        ) as file_obj:
            file_obj.write("# test source\n")
        _git(source, "init", "--initial-branch=main")
        _git(source, "config", "user.email", "tests@example.invalid")
        _git(source, "config", "user.name", "infra-tools tests")
        _git(source, "add", ".")
        _git(source, "commit", "-m", "source")
        return source

    def test_setup_snapshot_records_commit_version_branch_and_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source_repository(temp_dir)
            destination = os.path.join(temp_dir, "destination")
            os.mkdir(destination)

            path = write_setup_snapshot_metadata(source, destination)
            metadata = read_installation_metadata(destination)

            self.assertEqual(
                path,
                os.path.join(destination, INSTALLATION_METADATA_FILENAME),
            )
            self.assertIsNotNone(metadata)
            assert metadata is not None
            self.assertEqual(metadata["version"], "2.0.0")
            self.assertEqual(metadata["commit"], _git(source, "rev-parse", "HEAD"))
            self.assertEqual(metadata["branch"], "main")
            self.assertFalse(metadata["dirty"])
            self.assertEqual(installation_version(destination), "2.0.0")

            with open(
                os.path.join(source, "infra_tools.py"),
                "a",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write("# changed\n")
            write_setup_snapshot_metadata(source, destination)
            refreshed = read_installation_metadata(destination)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertTrue(refreshed["dirty"])

            second_destination = os.path.join(temp_dir, "second-destination")
            os.mkdir(second_destination)
            write_setup_snapshot_metadata(destination, second_destination)
            inherited = read_installation_metadata(second_destination)
            self.assertIsNotNone(inherited)
            assert inherited is not None
            self.assertEqual(inherited["commit"], metadata["commit"])
            self.assertEqual(inherited["branch"], "main")

    def test_snapshot_channel_status_reports_deployed_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = self._source_repository(temp_dir)
            destination = os.path.join(temp_dir, "destination")
            os.mkdir(destination)
            script = os.path.join(destination, "infra_tools.py")
            with open(script, "w", encoding="utf-8") as file_obj:
                file_obj.write("# deployed launcher\n")
            write_setup_snapshot_metadata(source, destination)

            managed = managed_repository_path(script)
            info = get_channel_info(managed)

            self.assertEqual(managed, destination)
            self.assertEqual(info["channel"], "setup-snapshot")
            self.assertEqual(info["version"], "2.0.0")
            self.assertEqual(info["commit"], _git(source, "rev-parse", "HEAD"))

            with self.assertRaisesRegex(ChannelError, "rerun setup"):
                switch_channel(destination, "dev")
            with self.assertRaisesRegex(ChannelError, "rerunning setup"):
                upgrade_channel(destination)

            with patch(
                "infra_tools._managed_repository",
                return_value=destination,
            ), patch("builtins.print") as mock_print:
                result = infra_tools.run_channel_command(
                    Namespace(channel_name=None),
                )

            self.assertEqual(result, 0)
            output = "\n".join(
                str(call.args[0]) for call in mock_print.call_args_list
            )
            self.assertIn("Channel: setup-snapshot", output)
            self.assertIn("Version: 2.0.0", output)

    def test_invalid_snapshot_metadata_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = os.path.join(temp_dir, "infra_tools.py")
            with open(script, "w", encoding="utf-8") as file_obj:
                file_obj.write("# deployed launcher\n")
            with open(
                os.path.join(temp_dir, INSTALLATION_METADATA_FILENAME),
                "w",
                encoding="utf-8",
            ) as file_obj:
                json.dump({"schema_version": 1, "installation_type": "unexpected"}, file_obj)

            with self.assertRaisesRegex(ChannelError, "neither"):
                managed_repository_path(script)

    def test_top_level_version_flag_is_stable_and_machine_readable(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
            parser.parse_args(["--version"])

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue(), "basaltw 2.0.0\n")


if __name__ == "__main__":
    unittest.main()
