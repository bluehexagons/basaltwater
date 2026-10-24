"""Development-only adapters for real panel handlers; never installed on targets."""

from __future__ import annotations

from contextlib import ExitStack
import copy
from pathlib import Path
import threading
import time
from unittest.mock import patch
import uuid

from common.service_tools import web_panel_admin as admin
from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_diagnostics as diagnostics
from common.service_tools import web_panel_github_runner as github_runner
from common.service_tools import web_panel_jobs as jobs
from common.service_tools import web_panel_maintenance as maintenance
from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_storage as storage
from common.service_tools.web_panel_features import PanelFeatures
from lib import agent_tasks
from lib import agent_task_followups
from lib.agent_inbox import AgentInbox
from lib.atomic_io import read_json_file, write_json_atomic
from lib.github_runner_config import VERSION as RUNNER_VERSION, validate_policy as validate_runner_policy
from lib.maintenance_agent_config import DEFAULTS, validate_policy
from lib.proxmox_panel import sanitize_cluster, sanitize_resources
from lib.validation import validate_git_author_email, validate_git_author_name, validate_github_token, validate_runner_registration_token
from scripts.panel_samples import job_rows
from scripts.panel_storage_samples import storage_snapshot

SCENARIOS = ("minimal", "full", "empty", "degraded", "proxmox")


class Scenario:
    """Replace host boundaries while keeping form parsing and local stores real."""

    def __init__(self, state: panel.WebPanelState, name: str, stack: ExitStack) -> None:
        if name not in SCENARIOS:
            raise ValueError("Unknown panel scenario")
        self.state, self.name, self.stack = state, name, stack
        self.home = Path(state.agent_tasks.home)
        self.path = self.home / "scenario.json"
        self.lock = threading.RLock()
        self.data = read_json_file(str(self.path)) if self.path.exists() else {
            "name": "Development Operator", "email": "operator@example.test", "github_present": False,
            "requests": [], "policy": {**DEFAULTS, "enabled": True}, "hold": False, "checked": False,
        }
        self.features = PanelFeatures(admin_page=name != "proxmox") if name in {"minimal", "proxmox"} else PanelFeatures(
            tools=frozenset({"codex", "git", "gh", "butler", "steamcmd"}), t3=True, maintenance=True, admin=True,
            github_runner=True,
        )
        self.install()

    def replace(self, target, attribute: str, **kwargs) -> None:
        self.stack.enter_context(patch.object(target, attribute, **kwargs))

    def save(self) -> None:
        write_json_atomic(str(self.path), self.data)

    def install(self) -> None:
        state = self.state
        self.replace(state, "features", return_value=self.features)
        self.replace(state, "system_overview", return_value=[
            {"label": label, "value": value, "description": "Synthetic development data"}
            for label, value in (("Uptime", "2d 6h"), ("Memory", "94% used" if self.name == "degraded" else "24% used"),
                                 ("Swap", "8% used"), ("Root disk", "96% used" if self.name == "degraded" else "52% used"),
                                 ("CPU load", "0.32"), ("Root inodes", "12% used"))
        ])
        self.replace(state, "audit_snapshot", return_value={
            "status": "unavailable" if self.name == "degraded" else "ok", "events": [],
            "issues": ["Synthetic audit collector unavailable"] if self.name == "degraded" else [],
        })
        self.replace(state, "notification_events", return_value=[])
        self.replace(state, "service_health", return_value=[] if self.name == "empty" else [
            {"label": "Synthetic web service", "value": "failed" if self.name == "degraded" else "active",
             "description": "Simulated service; no host query"},
        ])
        self.replace(state, "diagnostic_sources", return_value={"nginx.service": "Synthetic web service"})
        self.replace(panel, "discover_basaltwater_web_services", return_value=[])
        self.replace(panel, "discover_certificate_trust", return_value=None)
        if self.name == "proxmox":
            state.manifest.update(system_type="server_proxmox", title="Proxmox development node")
            guests, pools, _issues = sanitize_resources([
                {"node": "pve1", "type": kind, "vmid": vmid, "name": name, "status": status, "template": template,
                 "cpu": .14, "maxcpu": 4, "mem": 3 * 1024**3, "maxmem": 8 * 1024**3,
                 "maxdisk": 64 * 1024**3, "uptime": 86400}
                for kind, vmid, name, status, template in (
                    ("qemu", 100, "build-agent", "running", 0), ("lxc", 101, "web-services", "running", 0),
                    ("qemu", 102, "test-workstation", "stopped", 0), ("qemu", 9000, "debian-template", "stopped", 1))
            ] + [{"node": "pve1", "type": "storage", "storage": "local-lvm", "status": "available",
                  "plugintype": "lvmthin", "shared": 0, "disk": 85 * 1024**3, "maxdisk": 100 * 1024**3}], "pve1")
            self.replace(panel, "load_proxmox_snapshot", return_value={
                "available": True, "inventory": True, "generated_at": time.time(), "node": "pve1", "version": "9.1.0",
                "guests": guests, "storage": pools, "issues": [],
                "cluster": sanitize_cluster([{"type": "node", "name": "pve1", "local": 1, "online": 1}]),
            })
        self.replace(state, "t3_update_available", return_value=self.features.t3)
        self.replace(state, "trigger_t3_update", side_effect=self.update)
        self.replace(jobs, "collect_jobs", return_value=jobs.JobSnapshot(
            [] if self.name == "empty" else job_rows(),
            ["Synthetic timer collection incomplete"] if self.name == "degraded" else [], absent=9,
        ))
        self.replace(github_runner, "collect_runner_status", side_effect=self.runner_status)
        self.replace(panel, "submit_registration", side_effect=self.register_runner)
        self.replace(storage, "load_storage_snapshot", return_value={"available": False, "jobs": []})
        self.replace(diagnostics, "collect_diagnostics", side_effect=self.diagnostics)
        self.replace(agents, "_tool_path", side_effect=lambda tool, home: "/fixture/" + tool if tool in self.features.tools else None)
        self.replace(agent_tasks, "_tool_path", side_effect=lambda tool, home: "/fixture/" + tool if tool in self.features.tools else None)
        self.replace(agents, "codex_models", return_value=[])
        self.replace(agents, "_available_templates", return_value={
            key: value for key, value in agents.PROMPT_TEMPLATES.items() if "requires" not in value
        })
        self.replace(agents, "worker_status", return_value={"installed": False, "running": False})
        self.replace(state.agent_diagnostics, "snapshot", side_effect=self.agent_diagnostics)
        self.replace(state.agent_diagnostics, "trigger", side_effect=self.collect_agent_diagnostics)
        self.replace(state.storage_diagnostics, "snapshot", side_effect=lambda: storage_snapshot(
            loaded=bool(self.data.get("storage_loaded")), degraded=self.name == "degraded",
            mode=self.data.get("storage_mode", "quick")))
        self.replace(state.storage_diagnostics, "start", side_effect=self.collect_storage)
        self.replace(state.git_settings, "snapshot", side_effect=self.identity)
        self.replace(state.git_settings, "set_identity", side_effect=self.set_identity)
        self.replace(state.git_settings, "set_github_token", side_effect=self.set_token)
        self.replace(state.git_settings, "remove_github_token", side_effect=lambda: self.set_token(None))
        state.admin.approval_url = "https://127.0.0.1/fixture-approvals"
        self.replace(admin, "exchange", side_effect=self.exchange)
        self.replace(agent_task_followups, "exchange", side_effect=self.exchange)
        self.replace(agent_task_followups, "load_policy", return_value={"services": {"example.service": "approve"}})
        self.replace(maintenance, "load_policy", side_effect=lambda: copy.deepcopy(self.data["policy"]))
        self.replace(maintenance, "load_reports", side_effect=self.reports)
        self.replace(maintenance, "configure_policy", side_effect=self.policy)
        self.replace(maintenance, "request_check", side_effect=self.check)
        self.replace(maintenance, "inspect_agent_maintenance", side_effect=lambda: {
            "active": self.data["hold"], "status": "active" if self.data["hold"] else "inactive", "expires_at": "synthetic expiry",
        })
        self.replace(maintenance, "hold_agent_maintenance", side_effect=self.hold_for_hours)
        self.replace(maintenance, "release_agent_maintenance", side_effect=lambda: self.hold(False))
        # Publishing provider/file operations deliberately stop at this boundary.
        self.replace(state.publishing, "status", return_value={
            key: [] for key in ("projects", "tools", "accounts", "artifacts", "runs", "jobs", "drafts", "releases")
        })
        self.replace(state.publishing_auth, "views", return_value=[])
        self.replace(panel, "publishing_action", side_effect=ValueError("Publishing provider operations are unavailable in the development fixture"))
        # Exercise the real scheduler and task store with an entirely synthetic executor.
        self.replace(agent_tasks, "execute_prompt", side_effect=self.execute)
        self.replace(state.agent_tasks.contexts, "prepare", side_effect=lambda task, run: {
            "directory": str(self.home), "context_directory": str(self.home), "log_path": "",
        })
        state.agent_tasks._background_tick = None
        marker = self.home / "seeded"
        if not marker.exists():
            if self.name in {"full", "degraded"}:
                AgentInbox(str(self.home)).send("worker-build", "operator", "Synthetic build completed. Review the artifact before publishing.")
                state.agent_tasks.create({"title": "Example inspection", "prompt": "Inspect the synthetic host.",
                                          "directory": str(self.home), "mode": "inspect", "interval": "once"}, run_now=False, draft=True)
            marker.touch(mode=0o600)

    def identity(self) -> dict:
        return {"available": True, "git_error": "", "github_error": "", **{
            key: self.data[key] for key in ("name", "email", "github_present")
        }}

    def set_identity(self, name: str, email: str) -> None:
        with self.lock:
            self.data.update(name=validate_git_author_name(name), email=validate_git_author_email(email))
            self.save()

    def set_token(self, token: str | None) -> None:
        if token is not None:
            validate_github_token(token)
        with self.lock:
            self.data["github_present"] = token is not None  # Never retain even a sample token.
            self.save()

    def runner_status(self) -> github_runner.RunnerSnapshot:
        with self.lock:
            registered = self.data.get("runner_registered", self.name != "empty")
            policy = validate_runner_policy({
                "schema": 1, "registration": "registered" if registered else "pending",
                "repository": "" if self.name == "empty" else "example/private-project",
                **({"organization": "example-org", "runner_group": "Private CI"} if self.name == "empty" else {}),
                "runner_version": RUNNER_VERSION, "allowed_users": ["operator", "maintainer"], "labels": ["debian-ci"],
            })
            result = github_runner.RunnerSnapshot(
                policy=policy,
                service={"ActiveState": "failed" if self.name == "degraded" else "active" if registered else "inactive",
                         "SubState": "failed" if self.name == "degraded" else "running" if registered else "dead",
                         "UnitFileState": "enabled" if registered else "disabled",
                         "Result": "exit-code" if self.name == "degraded" else "success"},
                outcomes=[] if self.name == "empty" else [
                    {"time": "2026-01-01T09:00:00Z", "allowed": allowed, "reason": reason,
                     "identity": identity, "event": "workflow_dispatch", "run_id": run_id}
                    for allowed, reason, identity, run_id in ((False, "actor-denied", "visitor", "102"),
                                                            (True, "allowed", "operator", "101"))
                ],
                issues=["Synthetic runner inspection incomplete"] if self.name == "degraded" else [],
            )
            if self.name == "full":
                for repository in ("example/second-project", "example/third-project"):
                    ready = repository in self.data.get("runner_registered_repos", [])
                    result.instances.append(github_runner.RunnerSnapshot(
                        policy={**policy, "repository": repository, "registration": "registered" if ready else "pending"},
                        service={"ActiveState": "active" if ready else "inactive",
                                 "UnitFileState": "enabled" if ready else "disabled"},
                    ))
            return result

    def register_runner(self, token: bytes) -> None:
        validate_runner_registration_token(token)
        with self.lock:
            status = self.runner_status()
            pending = next((item for item in [status, *status.instances] if item.policy["registration"] == "pending"), None)
            if pending is None:
                raise ValueError("Synthetic runner is already registered")
            if token == b"reject-sample-runner-token":
                raise RuntimeError("Synthetic registration rejected")
            if pending is status:
                self.data["runner_registered"] = True
            else:
                self.data.setdefault("runner_registered_repos", []).append(pending.policy["repository"])
            self.save()

    def exchange(self, message: dict) -> dict:
        with self.lock:
            if message["action"] == "admin-status":
                records = copy.deepcopy(self.data["requests"])
                privilege = [{"id": row["id"], "created": row["created"], "expires": row["created"] + 3600,
                    "state": row["state"], "operation": row.get("plan", {}).get("operation", "admin.run"),
                    "target": "" if row.get("plan", {}).get("operation") == "command.run" else row.get("plan", {}).get("parameters", {}).get("unit", row["action"]),
                    "execution": None, "review_url": row.get("review_url", "https://127.0.0.1/fixture-approvals/" + row["id"])}
                    for row in records]
                return {"blocked": {}, "requests": [row for row in records if row.get("plan", {}).get("operation", "admin.run") == "admin.run"],
                        "privilege_requests": privilege, "restartable_services": ["example.service"], "unit_state": "inactive"}
            if message["action"] == "request":
                if message["operation"] in {"service.restart", "command.run"}:
                    request = {"id": uuid.uuid4().hex, "action": message["operation"],
                               "state": "failed" if self.name == "degraded" else "succeeded", "created": time.time(),
                               "plan": {"operation": message["operation"], "parameters": message["parameters"]}}
                    request["review_url"] = "https://127.0.0.1/fixture-approvals/" + request["id"]
                    self.data["requests"].insert(0, request)
                    self.save()
                    return copy.deepcopy(request)
                self.data["requests"].insert(0, {"id": uuid.uuid4().hex, "action": message["parameters"]["action"],
                    "state": "failed" if self.name == "degraded" else "succeeded", "created": time.time()})
                self.data["requests"] = self.data["requests"][:20]
                self.save()
                return {}
            if message["action"] in {"status", "cancel"}:
                request = next((item for item in self.data["requests"] if item["id"] == message["id"]), None)
                if request is None:
                    raise ValueError("Synthetic request was not found")
                if message["action"] == "cancel" and request["state"] == "pending":
                    request["state"] = "cancelled"
                    self.save()
                return copy.deepcopy(request)
            raise ValueError("Unsupported synthetic broker operation")

    def diagnostics(self, query) -> dict:
        return {"issues": ["Synthetic journal access restricted"] if self.name == "degraded" else [],
                "properties": {} if self.name == "empty" else {"LoadState": "loaded", "ActiveState": "active"},
                "events": [] if self.name == "empty" else [{"timestamp": "2026-01-01T09:00:00+00:00", "priority": "3",
                    "message": "Synthetic upstream connection refused"}]}

    def policy(self, values: dict) -> None:
        with self.lock:
            self.data["policy"] = validate_policy(values)
            self.save()

    def collect_agent_diagnostics(self) -> bool:
        with self.lock:
            self.data["diagnostics"] = True
            self.save()
        return True

    def collect_storage(self, *, mode: str = "quick") -> bool:
        with self.lock:
            self.data["storage_loaded"] = True
            self.data["storage_mode"] = mode
            self.save()
        return True

    def agent_diagnostics(self) -> dict:
        return {"status": "loaded" if self.data.get("diagnostics") else "not_loaded", "checked_at": 1767258000,
                "tools": [{"tool": tool, "installed": True, "version": "synthetic development runtime",
                           "credential": False} for tool in ("codex", "gh") if tool in self.features.tools],
                "t3": None, "maintenance": {"active": self.data["hold"], "status": "synthetic",
                                             "expires_at": "synthetic expiry"},
                "record": None, "record_error": "Synthetic diagnostics only; no host tools were inspected"}

    def check(self) -> None:
        with self.lock:
            self.data["checked"] = True
            self.save()

    def hold(self, active: bool) -> None:
        with self.lock:
            self.data["hold"] = active
            self.save()

    def hold_for_hours(self, hours: int) -> None:
        if not 1 <= hours <= maintenance.MAX_HOLD_HOURS:
            raise ValueError("Choose a whole number of hours from 1 to 72")
        self.hold(True)

    def reports(self) -> dict:
        return {"username": "operator", "context": {"system_type": "server_dev"}, "reports": [{
            "checked_at": "2026-01-01T09:00:00+00:00", "check_status": "completed", "security_audit": False,
            "issues": [], "routines": [], "review": {"status": "skipped", "reason": "Synthetic development check"},
        }] if self.data["checked"] else []}

    def update(self) -> bool:
        self.state.action_status = "failed" if self.name == "degraded" else "complete"
        self.state.action_message = "Synthetic update failed" if self.name == "degraded" else "Synthetic update completed"
        return True

    def execute(self, task, home, cancel) -> dict:
        failed = self.name == "degraded"
        return {"status": "failed" if failed else "completed", "outcome": "blocked" if failed else "complete",
                "message": "Synthetic execution; no provider was contacted", "output": "Development fixture result",
                "summary": "Synthetic host review completed. An example follow-up is ready for review." if task["title"] == "Example inspection" else "Synthetic verification completed; evidence retained.",
                "follow_up": {"title": "Verify synthetic service", "reason": "Demonstrate a reviewed follow-up with exact prerequisites.",
                              "prompt": "Verify the synthetic service's results and report the evidence.", "directory": home,
                              "mode": "workspace", "workspace": "run-directory", "network": True, "temporary_files": False,
                              "writable_directories": [], "operator_requirements": [], "service_restarts": ["example.service"]}
                              if task["title"] == "Example inspection" else None,
                "environment_feedback": "Synthetic environment feedback for inbox review." if task["title"] == "Example inspection" else "",
                "exit_code": 1 if failed else 0, "tokens": 0}
