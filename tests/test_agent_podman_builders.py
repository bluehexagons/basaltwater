"""Offline safety contract for bounded Buildah builder metadata checks."""

from __future__ import annotations

import json
import subprocess
import unittest

from lib import podman_builders as builders


_STORE = {
    "graphRoot": "/private/graph",
    "runRoot": "/private/run",
    "graphDriverName": "overlay",
}


def _completed(argv: list[str], payload: object, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, json.dumps(payload), "private stderr")


def _podman_row(identifier: str, *, created: int, state: str = "storage") -> dict:
    return {"ID": identifier, "Created": created, "State": state}


def _inspect(identifier: str, *, mount: str = "", docker_labels=None, oci_labels=None) -> dict:
    return {
        "Type": "buildah 0.0.1",
        "ContainerID": identifier,
        "MountPoint": mount,
        "Docker": {"config": {"Labels": docker_labels or {}}},
        "OCIv1": {"config": {"Labels": oci_labels or {}}},
    }


def _buildah_info(*, graph: str | None = None) -> dict:
    return {
        "host": {"rootless": True},
        "store": {
            "GraphRoot": graph or _STORE["graphRoot"],
            "RunRoot": _STORE["runRoot"],
            "GraphDriverName": _STORE["graphDriverName"],
        },
    }


class PodmanBuilderPlanTests(unittest.TestCase):
    def setUp(self):
        self.old = "a" * 64
        self.young = "b" * 64
        self.ordinary = "c" * 64
        self.unknown = "d" * 64
        self.pinned = "e" * 64
        self.mounted = "f" * 64
        self.calls: list[list[str]] = []
        self.rows = [
            _podman_row(self.old, created=100),
            _podman_row(self.young, created=200),
            _podman_row(self.ordinary, created=1, state="running"),
            _podman_row(self.unknown, created=90),
            _podman_row(self.pinned, created=80),
            _podman_row(self.mounted, created=70),
        ]
        self.inventory = [
            {"id": self.old, "builder": True},
            {"id": self.young, "builder": True},
            {"id": self.pinned, "builder": True},
            {"id": self.mounted, "builder": True},
            {"id": "1" * 64, "builder": False},
        ]

        def podman(argv):
            self.calls.append(argv)
            return _completed(argv, self.rows)

        def buildah(argv):
            self.calls.append(argv)
            if argv[:2] == ["containers", "--json"]:
                return _completed(argv, self.inventory)
            if argv[:2] == ["info", "--format"]:
                return _completed(argv, _buildah_info())
            if argv[:2] == ["inspect", "--type"]:
                identifier = argv[-1]
                if identifier == self.pinned:
                    metadata = _inspect(identifier, oci_labels={"io.basaltwater.keep": ""})
                elif identifier == self.mounted:
                    metadata = _inspect(identifier, mount="/run/user/7/buildah/mount")
                else:
                    metadata = _inspect(identifier)
                return _completed(argv, metadata)
            raise AssertionError("unexpected Buildah command")

        self.podman_run = podman
        self.buildah_run = buildah

    def test_selects_only_old_inspected_unmounted_unpinned_builders(self):
        result = builders.plan_builders(self.podman_run, self.buildah_run, _STORE, 150)
        self.assertEqual(result, {
            "candidate_ids": [self.old],
            "retained_counts": {"young": 1, "ordinary": 1, "unknown": 1, "protected": 2},
        })
        self.assertEqual(self.calls[0], ["ps", "--all", "--external", "--format", "json"])
        self.assertEqual(self.calls[1], ["containers", "--json"])
        self.assertTrue(all("--root" not in argv for argv in self.calls))

    def test_wrong_store_fails_closed_without_inspecting_builders(self):
        original = self.buildah_run

        def mismatched(argv):
            if argv[:2] == ["info", "--format"]:
                self.calls.append(argv)
                return _completed(argv, _buildah_info(graph="/different/graph"))
            return original(argv)

        with self.assertRaises(builders.BuilderObservationError) as caught:
            builders.plan_builders(self.podman_run, mismatched, _STORE, 150)
        self.assertEqual(caught.exception.code, "buildah_store_mismatch")
        self.assertFalse(any(argv[:1] == ["inspect"] for argv in self.calls))

    def test_buildah_unavailable_retains_old_storage_and_does_not_block_other_cleanup(self):
        result = builders.plan_builders(self.podman_run, None, _STORE, 150)
        self.assertEqual(result["candidate_ids"], [])
        self.assertEqual(result["retained_counts"], {
            "young": 1, "ordinary": 1, "tool_unavailable": 4,
        })
        self.assertEqual(self.calls, [["ps", "--all", "--external", "--format", "json"]])

    def test_fresh_recheck_validates_id_and_observes_current_mount_and_pin(self):
        observed = []

        def run(argv):
            observed.append(argv)
            return _completed(argv, _inspect(self.old, mount="/new/mount"))

        self.assertFalse(builders.builder_is_removable(run, self.old))
        self.assertEqual(observed, [["inspect", "--type", "container", self.old]])
        for invalid in ("a" * 63, "A" * 64, "a" * 65, "a" * 63 + "g"):
            with self.subTest(identifier=invalid), self.assertRaises(builders.BuilderObservationError) as caught:
                builders.builder_is_removable(run, invalid)
            self.assertEqual(caught.exception.code, "invalid_identifier")
        self.assertEqual(len(observed), 1)

    def test_labels_with_any_value_protect_and_malformed_inspect_fails_closed(self):
        run = lambda argv: _completed(argv, _inspect(self.old, docker_labels={"io.basaltwater.keep": None}))
        self.assertFalse(builders.builder_is_removable(run, self.old))

        malformed = lambda argv: _completed(argv, {"Type": "buildah 0.0.1", "ContainerID": self.old})
        with self.assertRaises(builders.BuilderObservationError) as caught:
            builders.builder_is_removable(malformed, self.old)
        self.assertEqual(caught.exception.code, "invalid_builder_metadata")

    def test_inventory_rows_are_bounded_and_malformed_observations_are_redacted(self):
        for rows, code in ((["malformed"], "invalid_podman_inventory"),
                           ([{}] * 10_001, "invalid_podman_inventory")):
            self.rows = rows
            with self.subTest(code=code, size=len(rows)), self.assertRaises(builders.BuilderObservationError) as caught:
                builders.plan_builders(self.podman_run, self.buildah_run, _STORE, 150)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private", str(caught.exception))

    def test_builder_inspection_has_a_fixed_limit(self):
        self.rows = [_podman_row(f"{index:064x}", created=1) for index in range(40)]
        self.inventory = [{"id": row["ID"], "builder": True} for row in self.rows]
        result = builders.plan_builders(self.podman_run, self.buildah_run, _STORE, 150)
        inspections = [argv for argv in self.calls if argv[:1] == ["inspect"]]
        self.assertEqual(len(inspections), 32)
        self.assertEqual(len(result["candidate_ids"]), 32)
        self.assertEqual(result["retained_counts"]["inspection_limit"], 8)

    def test_bad_buildah_id_and_failed_callback_use_fixed_errors(self):
        self.inventory = [{"id": "short", "builder": True}]
        with self.assertRaises(builders.BuilderObservationError) as caught:
            builders.plan_builders(self.podman_run, self.buildah_run, _STORE, 150)
        self.assertEqual(caught.exception.code, "invalid_identifier")

        def failed(argv):
            raise RuntimeError("private argv and environ")

        with self.assertRaises(builders.BuilderObservationError) as caught:
            builders.plan_builders(failed, self.buildah_run, _STORE, 150)
        self.assertEqual(str(caught.exception), "podman_inventory_failed")


if __name__ == "__main__":
    unittest.main()
