from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import team_lease as lease, team_records as records
from lib.atomic_io import write_json_atomic


class DesktopTeamTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.parent = Path(scratch)
        self.root = self.parent / "team"
        self.native = self.parent / "native"
        self.debian = self.parent / "debian"
        for folder in (self.native, self.debian):
            folder.mkdir(mode=0o700)
        instruction = self.parent / "instructions.md"
        instruction.write_text("Play one turn.", encoding="utf-8")
        worker = {"role": "player", "task": "Use the assigned board", "acceptance": "Ready is visible"}
        spec = self.parent / "spec.json"
        write_json_atomic(str(spec), {
            "assignment": "fixture", "objective": "Finish a turn",
            "resources": {"couch": "cachyos-native", "lab": "debian-desktop"}, "scopes": ["shared", "p1", "p2"],
            "workers": {
                "coordinator": {"role": "coordinator", "task": "Integrate results", "acceptance": "Reconcile closeout"},
                "p1": {**worker, "resource": "couch", "watches": ["shared", "p1"], "instructions": [str(instruction)]},
                "p2": {**worker, "resource": "couch", "watches": ["shared", "p2"]},
                "lab": {**worker, "resource": "lab"},
                "observer": {**worker, "role": "review", "watches": ["shared", "p2"]},
            },
        })
        self.briefs = records.prepare(spec, str(self.root))["briefs"]
        self.enterContext(patch.object(lease, "resource_directory", lambda native: self.native if native else self.debian))
        self.backend = Mock()
        self.backend.status.return_value = {"generation": "fixture-native", "state": "running",
                                            "capabilities": [lease.CAPABILITY]}
        self.pad = self.enterContext(patch("desktop.gamepad_qa.call", return_value={"running": False}))

    def worker_result(self, worker, delivery="completed"):
        return records.result(self.root, worker, {
            "state": "ready", "delivery": delivery, "summary": "Input and visual effect checked",
            "next": "Inspect the next player's board",
        })["id"]

    def claim(self, worker="p1", **options):
        return lease.claim(self.root, options.get("resource", "couch"), worker,
                           native=options.get("native", True), backend=self.backend)

    def request(self, action, owner, *, channel="helper", callback=None, **kwargs):
        payload = {"action": action, **kwargs}
        if owner:
            with patch.dict(os.environ, {lease.LEASE_ENV: owner["lease_file"]}):
                payload = lease.wire_request(payload)
        call = callback or (lambda message: self.fail(f"Unexpected dispatch: {message['action']}"))
        return lease.dispatch(self.native, payload, self.backend.status.return_value["generation"], call,
                              channel=channel)

    def transfer(self, action, current, worker, result, *, credential=None, epoch=None):
        return lease.transition(action, native=True, backend=self.backend,
                                epoch=epoch or current["epoch"], worker=worker,
                                credential=credential or current["lease_file"], result=result,
                                note="Observed released controls and recorded the effect")

    def test_worker_brief_and_seat_scoped_pending_decisions(self):
        brief = records.object_file(self.briefs["p1"])
        self.assertEqual(brief["watches"], ["shared", "p1"])
        self.assertEqual(brief["instructions"][0]["total_characters"], 14)
        self.assertEqual(records.object_file(self.briefs["observer"])["resource"], None)
        image = self.parent / "pane.png"
        crop = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        image.write_bytes(crop)
        first = records.observe(self.root, "coordinator", "initial", {
            "updates": {"shared": "Round 11", "p1": "Host shop open", "p2": "Guest Ready"},
            "views": {"p2": [str(image)]},
        })
        image.write_bytes(b"rewritten source")
        guest = records.view(self.root, "p2")
        self.assertEqual(guest["summaries"], {"shared": "Round 11", "p2": "Guest Ready"})
        self.assertEqual(Path(guest["views"][0]["artifact"]).read_bytes(), crop)
        self.assertEqual(records.view(self.root, "p1")["views"], [])
        decision = records.decide(self.root, "p2", first["id"], {
            "proposal": "Keep Ready", "assumptions": ["No received card reopens shop"],
        })
        host = records.observe(self.root, "p1", first["id"], {"updates": {"p1": "Host bought a card"}})
        self.assertTrue(records.check(self.root, decision["id"])["current"])
        with self.assertRaisesRegex(ValueError, "head changed"):
            records.observe(self.root, "p2", first["id"], {"updates": {"p2": "Old sample"}})
        reopened = records.observe(self.root, "p1", host["id"], {
            "updates": {"p1": "Sent a card", "p2": "Received card and shop reopened"},
        })
        self.assertEqual(records.check(self.root, decision["id"])["stale_scopes"], ["p2"])
        with self.assertRaisesRegex(ValueError, "fresh observation"):
            records.decide(self.root, "p2", first["id"], {"proposal": "Keep Ready"})
        self.assertEqual(records.view(self.root, "p2")["id"], reopened["id"])
        result = self.worker_result("p2")
        self.assertEqual(records.results(self.root, "000000", "p2")["results"][0]["id"], result)
        self.assertEqual(records.results(self.root, result)["results"], [])

    def test_keyboard_gamepad_and_handoff_share_one_boundary(self):
        p1 = self.claim()
        with self.assertRaises(lease.LeaseRejected):
            self.request("screenshot", None)
        early = lease.dispatch_helper(self.native, {"action": "input"}, "fixture-native", Mock())
        self.assertEqual(early["input_delivery"], "not-sent")
        self.request("screenshot", p1, callback=lambda payload: {"observation_id": "from-p1"})
        result = self.worker_result("p1")

        def active_input(payload):
            self.assertNotIn("team_lease_token", payload)
            with self.assertRaises(records.TeamBusy):
                self.transfer("handoff", p1, "p2", result)
            with self.assertRaises(records.TeamBusy):
                self.request("press", p1, channel="pad", observation={"observation_id": "from-p1"})
            return {"input_delivery": "completed"}

        acknowledged = self.request("input", p1, observation_id="from-p1", callback=active_input)
        self.assertEqual(acknowledged["team_lease"]["owner"], "p1")
        with self.assertRaisesRegex(ValueError, "after the last input"):
            self.transfer("handoff", p1, "p2", result)
        p2 = self.transfer("handoff", p1, "p2", self.worker_result("p1"))
        self.assertEqual((p2["owner"], p2["epoch"]), ("p2", 2))
        with self.assertRaises(lease.LeaseRejected):
            self.request("screenshot", p1)
        with self.assertRaisesRegex(lease.LeaseRejected, "fresh frame"):
            self.request("press", p2, channel="pad", observation={"observation_id": "from-p1"})
        self.request("screenshot", p2, callback=lambda payload: {"observation_id": "from-p2"})
        pad = self.request("press", p2, channel="pad", observation={"observation_id": "from-p2"},
                           callback=lambda payload: {"delivery": "completed", "neutralized": True})
        self.assertEqual(pad["team_lease"]["epoch"], 2)
        with self.assertRaisesRegex(lease.LeaseRejected, "fresh frame"):
            self.request("input", p2, observation_id="from-p2")
        self.transfer("release", p2, None, self.worker_result("p2"))
        self.assertEqual(lease.status(native=True)["state"], "inactive")
        self.assertEqual(self.request("screenshot", None, callback=lambda payload: {"legacy": True}), {"legacy": True})

    def test_unknown_input_requires_new_coordinator_result_and_recovery(self):
        old_result = self.worker_result("coordinator")
        p1 = self.claim()
        self.request("screenshot", p1, callback=lambda payload: {"observation_id": "before"})
        self.request("press", p1, channel="pad", observation={"observation_id": "before"},
                     callback=lambda payload: {"delivery": "unknown", "neutralized": True})
        self.assertEqual(lease.status(native=True)["pending_input"]["epoch"], 1)
        with self.assertRaisesRegex(lease.LeaseRejected, "recovery"):
            self.transfer("handoff", p1, "p2", self.worker_result("p1"))
        with self.assertRaisesRegex(ValueError, "ownership epoch"):
            self.transfer("recover", p1, "coordinator", old_result, credential=p1["recovery_file"])
        with self.assertRaisesRegex(lease.LeaseRejected, "uncertain"):
            self.transfer("recover", p1, "coordinator", self.worker_result("coordinator", "unknown"),
                          credential=p1["recovery_file"])
        self.backend.status.return_value["generation"] = "replacement"
        with self.assertRaisesRegex(lease.LeaseRejected, "helper changed"):
            self.request("screenshot", p1)
        current = self.transfer("recover", p1, "coordinator", self.worker_result("coordinator"),
                                credential=p1["recovery_file"])
        self.assertEqual(current["generation"], "replacement")
        self.assertIsNone(current["pending_input"])
        with self.assertRaisesRegex(lease.LeaseRejected, "epoch changed"):
            self.transfer("handoff", current, "p2", self.worker_result("coordinator"), epoch=1)

    def test_capability_and_independent_resource(self):
        self.backend.status.return_value["capabilities"] = []
        with self.assertRaisesRegex(lease.LeaseRejected, "running desktop helper"):
            self.claim()
        self.backend.status.return_value["capabilities"] = [lease.CAPABILITY]
        self.pad.return_value = {"running": True}
        with self.assertRaisesRegex(lease.LeaseRejected, "running gamepad"):
            self.claim()
        self.pad.return_value = {"running": False}
        self.claim()
        with self.assertRaisesRegex(ValueError, "Worker or resource"):
            self.claim("observer")
        with self.assertRaisesRegex(lease.LeaseRejected, "already assigned"):
            self.claim("p2")
        with records.locked(self.native, "team-resource.lock", nonblocking=True):
            lab = self.claim("lab", resource="lab", native=False)
        self.assertEqual(lab["owner"], "lab")
        self.assertEqual(lease.status(native=True)["owner"], "p1")

    def test_parallel_workers_publish_distinct_results(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids = list(pool.map(self.worker_result, ("observer", "p1", "p2")))
        self.assertEqual(sorted(ids), ["000001", "000002", "000003"])
        outputs = records.results(self.root, "000001")
        self.assertEqual([row["id"] for row in outputs["results"]], ["000002", "000003"])
        self.assertEqual(outputs["cursor"], "000003")
