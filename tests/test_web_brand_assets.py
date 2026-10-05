"""Static review assets must not depend on the machine generating them."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.service_tools import web_panel_agents as agents
from lib.agent_tasks import AgentTasks
from scripts.export_brand import export_assets


class BrandAssetTest(unittest.TestCase):
    def test_exports_are_identical_for_root_and_developer_accounts(self) -> None:
        outputs = []
        for home, uid, timestamp in (("/root", 0, 0), ("/home/fixture-builder", 1000, 2000000000)):
            with (
                self.subTest(home=home),
                tempfile.TemporaryDirectory() as directory,
                patch("lib.agent_tasks.os.path.expanduser", return_value=home),
                patch("lib.agent_tasks.os.geteuid", return_value=uid),
                patch.object(agents.time, "time", return_value=timestamp),
            ):
                destination = Path(directory)
                export_assets(destination)
                outputs.append({path.name: path.read_bytes() for path in destination.iterdir()})
        self.assertEqual(outputs[0].keys(), outputs[1].keys())
        self.assertEqual([name for name in outputs[0] if outputs[0][name] != outputs[1][name]], [])
        self.assertIn(b'value="/home/operator"', outputs[0]["agents.html"])
        self.assertNotIn(b'/home/fixture-builder', outputs[0]["agents.html"])
        self.assertNotIn(b'value="/root"', outputs[0]["agents.html"])

    def test_exports_do_not_read_task_state_or_collect_host_diagnostics(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("subprocess.run", side_effect=AssertionError("Host command during asset export")),
            patch.object(AgentTasks, "_load", side_effect=AssertionError("Private task storage read")),
            patch.object(agents.AgentDiagnostics, "snapshot", side_effect=AssertionError("Host diagnostic state read")),
            patch.object(agents, "inspect_agent_tools", side_effect=AssertionError("Agent credential inspection")),
        ):
            export_assets(Path(directory))


if __name__ == "__main__":
    unittest.main()
