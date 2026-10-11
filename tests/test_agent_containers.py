"""Container probes are bounded, aggregate-only in support, and never delete."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import agent_containers as containers
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_probe import ProbeResult
from lib.agent_readiness import build_agent_readiness_record


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(containers.os.environ, {}, clear=True))
        self.enterContext(patch.object(containers.shutil, "which", return_value="/usr/bin/podman"))

    def probe(self, *, rootless=True, free=3*1024**3, rows=None):
        info = {"rootless": rootless, "remote": False, "graph_root": str(self.root), "containers": 2, "running": 1}
        rows = rows if rows is not None else [
            {"Type": "Images", "Total": 10, "Active": 2, "RawSize": 4*1024**3, "RawReclaimable": 5*1024**3},
            {"Type": "Containers", "Total": 2, "Active": 1, "RawSize": 2, "RawReclaimable": 1},
        ]
        with patch.object(containers, "run_probe", side_effect=[ProbeResult(json.dumps(info)), ProbeResult(json.dumps(rows))]) as probe, \
                patch.object(containers, "_filesystem", return_value={"available": True, "free_bytes": free,
                          "same_filesystem_as_root": False, "same_filesystem_as_home": True}):
            result = containers.inspect_container_engine("podman", home=str(self.root))
        self.assertTrue(all(call.kwargs == {} for call in probe.call_args_list))  # fixed bounded defaults
        self.assertEqual([call.args[0][2:4] for call in probe.call_args_list], [["info", "--format"], ["system", "df"]])
        self.assertTrue(all(call.args[0][1] == "--remote=false" for call in probe.call_args_list))
        return result

    def test_shared_layers_rootless_and_rootful_are_estimates(self):
        for rootless in (True, False):
            result = self.probe(rootless=rootless)
            self.assertEqual(result["rootless"], rootless)
            self.assertEqual(result["running_container_count"], 1)
            self.assertEqual(result["image_count"], 10)
            self.assertIsNone(result["build_cache_bytes"])  # Podman does not report a separate total.
            self.assertGreater(result["reclaimable_estimate_bytes"], result["image_bytes"])
            self.assertTrue(result["shared_layers_may_overestimate_reclaimable"])
            estimate = containers.container_preflight(result, image_bytes=2*1024**3)
            self.assertEqual(estimate["status"], "insufficient")
            self.assertFalse(estimate["reclaimable_counted_as_free"])

    def test_filesystem_uses_graph_root_on_separate_mount(self):
        usage = SimpleNamespace(total=32, used=25, free=7)
        original_stat = containers.os.stat
        def metadata(path, *args, **kwargs):
            if str(path) == "/":
                return SimpleNamespace(st_dev=-1)
            return original_stat(path, *args, **kwargs)
        with patch.object(containers.shutil, "disk_usage", return_value=usage) as disk, patch.object(containers.os, "stat", side_effect=metadata):
            result = containers._filesystem(str(self.root), str(self.root))
        disk.assert_called_once_with(str(self.root))
        self.assertFalse(result["same_filesystem_as_root"])
        self.assertTrue(result["same_filesystem_as_home"])
        self.assertEqual(result["free_bytes"], 7)

    def test_missing_failed_timeout_and_oversized_engines(self):
        with patch.object(containers.shutil, "which", return_value=None), patch.object(containers, "run_probe") as probe:
            result = containers.inspect_container_engine("podman")
        self.assertEqual(result["issues"], ["engine_missing"])
        probe.assert_not_called()
        for issue in ("probe_failed", "probe_timeout", "probe_output_limit", "probe_unavailable"):
            with self.subTest(issue=issue), patch.object(containers, "run_probe", return_value=ProbeResult(issue=issue)):
                result = containers.inspect_container_engine("podman")
            self.assertFalse(result["available"])
            self.assertEqual(result["issues"], [issue])
        with patch.object(containers, "run_probe", return_value=ProbeResult("not json")):
            self.assertEqual(containers.inspect_container_engine("podman")["issues"], ["invalid_engine_data"])

    def test_temporary_directory_failure_is_unavailable_without_raw_paths(self):
        with patch.object(containers.tempfile, "TemporaryDirectory", side_effect=OSError("/private/temp is full")), \
                patch.object(containers, "run_probe") as probe:
            result = containers.inspect_container_engine("podman")
        self.assertEqual(result["issues"], ["probe_unavailable"])
        self.assertNotIn("private", json.dumps(result))
        probe.assert_not_called()

    def test_remote_selection_never_invokes_engine(self):
        for engine, key in (("podman", "CONTAINER_HOST"), ("docker", "DOCKER_CONTEXT")):
            with patch.dict(containers.os.environ, {key: "private endpoint"}), patch.object(containers, "run_probe") as probe:
                result = containers.inspect_container_engine(engine)
            self.assertEqual(result["issues"], ["remote_selection_unsupported"])
            probe.assert_not_called()

    def test_disk_usage_failure_preserves_capacity_without_fabricating_totals(self):
        info = {"rootless": True, "graph_root": str(self.root)}
        for response in (ProbeResult(issue="probe_timeout"), ProbeResult("{}"),
                         ProbeResult('[{"Type":"Images","RawSize":-1}]')):
            with patch.object(containers, "run_probe", side_effect=[ProbeResult(json.dumps(info)), response]), \
                    patch.object(containers, "_filesystem", return_value={"available": True, "free_bytes": 4}):
                result = containers.inspect_container_engine("podman")
            self.assertEqual(result["filesystem"]["free_bytes"], 4)
            self.assertIsNone(result["image_bytes"])
            self.assertIsNone(result["reclaimable_estimate_bytes"])
            self.assertTrue(result["issues"])
        with patch.object(containers, "run_probe", side_effect=[ProbeResult(json.dumps(info)), ProbeResult("[]")]), \
                patch.object(containers, "_filesystem", side_effect=PermissionError):
            result = containers.inspect_container_engine("podman")
        self.assertIn("filesystem_unavailable", result["issues"])
        self.assertEqual(containers.container_preflight(result, image_bytes=1)["status"], "unknown")

    def test_docker_uses_local_socket_and_empty_config_with_build_cache(self):
        info = {"graph_root": str(self.root), "security_options": ["name=rootless"], "containers": 1, "running": 1}
        rows = '\n'.join(json.dumps(row) for row in [
            {"Type": "Images", "TotalCount": "3", "Active": "1", "Size": "1.5GB", "Reclaimable": "500MB (33%)"},
            {"Type": "Build Cache", "Size": "256MiB", "Reclaimable": "0B"},
        ])
        with patch.object(containers.os.path, "exists", return_value=True), \
                patch.object(containers.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)), \
                patch.object(containers, "_filesystem", return_value={"available": False}), \
                patch.object(containers, "run_probe", side_effect=[ProbeResult(json.dumps(info)), ProbeResult(rows)]) as probe:
            result = containers.inspect_container_engine("docker")
        self.assertTrue(result["rootless"])
        self.assertEqual(result["image_bytes"], 1_500_000_000)
        self.assertEqual(result["build_cache_bytes"], 256*1024**2)
        self.assertEqual(probe.call_args_list[0].args[0][1], "--config")
        self.assertIn("unix:///run/user/", " ".join(probe.call_args.args[0]))

    def test_shareable_bundle_and_readiness_discard_names_paths_labels(self):
        result = self.probe()
        result.update(registry="https://private-registry", labels={"owner": "private-owner"}, images=["private-image"])
        result["filesystem"]["mountpoint"] = "/private/home"
        result["issues"] = ["probe_timeout", "/private/error"]
        raw = {"requested": True, "engines": [result]}
        sanitized = containers.sanitize_container_storage(raw)
        readiness = build_agent_readiness_record([], [{"capability": "host", "healthy": True, "container_storage": raw}], trigger="manual")
        for output in (sanitized, readiness):
            text = json.dumps(output)
            self.assertNotIn("private", text)
            self.assertNotIn(str(self.root), text)
        self.assertEqual(sanitized["engines"][0]["issues"], ["probe_timeout"])

    def test_preflight_unknown_zero_invalid_and_cli_dispatch(self):
        self.assertEqual(containers.container_preflight({}, image_bytes=1)["status"], "unknown")
        record = {"filesystem": {"available": True, "free_bytes": 2}}
        self.assertEqual(containers.container_preflight(record, image_bytes=2, additional_copies=0, reserve_bytes=0)["status"], "sufficient_estimate")
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "containers", "preflight", "--engine", "podman", "--image-bytes", "-1", "--json"])
        with patch.object(containers, "inspect_container_engine") as probe, redirect_stdout(output := StringIO()):
            self.assertEqual(run_agent_command(args), 1)
        self.assertEqual(json.loads(output.getvalue())["issue"], "invalid_capacity_input")
        probe.assert_not_called()
        for fields in ({"image_bytes": True}, {"image_bytes": 1, "additional_copies": 9}, {"image_bytes": 1, "reserve_bytes": -1}):
            with self.assertRaises(ValueError):
                containers.container_preflight({}, **fields)
