"""Exercise the WSL bridge with the shared runner authorization policy."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from lib.github_runner_config import VERSION
from windows import runner_gate


class WindowsRunnerGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = dict(schema=1, repository="owner/project", organization="", runner_group="",
                           allowed_users=["owner"], labels=["windows-ci"], runner_version=VERSION,
                           registration="registered", enabled=True)
        self.context = dict(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REPOSITORY="owner/project",
                            GITHUB_SERVER_URL="https://github.com", GITHUB_RUN_ATTEMPT="1", GITHUB_ACTOR="owner")
        self.event = dict(repository=dict(full_name="owner/project", private=True), sender=dict(login="owner", type="User"))

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def run_gate(self, *, validate=False):
        args = ["--policy", self.write("policy.json", self.policy)]
        if validate:
            args.append("--validate")
        else:
            args.extend(["--event", self.write("event.json", self.event), "--context", self.write("context.json", self.context),
                         "--outcomes", str(self.root / "outcomes.json")])
        with contextlib.redirect_stdout(io.StringIO()):
            return runner_gate.main(args)

    def test_allowed_job_records_bounded_identity_only(self):
        self.event["secret"] = "not-in-telemetry"
        self.write("outcomes.json", [{}] * 100)
        self.assertEqual(self.run_gate(), 0)
        outcomes = (self.root / "outcomes.json").read_text()
        self.assertNotIn("not-in-telemetry", outcomes)
        self.assertEqual(len(json.loads(outcomes)), 100)
        self.assertEqual(json.loads(outcomes)[-1]["identity"], "owner")

    def test_denied_deliveries_and_policy_do_not_proceed(self):
        original_policy, original_context, original_event = (json.dumps(value) for value in (self.policy, self.context, self.event))
        for kind in ("actor", "public", "rerun", "disabled", "pending", "context"):
            with self.subTest(kind=kind):
                self.policy, self.context, self.event = (json.loads(value) for value in (original_policy, original_context, original_event))
                if kind == "actor": self.context["GITHUB_ACTOR"] = "other"
                if kind == "public": self.event["repository"]["private"] = False
                if kind == "rerun": self.context["GITHUB_RUN_ATTEMPT"] = "2"
                if kind == "disabled": self.policy["enabled"] = False
                if kind == "pending": self.policy["registration"] = "pending"
                if kind == "context": self.context["GITHUB_ACTOR"] = {"login": "owner"}
                self.assertEqual(self.run_gate(), 1)

    def test_pending_policy_can_be_validated_before_install(self):
        self.policy["registration"] = "pending"
        self.assertEqual(self.run_gate(validate=True), 0)
        self.policy["allowed_users"] = []
        self.assertEqual(self.run_gate(validate=True), 1)

    def test_unavailable_telemetry_fails_closed(self):
        self.write("outcomes.json", {"invalid": True})
        self.assertEqual(self.run_gate(), 1)
