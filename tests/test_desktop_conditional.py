"""Mocked fail-stop coverage; never capture or send input to the host."""

from __future__ import annotations

import argparse
import copy
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import conditional, conditional_contract, conditional_native, evidence, perception, png_regions
from desktop.receipts import DesktopRequestError
from lib.desktop_cli import add_desktop_subparser, run_desktop_command

EXAMPLES = Path(__file__).resolve().parents[1] / "docs/examples/learning-records"


class Controller:
    def __init__(self, scenes):
        assets = json.loads((EXAMPLES / "perception.json").read_text())
        self.profile = assets["profile"]
        self.templates = {key: bytes.fromhex(value) for key, value in assets["templates_hex"].items()}
        self.scenes, self.inputs, self.count = scenes, [], 0
        self.current = {"state": "running", "agent_control": "enabled", "generation": "generation-1",
                        "control_revision": "control-1", "input_capabilities": ["observation-input"]}
        self.change = lambda snapshot, index: None
        self.delivery = "completed"
        self.failure = None

    def status(self, deadline):
        return copy.deepcopy(self.current)

    def observe(self, path, deadline, max_bytes):
        index = self.count
        self.count += 1
        scene = self.scenes[index]
        if isinstance(scene, BaseException):
            raise scene
        content = self.templates.get(scene, png_regions.encode(2, 2, b"\0\xff\0" * 4))
        path.write_bytes(content)
        path.chmod(0o600)
        packet = json.loads((EXAMPLES / "accepted.json").read_text())["records"][0]
        packet.update(id=f"packet-{index}", state_revision=f"state-{scene}")
        packet["conditions"].update(self.profile["conditions"])
        frame = {"id": f"frame-{index}", "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                 "width": 2, "height": 2, "origin": [0, 0], "geometry": [2, 2]}
        packet["frames"] = [frame]
        result, _ = perception.recognize(packet, frame["id"], content, self.profile, self.templates)
        packet["fields"] = [{key: field[key] for key in ("name", "status", "value", "sources")} for field in result["fields"]]
        capture = {"generation": self.current["generation"], "control_revision": self.current["control_revision"],
                   "observation_id": f"{index + 1:032x}", "geometry": [2, 2], "image_origin": [0, 0], "image_geometry": [2, 2],
                   "capture_freshness": {"fresh": True}, "artifact_sha256": frame["sha256"], "initial_frame_skipped": True}
        snapshot = {"packet": packet, "recognition": result, "capture": capture, "frame": content, "frame_id": frame["id"]}
        self.change(snapshot, index)
        self.current.update(snapshot["capture"])
        return snapshot

    def dispatch(self, payload, deadline):
        self.inputs.append(copy.deepcopy(payload))
        if self.failure:
            raise self.failure
        return {"input_id": "helper-owned-id", "input_delivery": self.delivery,
                **{key: payload[key] for key in ("generation", "geometry", "observation_id")}}


class ConditionalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.plan = json.loads((EXAMPLES / "conditional.json").read_text())
        for target in ("desktop.session_runtime.status", "desktop.session_runtime.request",
                       "desktop.native_session.status", "desktop.native_session.request", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("Live boundary reached"))
            guard.start()
            self.addCleanup(guard.stop)

    def execute(self, controller, **options):
        return conditional.execute(self.plan, controller, output=str(self.root / "run"), **options)

    def two_steps(self):
        self.plan["limits"]["inputs"] = 2
        self.plan["steps"].append(copy.deepcopy(self.plan["steps"][0]))
        self.plan["steps"][1]["id"] = "step-2"

    def test_effect_verified_and_fresh_target_mapping_per_step(self):
        self.two_steps()
        controller = Controller(["red", "blue", "red", "blue"])
        def moved(snapshot, index):
            if index == 2:
                snapshot["packet"]["fields"][0]["sources"][0]["rectangle"] = [0, 0, 1, 1]
        controller.change = moved
        result = self.execute(controller)
        self.assertEqual(result["stop"], "plan-completed")
        self.assertEqual([item["status"] for item in result["steps"]], ["completed", "completed"])
        self.assertEqual([(item["x"], item["y"]) for item in controller.inputs], [(1, 1), (0, 0)])
        self.assertEqual([item["observation_id"] for item in controller.inputs], [f"{i:032x}" for i in (1, 3)])
        self.assertEqual(result["match_completion"], "not-asserted")
        self.assertEqual(result["steps"][0]["delta"]["changes"][0]["after"], "blue")
        receipt = Path(result["artifact"])
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertTrue((receipt.parent / "step-0-intent.json").exists())
        self.assertEqual(json.loads(receipt.read_text())["steps"], result["steps"])

    def test_no_later_input_on_unknowns_scene_or_predicate_failure(self):
        self.two_steps()
        for mode in ("unknown", "unsupported", "predicate", "already-true", "inferred", "drift"):
            controller = Controller(["green" if mode == "unknown" else "blue" if mode == "already-true" else "red"])
            plan = copy.deepcopy(self.plan)
            if mode == "unsupported":
                plan["allowed_scenes"] = ["blue"]
            if mode == "predicate":
                plan["steps"][0]["preconditions"][0]["equals"] = "blue"
            if mode == "already-true":
                plan["steps"][0]["preconditions"][0]["equals"] = "blue"
            if mode == "inferred":
                controller.change = lambda value, _: value["packet"]["fields"][0].update(status="inferred")
            if mode == "drift":
                controller.change = lambda value, _: value["recognition"].update(drift=True)
            with self.subTest(mode=mode):
                result = conditional.execute(plan, controller, output=str(self.root / mode))
                self.assertIn("error", result)
                self.assertEqual(controller.inputs, [])
                self.assertEqual([step["status"] for step in result["steps"]], ["refused", "not-attempted"])

    def test_control_geometry_expired_consumed_and_reused_tokens_fail_stop(self):
        self.two_steps()
        for mode in ("control", "geometry", "expired", "consumed", "reuse", "profile"):
            controller = Controller(["red", "blue", "red"])
            def changed(snapshot, index):
                if index != 2:
                    return
                capture = snapshot["capture"]
                if mode == "control":
                    capture["control_revision"] = "changed"
                elif mode == "geometry":
                    capture["geometry"] = [3, 3]
                elif mode == "expired":
                    capture["capture_freshness"] = {"fresh": False}
                elif mode == "consumed":
                    capture["observation_id"] = None
                elif mode == "reuse":
                    capture["observation_id"] = f"{1:032x}"
                else:
                    snapshot["recognition"]["settings"] = {"changed": True}
            controller.change = changed
            with self.subTest(mode=mode):
                result = conditional.execute(self.plan, controller, output=str(self.root / mode))
                self.assertIn("error", result)
                self.assertEqual(len(controller.inputs), 1)
                self.assertEqual(result["steps"][0]["status"], "completed")
                self.assertEqual(result["steps"][1]["input_delivery"], "not-sent")

    def test_unknown_transport_partial_refusal_and_post_capture_failure(self):
        self.two_steps()
        for mode in ("disconnect", "partial", "not-sent", "completed-error", "post-capture", "pixel-stable"):
            controller = Controller(["red", OSError("capture lost") if mode == "post-capture" else "red"])
            if mode == "disconnect":
                controller.failure = OSError("transport disconnected")
            elif mode in ("partial", "not-sent"):
                controller.delivery = mode
            elif mode == "completed-error":
                controller.failure = DesktopRequestError({"error": "capture/transport failed after completion", "input_delivery": "completed"})
            with self.subTest(mode=mode):
                result = conditional.execute(self.plan, controller, output=str(self.root / mode))
                self.assertIn("error", result)
                self.assertEqual(len(controller.inputs), 1)
                self.assertEqual(result["steps"][1]["status"], "not-attempted")
                expected = "unknown" if mode == "disconnect" else mode if mode in ("partial", "not-sent") else "completed"
                self.assertEqual(result["steps"][0]["input_delivery"], expected)
                self.assertNotEqual(result["steps"][0]["effect"], "verified")
                self.assertTrue((Path(result["bundle"]) / "step-0-dispatch.json").exists())

    def test_missing_post_capture_field_cannot_verify_effect_or_dispatch_again(self):
        self.two_steps()
        controller = Controller(["red", "blue"])
        extra = copy.deepcopy(controller.profile["fields"][0])
        extra["name"] = "anchor"
        for template in extra["templates"]:
            controller.templates["anchor-" + template["id"]] = controller.templates[template["id"]]
            template["id"] = "anchor-" + template["id"]
        controller.profile["fields"].append(extra)
        def missing(snapshot, index):
            if index == 1:
                snapshot["packet"]["fields"].pop()
                snapshot["recognition"]["fields"].pop()
        controller.change = missing
        result = self.execute(controller)
        self.assertEqual(len(controller.inputs), 1)
        self.assertEqual(result["steps"][0]["input_delivery"], "completed")
        self.assertEqual(result["steps"][0]["effect"], "unknown")
        self.assertEqual(result["steps"][1]["status"], "not-attempted")
        self.assertIn("field set", result["error"])
        controller.count = 0
        controller.inputs.clear()
        def missing_before(snapshot, index):
            snapshot["packet"]["fields"].pop()
            snapshot["recognition"]["fields"].pop()
        controller.change = missing_before
        result = conditional.execute(self.plan, controller, output=str(self.root / "before"))
        self.assertEqual(controller.inputs, [])
        self.assertEqual(result["steps"][0]["status"], "refused")

    def test_deadline_cancellation_storage_and_durable_intent_failure(self):
        for mode in ("deadline", "cancel", "storage", "intent", "post-persistence"):
            controller = Controller(["red", "blue"])
            options = {}
            if mode == "deadline":
                times = iter([0, 21])
                options["now"] = lambda: next(times, 21)
            if mode == "cancel":
                options["cancelled"] = lambda: True
            plan = copy.deepcopy(self.plan)
            if mode == "storage":
                plan["limits"].update(frame_bytes=32768, storage_bytes=65536)
            original = conditional.write_bytes_atomic
            def fail(path, content):
                if (mode == "intent" and path.endswith("-intent.json")) or (mode == "post-persistence" and path.endswith("-dispatch.json")):
                    raise OSError("disk full")
                original(path, content)
            with self.subTest(mode=mode), patch.object(conditional, "write_bytes_atomic", side_effect=fail):
                result = conditional.execute(plan, controller, output=str(self.root / mode), **options)
                self.assertIn("error", result)
                self.assertEqual(len(controller.inputs), 1 if mode == "post-persistence" else 0)
                self.assertEqual(result["match_completion"], "not-asserted")
                if mode == "post-persistence":
                    intent = json.loads((Path(result["bundle"]) / "step-0-intent.json").read_text())
                    self.assertEqual(intent["input_delivery"], "unknown")
                    self.assertEqual(result["steps"][0]["input_delivery"], "completed")

    def test_dispatch_boundary_deadline_cancel_and_malformed_reply_preserve_uncertainty(self):
        self.two_steps()
        for mode in ("deadline", "cancel", "malformed", "wrong-token", "interrupt"):
            controller = Controller(["red", "blue"])
            current = [0.0]
            original = controller.dispatch
            def dispatch(payload, deadline):
                result = original(payload, deadline)
                current[0] = 21
                if mode == "interrupt":
                    raise KeyboardInterrupt()
                if mode == "malformed":
                    return None
                if mode == "wrong-token":
                    result["observation_id"] = "f" * 32
                return result
            controller.dispatch = dispatch
            options = {"now": lambda: current[0]} if mode == "deadline" else {}
            if mode == "cancel":
                options["cancelled"] = lambda: current[0] > 0
            result = conditional.execute(self.plan, controller, output=str(self.root / mode), **options)
            with self.subTest(mode=mode):
                self.assertEqual(len(controller.inputs), 1)
                self.assertEqual(controller.count, 1)
                self.assertEqual(result["steps"][1]["status"], "not-attempted")
                self.assertEqual(result["steps"][0]["effect"], "unknown")
                self.assertEqual(result["steps"][0]["input_delivery"], "unknown" if mode in ("malformed", "interrupt") else "completed")
                self.assertIn("error", result)

    def test_terminal_branch_no_automatic_new_match_and_output_refuses_reuse(self):
        self.two_steps()
        self.plan.update(allowed_scenes=["red"], terminal_scenes=["blue"])
        controller = Controller(["red", "blue"])
        result = self.execute(controller)
        self.assertEqual(result["stop"], "terminal-scene")
        self.assertEqual(result["steps"][0]["effect"], "verified")
        self.assertEqual(result["steps"][1]["status"], "not-attempted")
        self.assertEqual(len(controller.inputs), 1)
        with self.assertRaises(FileExistsError):
            self.execute(controller)

    def wait_plan(self):
        plan = copy.deepcopy(self.plan)
        plan.pop("steps")
        plan.update(kind="event-wait", until=[{"field": "scene", "equals": "blue"}],
                    cadence_ms=100, max_frames=5, retain_frames=1)
        plan["limits"]["inputs"] = 0
        return plan

    def test_wait_cadence_retention_semantic_predicate_no_input(self):
        controller = Controller(["red", "red", "blue"])
        current = [0.0]
        def sleep(seconds):
            current[0] += seconds
        result = conditional.wait(self.wait_plan(), controller, output=str(self.root / "wait"),
                                  now=lambda: current[0], sleep=sleep)
        self.assertEqual(result["stop"], "predicate-satisfied")
        self.assertEqual(controller.inputs, [])
        self.assertEqual([entry["retention"] for entry in result["captures"]], ["evicted-ring", "evicted-ring", "retained"])
        self.assertEqual([entry["gap_seconds"] for entry in result["captures"]], [None, .1, .1])
        self.assertFalse((Path(result["bundle"]) / "capture-0.png").exists())
        self.assertTrue((Path(result["bundle"]) / "capture-2.png").exists())

    def test_wait_limits_cancel_unexpected_terminal_and_failed_capture_keep_media(self):
        for mode in ("deadline", "cancel", "unknown", "terminal", "frame-limit", "lost"):
            plan = self.wait_plan()
            plan["max_frames"] = 2
            controller = Controller(["red", OSError("lost") if mode == "lost" else "green" if mode == "unknown" else "red"])
            current = [0.0]
            def sleep(seconds):
                current[0] += 301 if mode == "deadline" else seconds
            if mode == "terminal":
                plan.update(allowed_scenes=["blue"], terminal_scenes=["red"])
            result = conditional.wait(plan, controller, output=str(self.root / mode), now=lambda: current[0], sleep=sleep,
                                      cancelled=lambda: mode == "cancel" and current[0] > 0)
            with self.subTest(mode=mode):
                self.assertEqual(controller.inputs, [])
                self.assertEqual(result["game_outcome"], "not-asserted")
                self.assertEqual(result["stop"], "terminal-scene" if mode == "terminal" else "frame-limit" if mode == "frame-limit" else "returned-control")
                if mode == "lost":
                    self.assertTrue((Path(result["bundle"]) / "capture-0.png").exists())

    def test_wait_partial_eviction_records_actual_file_disposition(self):
        controller = Controller(["red", "blue"])
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.name == "capture-0.json":
                raise OSError("metadata removal failed")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", autospec=True, side_effect=unlink):
            result = conditional.wait(self.wait_plan(), controller, output=str(self.root / "wait"), sleep=lambda _: None)
        old = result["captures"][0]
        self.assertEqual(old["retention"], "eviction-incomplete")
        self.assertEqual(old["evicted_artifacts"], ["capture-0.png"])
        self.assertTrue((Path(result["bundle"]) / "capture-1.png").exists())
        self.assertTrue((Path(result["bundle"]) / "capture-0.json").exists())
        self.assertEqual(controller.inputs, [])
        self.assertIn("error", result)

    def recorded_wait(self, frames=3):
        folder = self.root / "recordings"
        folder.mkdir(mode=0o700)
        self.enterContext(patch.object(evidence, "_directory", return_value=folder))
        self.enterContext(patch.object(evidence, "revision", return_value={"commit": None, "dirty": None}))
        recorder = evidence.Recorder(str(self.root), "generation-1")
        controller = Controller(["red"] * (frames - 1) + ["blue"])
        observe = controller.observe
        def recorded(path, deadline, maximum):
            snapshot = observe(path, deadline, maximum)
            snapshot["capture"]["output"] = str(path)
            recorder.event({"action": "screenshot", "output": str(path)}, snapshot["capture"])
            return snapshot
        controller.observe = recorded
        plan = self.wait_plan()
        plan["max_frames"] = frames
        result = conditional.wait(plan, controller, output=str(self.root / "wait"), sleep=lambda _: None)
        recorder.finish({})
        self.assertEqual(result["stop"], "predicate-satisfied")
        self.assertEqual(controller.inputs, [])
        self.assertEqual(hashlib.sha256(Path(result["artifact"]).read_bytes()).hexdigest(), result["artifact_sha256"])
        return recorder, result, [[result["artifact"], result["artifact_sha256"]]]

    def repin(self, result, change):
        path = Path(result["artifact"])
        document = json.loads(path.read_text())
        change(document)
        content = (json.dumps(document) + "\n").encode()
        path.write_bytes(content)
        return [[str(path), hashlib.sha256(content).hexdigest()]]

    def test_pinned_wait_evictions_verify_and_closeout_without_rewriting_history(self):
        recorder, result, pins = self.recorded_wait()
        original = recorder.path.read_bytes()
        before = evidence.verify(str(recorder.path))
        self.assertFalse(before["healthy"])
        self.assertEqual(before["counts"]["missing"], 2)
        previous = evidence.closeout(str(recorder.path), extras=[], derivatives=[], output=str(self.root / "before"))
        after = evidence.verify(str(recorder.path), retention=pins)
        self.assertTrue(after["healthy"])
        self.assertEqual(after["counts"], {"verified": 1, "expected_evicted": 2, "missing": 0,
                                         "mismatched": 0, "unverified": 0, "image_less": 0})
        self.assertEqual(after["retention_receipts"][0]["expected_evictions"], 2)
        closeout = evidence.closeout(str(recorder.path), extras=[], derivatives=[], retention=pins,
                                    previous=previous["artifact"], output=str(self.root / "after"))
        self.assertEqual(closeout["verification"], after)
        self.assertFalse(json.loads(Path(previous["artifact"]).read_text())["verification"]["healthy"])
        self.assertEqual(recorder.path.read_bytes(), original)
        self.assertFalse(result["partial"])

    def test_retention_cli_verify_and_closeout_stay_offline(self):
        recorder, _, pins = self.recorded_wait()
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        for operation in ("verify", "closeout"):
            args = ["desktop", "--native", "evidence", operation, str(recorder.path), "--retention", *pins[0], "--json"]
            if operation == "closeout":
                args += ["--output", str(self.root / "cli-closeout")]
            stream = StringIO()
            with self.subTest(operation=operation), patch("sys.stdout", stream):
                self.assertEqual(run_desktop_command(parser.parse_args(args)), 0)
                report = json.loads(stream.getvalue())
                self.assertTrue((report if operation == "verify" else report["verification"])["healthy"])

    def test_retention_never_hides_unrelated_loss_uncertainty_or_truncation(self):
        recorder, _, pins = self.recorded_wait()
        recorder.event({"action": "input"}, None)
        report = evidence.verify(str(recorder.path), retention=pins)
        self.assertFalse(report["healthy"])
        self.assertEqual(report["uncertain_events"], 1)
        recorder.data["events"].pop()
        recorder.data["events_truncated"] = True
        recorder.save()
        self.assertFalse(evidence.verify(str(recorder.path), retention=pins)["healthy"])
        recorder.data["events_truncated"] = False
        recorder.event({"action": "screenshot"}, {"output": str(self.root / "lost.png"),
                       "artifact_sha256": "f" * 64})
        report = evidence.verify(str(recorder.path), retention=pins)
        self.assertFalse(report["healthy"])
        self.assertEqual(report["counts"]["expected_evicted"], 2)
        self.assertEqual(report["counts"]["missing"], 1)

    def test_retention_requires_exact_recorder_identity_and_hash(self):
        recorder, result, _ = self.recorded_wait()
        for field in ("generation", "control_revision", "observation_id", "sha256", "retention"):
            def change(document):
                for entry in document["captures"][:-1]:
                    if field in ("sha256", "retention"):
                        entry[field] = "f" * 64 if field == "sha256" else "eviction-incomplete"
                    else:
                        entry["capture_identity"][field] = "different"
            original = Path(result["artifact"]).read_bytes()
            pins = self.repin(result, change)
            with self.subTest(field=field):
                report = evidence.verify(str(recorder.path), retention=pins)
                self.assertFalse(report["healthy"])
                self.assertEqual(report["counts"]["missing"], 2)
            Path(result["artifact"]).write_bytes(original)

    def test_retention_rejects_invalid_or_incomplete_declarations(self):
        recorder, result, original_pins = self.recorded_wait()
        changes = {
            "legacy": lambda doc: doc["captures"][0].pop("capture_identity"),
            "schema": lambda doc: doc.update(schema_version=True),
            "plan-pin": lambda doc: doc.update(plan_sha256="f" * 64),
            "wrong-phase": lambda doc: doc.update(phase="different"),
            "steps": lambda doc: doc.update(steps=[{}]),
            "traversal": lambda doc: doc["captures"][0].update(frame="../capture-0.png"),
            "index": lambda doc: doc["captures"][0].update(index=1),
            "partial-unlink": lambda doc: doc["captures"][0].update(evicted_artifacts=["capture-0.png"]),
            "no-replacement": lambda doc: doc["captures"][-1].update(retention="issue-retained"),
            "too-many-retained": lambda doc: doc["captures"][0].update(retention="retained"),
        }
        original = Path(result["artifact"]).read_bytes()
        for name, change in changes.items():
            pins = self.repin(result, change)
            with self.subTest(change=name), self.assertRaises((OSError, ValueError)):
                evidence.verify(str(recorder.path), retention=pins)
            Path(result["artifact"]).write_bytes(original)
        for pins in ([[result["artifact"], "f" * 64]], original_pins * 2, original_pins * 17,
                     [[None, "f" * 64]], [[result["artifact"]]], "invalid"):
            with self.subTest(bindings=pins), self.assertRaises(ValueError):
                evidence.verify(str(recorder.path), retention=pins)

    def test_retention_requires_private_pinned_files_and_later_replacement(self):
        recorder, result, pins = self.recorded_wait()
        path = Path(result["artifact"])
        for mode in ("public", "symlink", "changed", "still-present", "replacement-lost", "replacement-changed"):
            with self.subTest(mode=mode):
                if mode == "public":
                    path.chmod(0o644)
                    with self.assertRaises(ValueError):
                        evidence.verify(str(recorder.path), retention=pins)
                    path.chmod(0o600)
                elif mode == "symlink":
                    target = path.with_name("saved.json")
                    path.rename(target)
                    path.symlink_to(target)
                    with self.assertRaises(ValueError):
                        evidence.verify(str(recorder.path), retention=pins)
                    path.unlink()
                    target.rename(path)
                elif mode == "changed":
                    content = path.read_bytes()
                    path.write_bytes(content + b"\n")
                    with self.assertRaises(ValueError):
                        evidence.verify(str(recorder.path), retention=pins)
                    path.write_bytes(content)
                else:
                    image = path.parent / ("capture-0.png" if mode == "still-present" else "capture-2.png")
                    content = image.read_bytes() if image.exists() else None
                    if mode == "replacement-lost":
                        image.unlink()
                    else:
                        image.write_bytes(b"changed")
                        image.chmod(0o600)
                    with self.assertRaises((OSError, ValueError)):
                        evidence.verify(str(recorder.path), retention=pins)
                    if content is None:
                        image.unlink()
                    else:
                        image.write_bytes(content)
                        image.chmod(0o600)

    def test_retention_supports_the_full_wait_capture_bound(self):
        recorder, _, pins = self.recorded_wait(frames=200)
        report = evidence.verify(str(recorder.path), retention=pins)
        self.assertTrue(report["healthy"])
        self.assertEqual(report["counts"]["expected_evicted"], 199)

    def test_schema_bounds_no_embedded_authority_and_cli_offline(self):
        for key, value in (("schema_version", True), ("generation", "injected"), ("steps", [])):
            plan = copy.deepcopy(self.plan)
            plan[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                conditional_contract.validate(plan)
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers(dest="command"))
        output = StringIO()
        with patch("sys.stdout", output):
            result = run_desktop_command(parser.parse_args(["desktop", "evidence", "conditional", "validate", str(EXAMPLES / "conditional.json"), "--json"]))
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output.getvalue())["input_authority"])

    def test_native_adapter_uses_existing_authority_and_fresh_frames(self):
        fixture = Controller(["red", "blue"])
        backend = Mock()
        backend.status.side_effect = lambda **_: fixture.status(100)
        def request(payload):
            if payload["action"] == "screenshot":
                snapshot = fixture.observe(Path(payload["output"]), 100, 131072)
                return {**snapshot["capture"], "captured_at": snapshot["packet"]["captured_at"],
                        "frame_received_monotonic_ns": 100, "output": payload["output"]}
            return fixture.dispatch(payload, 100)
        backend.request.side_effect = request
        adapter = conditional_native.NativeController(backend, fixture.profile, fixture.templates,
                    run_id="run-1", match_id="match-1", phase="preparation")
        result = self.execute(adapter)
        self.assertEqual(result["stop"], "plan-completed")
        self.assertEqual([call.args[0]["action"] for call in backend.request.call_args_list], ["screenshot", "input", "screenshot"])
        self.assertTrue(backend.request.call_args_list[0].args[0]["next_frame"])
        with self.assertRaises(ValueError):
            adapter.observe(self.root / "oversized.png", 100, 1)

    def test_published_compatibility_fixture_pins(self):
        receipt = json.loads((EXAMPLES / "learning-compatibility.json").read_text())
        root = EXAMPLES.parents[2]
        for filename, digest in receipt["fixtures"].items():
            with self.subTest(fixture=filename):
                self.assertEqual(hashlib.sha256((root / filename).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
