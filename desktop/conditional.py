"""Controller-owned observe/act/verify and capture-only bounded event waits."""

from __future__ import annotations

import copy
import json
import subprocess
import time

from desktop import conditional_contract, learning_records as records, perception
from desktop.conditional_native import require_control
from desktop.coordinates import map_observation_points
from desktop.qa_artifacts import new_bundle, stamp
from desktop.record_bundles import _frame, _read
from desktop.receipts import input_request_failure
from lib.atomic_io import write_bytes_atomic

METADATA_BYTES = 256 * 1024
REPORT_BYTES = 256 * 1024


def _json(value):
    return (json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode()


def _field(snapshot, name):
    fields = [field for field in snapshot["packet"]["fields"] if field["name"] == name]
    if len(fields) != 1 or fields[0]["status"] != "observed" or fields[0]["value"] is None:
        raise ValueError("Unknown or ambiguous observed field: " + name)
    return fields[0]


def _matches(snapshot, predicates):
    # Resolve every required field, including when an earlier predicate is false.
    return all([_field(snapshot, row["field"])["value"] == row["equals"] for row in predicates])


class _Run:
    def __init__(self, plan, controller, output, now, cancelled):
        conditional_contract.validate(plan)
        self.plan, self.controller = copy.deepcopy(plan), controller
        self.now, self.cancelled = now, cancelled
        self.started = now()
        self.deadline = self.started + plan["limits"]["seconds"]
        self.folder = new_bundle(output)
        self.tokens, self.retained = set(), []
        self.identity, self.geometry, self.profile = None, None, None
        self.report = {"schema_version": 1, "kind": "conditional-receipt", "plan_id": plan["id"],
            "run_id": plan["run_id"], "match_id": plan["match_id"], "phase": plan["phase"],
            "started": stamp(), "captures": [], "steps": [], "stop": "unknown",
            "match_completion": "not-asserted", "game_outcome": "not-asserted", "partial": True}

    def check(self):
        if self.cancelled():
            raise RuntimeError("cancelled")
        if self.now() >= self.deadline:
            raise RuntimeError("deadline")

    def used(self):
        return sum(path.stat().st_size for path in self.folder.iterdir())

    def reserve(self, count):
        if self.used() + count + REPORT_BYTES > self.plan["limits"]["storage_bytes"]:
            raise ValueError("storage-limit")

    def write(self, name, value, maximum=METADATA_BYTES):
        data = _json(value)
        if len(data) > maximum:
            raise ValueError("Controller metadata exceeds its byte limit")
        self.reserve(len(data))
        path = self.folder / name
        if path.exists() or path.is_symlink():
            raise ValueError("Controller artifacts cannot be overwritten")
        write_bytes_atomic(str(path), data)

    def status(self):
        self.check()
        current = self.controller.status(self.deadline)
        self.check()
        require_control(current)
        identity = [current[key] for key in ("generation", "control_revision")]
        if self.identity is None:
            self.identity = identity
        if identity != self.identity:
            raise ValueError("control-changed")
        return current

    def observe(self):
        self.status()
        maximum = self.plan["limits"]["frame_bytes"]
        self.reserve(maximum + METADATA_BYTES)
        index = len(self.report["captures"])
        name = f"capture-{index}"
        entry = {"index": index, "requested": stamp(), "started_seconds": self.now() - self.started,
                 "retention": "pending", "frame": name + ".png", "metadata": name + ".json"}
        previous = self.report["captures"][-1] if index else None
        entry["gap_seconds"] = entry["started_seconds"] - previous["started_seconds"] if previous else None
        self.report["captures"].append(entry)
        try:
            snapshot = self.controller.observe(self.folder / entry["frame"], self.deadline, maximum)
            self.check()
            records.keys(snapshot, "packet capture recognition frame_id frame")
            packet, capture, result = (snapshot[key] for key in ("packet", "capture", "recognition"))
            if not isinstance(capture, dict) or not isinstance(result, dict):
                raise ValueError("Malformed capture/recognition result")
            records.validate({"schema_version": 1, "records": [packet]})
            if any(packet[key] != self.plan[key] for key in ("run_id", "match_id", "phase")):
                raise ValueError("Observation belongs to another run/match/phase")
            frames = [frame for frame in packet["frames"] if frame["id"] == snapshot["frame_id"]]
            if len(frames) != 1:
                raise ValueError("Frame identity mismatch")
            frame = frames[0]
            content = _read(self.folder / entry["frame"], maximum, private=True)
            if content != snapshot["frame"]:
                raise ValueError("Capture bytes changed")
            _frame(content, frame)
            if (result.get("kind") != "region-recognition" or result.get("drift") is not False
                    or result.get("detector") != perception.DETECTOR
                    or any(result.get(key) != packet[source] for key, source in (("packet_id", "id"), ("state_revision", "state_revision")))
                    or result.get("frame_id") != frame["id"] or result.get("source_sha256") != frame["sha256"]
                    or [{key: field[key] for key in ("name", "status", "value", "sources")} for field in result["fields"]] != packet["fields"]):
                raise ValueError("Unusable or drifting recognition")
            # Reject missing/duplicate profile fields before granting a target,
            # as well as when comparing the post-input effect below.
            perception.field_delta(result, result)
            geometry = [capture.get(key) for key in ("geometry", "image_origin", "image_geometry")]
            if geometry != [frame["geometry"], frame["origin"], [frame["width"], frame["height"]]]:
                raise ValueError("Capture/packet geometry mismatch")
            if self.geometry is None:
                self.geometry = geometry
            if geometry != self.geometry:
                raise ValueError("geometry-changed")
            profile = [result.get("settings"), packet["conditions"]]
            if self.profile is None:
                self.profile = copy.deepcopy(profile)
            if profile != self.profile:
                raise ValueError("profile-or-conditions-changed")
            if [capture.get(key) for key in ("generation", "control_revision")] != self.identity:
                raise ValueError("control-changed")
            if capture.get("artifact_sha256") != frame["sha256"] or capture.get("initial_frame_skipped") is not True:
                raise ValueError("Unconfirmed fresh frame")
            # Mapping is read-only; it checks the live token, transform and helper age.
            current = self.status()
            map_observation_points(capture, current, capture["image_geometry"], 0, 0)
            token = capture["observation_id"]
            if token in self.tokens:
                raise ValueError("observation-token-reused")
            self.tokens.add(token)
            if packet["state_revision"] is None:
                raise ValueError("Unknown state revision")
            self.write(entry["metadata"], {key: value for key, value in snapshot.items() if key != "frame"})
            entry.update(retention="retained", sha256=frame["sha256"], captured_at=packet["captured_at"],
                         completed_seconds=self.now() - self.started)
            self.retained.append(entry)
            return snapshot, entry
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            entry.update(retention="issue-retained", error=str(exc), completed_seconds=self.now() - self.started)
            raise

    def scene(self, snapshot):
        value = _field(snapshot, self.plan["scene_field"])["value"]
        if value in self.plan["terminal_scenes"]:
            return "terminal-scene"
        if value not in self.plan["allowed_scenes"]:
            raise ValueError("unsupported-scene")
        return None

    def finish(self, stop, error=None):
        self.report.update(stop=stop, finished=stamp(), elapsed_seconds=self.now() - self.started,
                           partial=error is not None, storage_bytes_before_receipt=self.used())
        if error is not None:
            self.report["error"] = str(error)
        path = self.folder / "receipt.json"
        try:
            data = _json(self.report)
            if len(data) > REPORT_BYTES or self.used() + len(data) > self.plan["limits"]["storage_bytes"]:
                raise ValueError("Final receipt exceeds storage budget")
            write_bytes_atomic(str(path), data)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report.update(partial=True, persistence_error=str(exc), error="Receipt persistence failed: " + str(exc))
            return {**self.report, "bundle": str(self.folder), "artifact": None}
        return {**self.report, "bundle": str(self.folder), "artifact": str(path)}


def execute(plan, controller, *, output=None, now=time.monotonic, cancelled=lambda: False):
    """Execute finite clicks; a controller implements status/observe/dispatch.

    The controller is a trusted capture/recognizer adapter, not serialized input.
    Never replay a receipt or resume an uncertain step automatically.
    """
    conditional_contract.validate(plan)
    if plan["kind"] != "conditional-plan":
        raise ValueError("Expected conditional-plan")
    run = _Run(plan, controller, output, now, cancelled)
    run.report["steps"] = [{"id": step["id"], "status": "not-attempted", "input_delivery": "not-sent",
                            "effect": "not-observed"} for step in plan["steps"]]
    try:
        run.write("plan.json", plan, 65536)
        for index, step in enumerate(run.plan["steps"]):
            active = run.report["steps"][index]
            active["status"] = "refused"
            before, entry = run.observe()
            active["before"] = entry["metadata"]
            branch = run.scene(before)
            if branch:
                return run.finish(branch)
            if not _matches(before, step["preconditions"]):
                raise ValueError("precondition-failed")
            if _matches(before, step["effect"]):
                raise ValueError("Expected effect already holds; cannot verify a transition")
            target = _field(before, step["target"]["field"])
            if target["value"] != step["target"]["equals"] or len(target["sources"]) != 1:
                raise ValueError("target-ambiguous")
            source = target["sources"][0]
            if source["frame_id"] != before["frame_id"]:
                raise ValueError("Target belongs to another frame")
            x, y, width, height = source["rectangle"]
            current = run.status()
            mapped = map_observation_points(before["capture"], current, before["capture"]["image_geometry"],
                                            x + width // 2, y + height // 2)
            payload = {"action": "input", **step["input"], **mapped["input_coordinates"],
                       **{key: mapped[key] for key in ("generation", "control_revision", "observation_id", "geometry")}}
            # Reserve post-input evidence and all stage receipts before mutation.
            run.reserve(run.plan["limits"]["frame_bytes"] + 3 * METADATA_BYTES)
            run.write(f"step-{index}-intent.json", {"step_id": step["id"], "payload": payload,
                "before": entry["metadata"], "input_delivery": "unknown", "effect": "unknown"})
            run.check()
            active.update(status="attempted", input_delivery="unknown", effect="unknown")
            try:
                receipt = controller.dispatch(payload, run.deadline)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                receipt = input_request_failure(exc, payload)
            if not isinstance(receipt, dict):
                receipt = input_request_failure(ValueError("Malformed helper reply"), payload)
            delivery = receipt.get("input_delivery")
            active["input_delivery"] = delivery if delivery in ("not-sent", "completed", "partial", "unknown") else "unknown"
            active["dispatch"] = receipt
            if active["input_delivery"] == "not-sent":
                active["status"] = "refused"
            run.write(f"step-{index}-dispatch.json", active)
            if "error" in receipt or active["input_delivery"] != "completed":
                raise ValueError("Input refused, partial or uncertain; do not replay")
            if any(receipt.get(key) != payload[key] for key in ("generation", "geometry", "observation_id")):
                raise ValueError("Completed receipt belongs to another observation; inspect before further input")
            after, after_entry = run.observe()
            active["after"] = after_entry["metadata"]
            branch = run.scene(after)
            active["delta"] = perception.field_delta(before["recognition"], after["recognition"])
            if not active["delta"]["comparable"] or not _matches(after, step["effect"]):
                active["effect"] = "failed"
                raise ValueError("effect-not-verified")
            active.update(status="completed", effect="verified")
            run.write(f"step-{index}-effect.json", active)
            if branch:
                return run.finish(branch)
        return run.finish("plan-completed")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        return run.finish("returned-control", exc)


def wait(plan, controller, *, output=None, now=time.monotonic, cancelled=lambda: False, sleep=time.sleep):
    """Capture-only wait with owned ring retention; timeout is no game outcome."""
    conditional_contract.validate(plan)
    if plan["kind"] != "event-wait":
        raise ValueError("Expected event-wait")
    run = _Run(plan, controller, output, now, cancelled)
    try:
        run.write("plan.json", plan, 65536)
        for index in range(plan["max_frames"]):
            run.check()
            observed, _ = run.observe()
            # Evict only after a successful replacement; failed captures preserve
            # the previous frame plus any issue media written by the adapter.
            while len(run.retained) > plan["retain_frames"]:
                old = run.retained.pop(0)
                old.update(retention="eviction-incomplete", evicted_artifacts=[])
                for key in ("frame", "metadata"):
                    (run.folder / old[key]).unlink()
                    old["evicted_artifacts"].append(old[key])
                old["retention"] = "evicted-ring"
            branch = run.scene(observed)
            if branch:
                return run.finish(branch)
            if _matches(observed, plan["until"]):
                return run.finish("predicate-satisfied")
            run.check()
            if index + 1 < plan["max_frames"]:
                sleep(min(plan["cadence_ms"] / 1000, max(0, run.deadline - now())))
        return run.finish("frame-limit")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        return run.finish("returned-control", exc)
