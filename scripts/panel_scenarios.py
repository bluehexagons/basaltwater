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
from common.service_tools import web_panel_jobs as jobs
from common.service_tools import web_panel_maintenance as maintenance
from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_storage as storage
from common.service_tools.web_panel_features import PanelFeatures
from lib import agent_tasks
from lib.agent_inbox import AgentInbox
from lib.atomic_io import read_json_file, write_json_atomic
from lib.maintenance_agent_config import DEFAULTS, validate_policy
from lib.validation import validate_git_author_email, validate_git_author_name, validate_github_token
from scripts.panel_samples import job_rows

SCENARIOS = ("minimal", "full", "empty", "degraded")


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
        self.features = PanelFeatures() if name == "minimal" else PanelFeatures(
            tools=frozenset({"codex", "git", "gh", "butler", "steamcmd"}), t3=True, maintenance=True, admin=True,
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
        self.replace(state, "t3_update_available", return_value=self.features.t3)
        self.replace(state, "trigger_t3_update", side_effect=self.update)
        self.replace(jobs, "collect_jobs", return_value=jobs.JobSnapshot(
            [] if self.name == "empty" else job_rows(),
            ["Synthetic timer collection incomplete"] if self.name == "degraded" else [], absent=9,
        ))
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
        self.replace(state.git_settings, "snapshot", side_effect=self.identity)
        self.replace(state.git_settings, "set_identity", side_effect=self.set_identity)
        self.replace(state.git_settings, "set_github_token", side_effect=self.set_token)
        self.replace(state.git_settings, "remove_github_token", side_effect=lambda: self.set_token(None))
        state.admin.approval_url = "https://127.0.0.1/fixture-approvals"
        self.replace(admin, "exchange", side_effect=self.exchange)
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
        self.replace(state.agent_tasks.contexts, "record", return_value=None)
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

    def exchange(self, message: dict) -> dict:
        with self.lock:
            if message["action"] == "admin-status":
                return {"blocked": {}, "requests": copy.deepcopy(self.data["requests"]), "unit_state": "inactive"}
            if message["action"] == "request":
                self.data["requests"].insert(0, {"id": uuid.uuid4().hex, "action": message["parameters"]["action"],
                    "state": "failed" if self.name == "degraded" else "succeeded", "created": time.time()})
                self.data["requests"] = self.data["requests"][:20]
                self.save()
                return {}
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
                "exit_code": 1 if failed else 0, "tokens": 0}
