"""Agent diagnostics and prompt task controls for the authenticated panel."""

from __future__ import annotations

import copy
import html
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from common.service_tools.web_panel_diagnostics import _redact_log_message
from common.service_tools.web_panel_templates import panel_navigation, render_document
from lib.agent_cli import _tool_path, inspect_agent_tools, inspect_t3code
from lib.agent_maintenance import inspect_agent_maintenance
from lib.agent_readiness import load_agent_readiness_record
from lib.agent_tasks import (
    AgentTasks, DEFAULT_FAILURE_LIMIT, DEFAULT_TIMEOUT_MINUTES, EFFORTS, INTERVALS, MAX_FAILURE_LIMIT,
    MAX_PROMPT_BYTES, MAX_TIMEOUT_MINUTES, WEB_SEARCH_MODES, codex_models,
)


_REPOSITORY_EDIT_PREFLIGHT = (
    "Read this repository's agent instructions. Verify that the selected directory belongs to the intended Git checkout and inspect Git status, including untracked files. "
    "If there are existing changes, stop and report that they need review before another editing run. Do not stash, reset, clean, commit, or switch branches to clear them. "
)


PROMPT_TEMPLATES = {
    "maintenance": {
        "scope": "host", "description": "Inspect resources, services, update timers, and agent readiness.",
        "title": "Host maintenance review", "mode": "inspect", "interval": "daily", "network": False,
        "prompt": "Inspect this host's resource usage, failed services, update timers, reboot state, and agent readiness using available Basaltwater diagnostics. Check T3 Code only if installed. Summarize issues, evidence, and recommended repairs. Do not change the host or expose credentials.",
    },
    "dependencies": {
        "scope": "repository", "description": "Prepare dependency changes and validation for review each week.",
        "title": "Update repository dependencies", "mode": "workspace", "interval": "weekly", "network": True, "temporary_files": True,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Update dependencies using the repository's existing package manager, lockfiles, and update policy. Prefer compatible updates within its declared constraints; report major upgrades needing separate migration work. Run the relevant checks, explain changes and compatibility concerns, and leave the result ready for review. Do not commit, push, publish, or deploy.",
    },
    "repository": {
        "scope": "repository", "description": "Inspect repository state and identify maintenance work.",
        "title": "Repository health check", "mode": "inspect", "interval": "weekly", "network": False,
        "prompt": "Read this repository's agent instructions and inspect Git status, dependency manifests, CI configuration, and recent test evidence. Identify failing checks, stale dependencies, and maintenance gaps. Report findings with file references and suggested next steps. Do not modify files or publish changes.",
    },
    "ci-repair": {
        "scope": "repository", "title": "Repair failing checks",
        "description": "Reproduce a test, lint, or build failure and prepare a focused fix.",
        "mode": "workspace", "interval": "once", "network": False, "temporary_files": True, "timeout_minutes": 60,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Identify failing tests, lint checks, or builds from available local evidence. Reproduce the failure using the existing tooling, then make the smallest justified fix and rerun relevant checks. Do not weaken checks or remove tests to make them pass. If a failure cannot be reproduced or tooling is unavailable, report the missing evidence rather than guessing. Summarize the cause, changes, and validation. Do not commit, push, publish, or deploy.",
    },
    "regression-tests": {
        "scope": "repository", "title": "Add regression tests",
        "description": "Cover important behavior and edge cases using the existing test suite.",
        "mode": "workspace", "interval": "once", "network": False, "temporary_files": True, "timeout_minutes": 60,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Read recent commits and existing test conventions. Identify a small number of important behaviors or edge cases lacking meaningful coverage. Add focused regression tests that verify observable behavior, using mocks and temporary directories for external or system operations. Avoid tests that simply mirror implementation details. Run the affected suite and report what the tests cover and any remaining gaps. Do not change production behavior, commit, push, publish, or deploy.",
    },
    "documentation": {
        "scope": "repository", "title": "Update repository documentation",
        "description": "Reconcile setup instructions, examples, and references with the code.",
        "mode": "workspace", "interval": "weekly", "network": False, "timeout_minutes": 30,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Compare its README, setup instructions, CLI or API examples, and configuration references with the current implementation. Correct concrete discrepancies and broken local references using the established documentation format. Preserve accurate content, avoid marketing copy, and do not invent capabilities or unverified results. Run existing documentation checks when available, and report changes and any examples that could not be verified. Do not change application behavior, commit, push, publish, or deploy.",
    },
    "security-review": {
        "scope": "repository", "title": "Review repository security",
        "description": "Review sensitive code paths and dependency advisories with evidence.",
        "mode": "inspect", "interval": "weekly", "network": False, "web_search": "live", "timeout_minutes": 30,
        "prompt": "Read this repository's instructions, dependency manifests and lockfiles, and relevant authentication, authorization, input-validation, and secret-handling code. Review plausible security issues and use available web search to check current dependency advisories against the installed versions. Cite advisory sources and distinguish confirmed affected versions from uncertain matches. Report severity, evidence, file references, suggested remediation, and coverage limits. Redact any credentials encountered. Do not modify files, run exploit payloads, commit, push, publish, or deploy.",
    },
    "release-review": {
        "scope": "repository", "title": "Review release readiness",
        "description": "Check version consistency, change notes, validation, and rollout gaps.",
        "mode": "inspect", "interval": "once", "network": False, "timeout_minutes": 20,
        "prompt": "Read this repository's instructions, Git status and diff, version declarations, changelog, release configuration, migration notes, and available CI or test results. Check version consistency, undocumented behavior changes, compatibility concerns, and missing validation or rollout steps. Identify the release target from repository evidence; if unclear, say so. Produce a readiness report with evidence, blockers, and remaining checks. Do not claim unrun checks passed or modify files, create tags, commit, push, publish, or deploy.",
    },
    "backups": {
        "scope": "host", "title": "Check backup health",
        "description": "Check backup freshness, job results, and existing verification evidence.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Identify this host's configured backup jobs using available Basaltwater diagnostics, timer state, and readable job metadata or logs. Check the last successful backup, recent failures, next scheduled run, destination capacity when available, retention metadata, and any existing integrity-check or restore-test evidence. If a backup is running, report its state. Distinguish successful job completion from verified recoverability; report missing configuration or inaccessible evidence explicitly. Summarize issues and recommended follow-up. Do not start backups, restore data, change retention, delete files, or expose backup contents or credentials.",
    },
    "storage": {
        "scope": "host", "title": "Review storage cleanup",
        "description": "Find disk and inode pressure and propose cleanup for review.",
        "mode": "inspect", "interval": "weekly", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect this host's filesystem capacity, inode usage, and readable directory-size metadata. Identify major space consumers such as logs, caches, build artifacts, and old backups using available tools. Keep scans bounded to relevant local filesystems and summarize unavailable paths. Report sizes, likely owners, retention implications, and specific cleanup candidates with commands for later review. Do not read private file contents, delete or truncate files, prune containers, alter retention, or change the host.",
    },
    "incident": {
        "scope": "host", "title": "Triage a service incident",
        "description": "Correlate failed units, recent errors, and resource pressure.",
        "mode": "inspect", "interval": "once", "network": False, "timeout_minutes": 20,
        "prompt": "Use available Basaltwater diagnostics and readable service-manager state or recent logs to identify unhealthy services on this host. Check resource pressure, unit state, recent failures, dependency failures, and relevant configuration evidence. Check T3 Code only if installed. Correlate timestamps and distinguish symptoms from likely causes. If no failure is evident, report that and the checks performed. Summarize affected services, evidence, likely causes, and recommended next diagnostic or repair steps. Do not restart services, change configuration, install packages, or expose credentials.",
    },
    "certificates": {
        "scope": "host", "title": "Check certificate expiry",
        "description": "Inspect certificate lifetimes, renewal jobs, and configuration gaps.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 10,
        "prompt": "Inspect readable public certificate metadata, configured HTTPS service references, and renewal timer or job state on this host. Report expiry dates and remaining lifetimes, highlighting certificates expired or expiring within 30 days. Check recent renewal failures and identify services using certificates where the configuration is readable. Distinguish local certificate metadata from a verified live endpoint; report inaccessible or missing evidence. Recommend follow-up for renewal or configuration gaps. Do not read private keys, issue or replace certificates, change trust stores, reload services, or expose credentials.",
    },
    "t3-readiness": {
        "requires": "t3", "scope": "host", "title": "Check T3 Code readiness",
        "description": "Check the user service, runtime, agent tools, and saved readiness evidence.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect the configured or installed T3 Code runtime using available Basaltwater agent diagnostics without repair options. Check user-service state, runtime and installed version evidence, terminal agent availability, local credential metadata, Git identity readiness, and maintenance holds. Distinguish file presence from verified authentication. Report failures and missing evidence with suggested next steps. Do not update tools, restart services, rotate credentials, read or reveal pairing passwords or credential contents, or change the host.",
    },
    "containers": {
        "requires": "containers", "scope": "host", "title": "Review container health",
        "description": "Inspect Docker or Podman health, resource use, and storage pressure.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Use the installed Docker or Podman CLI for bounded read-only inspection of local containers, health status, restart counts, resource usage, and image or volume storage metadata. Check available recent error evidence without dumping environment variables or secrets. Command presence does not prove daemon access; report unavailable sockets, permissions, or health checks explicitly. Identify failing containers and cleanup candidates with evidence. Do not start, stop, restart, exec into, pull, build, prune, remove, or change containers, images, volumes, or networks.",
    },
    "web-hosting": {
        "requires": "web_hosting", "scope": "host", "title": "Review web hosting",
        "description": "Check gateway registrations, Nginx state, and local upstream evidence.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect existing Basaltwater web gateway registrations and readable Nginx site metadata, service state, and bounded recent error logs using read-only tools. Identify static sites, forwards, missing document roots, failed upstream services, and certificate or route configuration gaps. Do not infer live public reachability from configuration; distinguish saved configuration from verified health. Report inaccessible files or checks blocked by sandbox permissions. Do not publish sites, register forwards, modify configuration, reload services, renew certificates, or expose credentials or private site contents.",
    },
    "shared-storage": {
        "requires": "shared_storage", "scope": "host", "title": "Check shared storage",
        "description": "Check configured SMB shares and existing CIFS or SSHFS mounts.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect configured Samba shares and existing CIFS or SSHFS mount metadata using available Basaltwater diagnostics and readable local configuration. Check local service state, mount presence, ownership, relevant errors, and existing backup coverage. Keep checks bounded: avoid traversing unavailable network mounts, reading shared file contents, or printing credentials and mount passwords. An installed client does not prove a share is configured or reachable; report missing evidence. Do not mount, unmount, reconnect, chmod, change share configuration, delete files, or copy data.",
    },
    "remote-desktop": {
        "requires": "remote_desktop", "scope": "host", "title": "Check remote desktop readiness",
        "description": "Inspect xrdp, session-manager state, and recent connection failures.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 10,
        "prompt": "Inspect configured remote desktop access, xrdp and session-manager service state, readable session configuration, and bounded recent connection or startup errors. Report missing desktop components, service failures, and relevant permission or certificate evidence. Local readiness does not prove a remote client can connect; distinguish those checks. Do not open or capture another user's desktop, terminate sessions, restart services, modify accounts or configuration, or expose credentials.",
    },
    "github-actions": {
        "requires": "github", "scope": "repository", "title": "Review GitHub Actions failures",
        "description": "Read recent CI runs with GitHub CLI and identify recurring failures.",
        "mode": "workspace", "interval": "daily", "network": True, "timeout_minutes": 20,
        "prompt": "Read this repository's instructions and Git status; preserve unrelated work. Identify its GitHub repository from the existing remote without changing it. Use GitHub CLI read-only commands to inspect a bounded number of recent Actions runs and relevant failed-job logs. Compare failures with local workflow configuration; separate likely code, infrastructure, flaky-test, and permission causes. Redact credentials and report unavailable authentication or repository access explicitly. Produce an evidence-backed triage report. Do not modify files, rerun or cancel workflows, create issues or comments, commit, push, publish, or deploy.",
    },
    "browser-smoke": {
        "requires": "browser", "scope": "repository", "title": "Run browser smoke checks",
        "description": "Check key application flows with the managed browser tooling.",
        "mode": "workspace", "interval": "once", "network": True, "temporary_files": True, "timeout_minutes": 30,
        "prompt": "Read this repository's instructions and Git status; preserve unrelated work. Check the managed browser capability using available Basaltwater diagnostics, then use the repository's documented local preview and configured browser tools to inspect key flows, console errors, and a narrow viewport. A healthy installation does not prove browser tools are exposed in this session; if unavailable, report browser coverage as blocked and continue relevant local checks. Use an isolated test environment and synthetic data; avoid production endpoints and real user transactions. Start a loopback local preview only when repository instructions support it, and stop processes you start. Do not create managed gateway previews or forwards. Keep screenshots in the browser's private evidence directory and report results, URLs, and blocked checks. Do not install tools, change application code, publish, deploy, or send messages.",
    },
    "godot-export": {
        "requires": "godot", "scope": "repository", "title": "Validate Godot web export",
        "description": "Check export presets and prepare a local web build for review.",
        "mode": "workspace", "interval": "once", "network": False, "temporary_files": True, "timeout_minutes": 60,
        "prompt": "Read this repository's instructions and Git status; preserve unrelated work. Confirm a Godot project and suitable web export preset exist in the selected checkout. Check the installed Godot version and existing export templates, then use the documented headless export workflow to produce a local web build in the repository's designated ignored output directory. If the output location is not documented or would overwrite tracked or unrelated files, report the blocker. Report export errors, artifact paths, and runtime or browser checks still needed. Do not download tools or templates, change project sources or presets, commit, push, publish, or deploy.",
    },
    "homebox-health": {
        "requires": "homebox", "scope": "host", "title": "Check HomeBox data protection",
        "description": "Review service health and backup coverage for inventory and attachments.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect the configured HomeBox service using available Basaltwater diagnostics, readable service metadata, and bounded recent error logs. Identify inventory database and attachment storage paths from configuration without reading their contents. Check available capacity and existing backup-job evidence covering both database and attachments; distinguish job completion from restore verification. Report missing coverage, service failures, and inaccessible evidence. Do not change inventory, query private records, start backups, restore data, copy database files, restart services, or expose credentials.",
    },
    "gogs-health": {
        "requires": "gogs", "scope": "host", "title": "Check Gogs repository hosting",
        "description": "Review service errors, repository storage, and existing backup evidence.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect the configured or installed Gogs service using readable local service and configuration metadata and bounded recent error logs. Identify repository, database, and attachment storage locations without reading private repository or database contents. Check capacity, relevant ownership evidence, and existing backup coverage. Distinguish configured locations from verified availability and report inaccessible evidence. Recommend follow-up for failures or protection gaps. Do not change users or repositories, run repository hooks, migrate data, restart services, start backups, or expose credentials.",
    },
    "antistatic-health": {
        "requires": "antistatic", "scope": "host", "title": "Check Antistatic services",
        "description": "Review configured lobby and database service state and recent errors.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect the configured Antistatic lobby and database services using available Basaltwater diagnostics, readable service metadata, and bounded recent error logs. Check service state, dependency failures, resource pressure, database storage capacity, and existing backup-job evidence where available. Distinguish configuration from verified application readiness and report unavailable checks. Do not query player records, send game requests, modify databases, restart services, start backups, or expose credentials.",
    },
    "notifications-health": {
        "requires": "notifications", "scope": "host", "title": "Check notification delivery",
        "description": "Inspect receiver configuration and existing delivery evidence.",
        "mode": "inspect", "interval": "daily", "network": False, "timeout_minutes": 10,
        "prompt": "Inspect the configured Basaltwater panel notification receiver using available diagnostics and readable non-secret configuration or existing delivery logs. Check panel and proxy service state, accepted-event timestamps, sender failure or retry evidence, and relevant throttling or authentication errors. No recent events alone does not prove delivery is broken; report expected cadence only when configured evidence supports it. Redact sender links and bearer tokens. Do not reveal or rotate tokens, send test notifications or messages, change sender configuration, or restart services.",
    },
    "privilege-health": {
        "requires": "privilege", "scope": "host", "title": "Check privilege approval readiness",
        "description": "Review broker service, policy metadata, and available audit evidence.",
        "mode": "inspect", "interval": "weekly", "network": False, "timeout_minutes": 15,
        "prompt": "Inspect the configured Basaltwater privilege approval broker using available diagnostics, readable service and policy metadata, and sanitized audit evidence. Check broker service state, socket or endpoint configuration, policy coverage, and recent denial or execution errors. Report inaccessible evidence and distinguish configured permissions from verified readiness. Do not read approval credentials, submit or approve requests, execute privileged commands, change policies or allowlists, restart services, or expose secrets.",
    },
}

_STYLE = """
.agent-columns { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(240px, 1fr); gap: 18px; align-items: start; }
.agent-panel { padding: 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
.agent-panel h2 { margin-bottom: 12px; }
.agent-form { display: grid; gap: 12px; }
.agent-form label { display: block; font-size: .85rem; font-weight: 650; }
.agent-form input:not([type=checkbox]), .agent-form textarea { display: block; width: 100%; min-height: 44px;
  margin-top: 5px; padding: 9px 10px; background: var(--bg); color: var(--text); border: 1px solid var(--line); border-radius: 8px; font: inherit; }
.agent-form textarea { resize: vertical; min-height: 160px; font-size: .85rem; }
.agent-form input:not([type=checkbox]), .agent-form textarea, .agent-form select { font-weight: 400; }
.agent-form textarea:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.agent-form select { background: var(--bg); width: 100%; }
.agent-form details[open] { display: grid; gap: 12px; }
.agent-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.agent-check { display: flex !important; gap: 9px; align-items: start; font-weight: 400 !important; }
.agent-check input { margin-top: 4px; width: 18px; height: 18px; flex: none; accent-color: var(--accent); }
.agent-help { color: var(--muted); font-size: .78rem; margin: 0; }
.agent-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.agent-buttons form { margin: 0; }
.agent-buttons button { min-height: 44px; font-size: .8rem; }
.agent-buttons a { color: var(--accent); min-height: 44px; display: inline-flex; align-items: center; }
.agent-secondary { background: var(--accent-soft); color: var(--accent); }
.agent-table-wrap { overflow-x: auto; }
.agent-table { width: 100%; min-width: 800px; border-collapse: collapse; font-size: .8rem; }
.agent-table th { text-align: left; color: var(--muted); font-weight: 650; white-space: nowrap; }
.agent-table caption { text-align: left; padding-bottom: 8px; }
.agent-table th, .agent-table td { padding: 12px 10px; border-bottom: 1px solid var(--accent-soft); vertical-align: top; }
.agent-table td:first-child { min-width: 160px; }
.agent-table td:nth-child(2), .agent-table td:nth-child(3), .agent-table td:nth-child(4) { white-space: nowrap; }
.agent-table .agent-buttons { min-width: 190px; }
.agent-table small { display: block; color: var(--muted); margin-top: 4px; }
.agent-template { display: block; padding: 12px 0; border-top: 1px solid var(--accent-soft); text-decoration: none; color: var(--accent); }
.agent-template span { display: block; color: var(--muted); font-size: .8rem; margin-top: 4px; }
.agent-template small { display: block; color: var(--muted); font-size: .75rem; margin-top: 5px; }
.agent-template[aria-current=true] strong { text-decoration: underline; text-underline-offset: 3px; }
.agent-template-group { margin-top: 12px; }
.agent-runtime { margin: 14px 0 0; display: grid; gap: 8px; }
.agent-runtime div { display: flex; justify-content: space-between; gap: 12px; }
.agent-runtime dt { color: var(--muted); flex: none; }
.agent-runtime dd { margin: 0; text-align: right; }
.agent-run summary { width: 100%; }
.agent-run .event-head { align-items: center; }
.agent-run p { margin: 4px 0; }
.agent-summary { margin: 0 0 18px; }
.agent-columns { margin-top: 18px; }
.agent-history { display: grid; gap: 10px; }
.agent-history .agent-run { margin: 0; }
.agent-active { margin-bottom: 18px; border-left: 3px solid var(--accent); }
.agent-active p { margin: 6px 0; }
@media (max-width: 1150px) { .agent-columns { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 560px) { .agent-fields { grid-template-columns: 1fr; } .agent-buttons button { width: auto; } }
"""


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _time(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError, OverflowError, OSError):
        return "—"


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _run_seconds(run: dict[str, Any], now: float) -> float:
    if run["status"] != "running" and run.get("duration_seconds") is not None:
        return run["duration_seconds"]
    return max(0, (run["finished_at"] if run["finished_at"] is not None else now) - run["started_at"])


def _render_run_summary(tasks: list[dict[str, Any]], runs: list[dict[str, Any]], now: float) -> str:
    finished = [run for run in runs if run["status"] != "running"]
    completed = sum(run["status"] == "completed" for run in finished)
    failed = sum(run["status"] in {"failed", "interrupted"} for run in finished)
    cancelled = sum(run["status"] == "cancelled" for run in finished)
    cards = (
        ("Work queue", f'{sum(run["status"] == "running" for run in runs)} running', f'{sum(task["queued"] for task in tasks)} queued · executes one at a time'),
        ("Schedules", f'{sum(task["enabled"] for task in tasks)} repeating', f'{sum(task.get("draft", False) for task in tasks)} drafts · {sum(task.get("auto_paused", False) for task in tasks)} paused after failures'),
        ("Recent outcomes", f"{completed} completed", f"{failed} failed/interrupted · {cancelled} cancelled · latest {len(finished)} finished runs"),
        ("Recorded runtime", _duration(sum(_run_seconds(run, now) for run in finished)), "Latest retained finished runs · wall time, including waits"),
    )
    return '<dl class="overview-grid agent-summary" aria-label="Agent workflow summary">' + "".join(
        f'<div class="metric"><dt>{label}</dt><dd><span class="metric-value">{_escape(value)}</span><span class="metric-description">{_escape(note)}</span></dd></div>'
        for label, value, note in cards
    ) + '</dl>'


class AgentDiagnostics:
    """Run bounded existing checks on request, without blocking form submission."""

    def __init__(self, home: str, t3_configured: bool) -> None:
        self.home, self.t3_configured = home, t3_configured
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {"status": "not_loaded"}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def trigger(self) -> bool:
        with self._lock:
            if self._snapshot["status"] == "running":
                return False
            self._snapshot = {"status": "running"}
        threading.Thread(target=self._collect, daemon=True, name="agent-diagnostics").start()
        return True

    def _collect(self) -> None:
        try:
            tools = inspect_agent_tools(["codex", "claude", "opencode", "gh"], home=self.home)
            t3_present = self.t3_configured or os.path.isdir(os.path.join(self.home, ".t3/runtime"))
            t3 = inspect_t3code(home=self.home, fix=False) if t3_present else None
            record_error = ""
            try:
                record = load_agent_readiness_record(home=self.home)
            except RuntimeError as exc:
                record, record_error = None, str(exc)
            result = {"status": "loaded", "checked_at": time.time(), "tools": tools,
                      "t3": t3, "maintenance": inspect_agent_maintenance(home=self.home),
                      "record": record, "record_error": record_error}
        except Exception as exc:
            result = {"status": "failed", "message": f"Diagnostics failed: {type(exc).__name__}: {exc}"}
        with self._lock:
            self._snapshot = result


def parse_agent_query(raw: str) -> dict[str, str]:
    if len(raw) > 128:
        raise ValueError("Invalid agent view")
    query = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=1)
    if not query:
        return {}
    if set(query) == {"template"} and query["template"][0] in PROMPT_TEMPLATES:
        return {"template": query["template"][0]}
    for name in ("edit", "copy", "reuse"):
        if set(query) == {name} and len(query[name][0]) == 32 and all(c in "0123456789abcdef" for c in query[name][0]):
            return {name: query[name][0]}
    raise ValueError("Invalid agent view")


def _action_form(csrf: str, identifier: str, action: str, label: str) -> str:
    return f'''<form method="post" action="/actions/agent-task"><input type="hidden" name="csrf" value="{_escape(csrf)}">
<input type="hidden" name="id" value="{_escape(identifier)}"><button class="agent-secondary" name="action" value="{action}">{label}</button></form>'''


def _available_templates(manifest: dict[str, Any], home: str) -> dict[str, dict[str, Any]]:
    """Use saved configuration and executable presence, without executing tools."""

    def labels(section: str) -> set[str]:
        return {row["label"] for row in manifest.get(section, [])
                if isinstance(row, dict) and isinstance(row.get("label"), str)}

    def installed(*names: str) -> bool:
        return any(_tool_path(name, home) for name in names)

    services, access = labels("services"), labels("access")
    features = manifest.get("features", {})
    capabilities = {
        "t3": "T3 Code configured" if features.get("t3_update") is True else
              "T3 Code runtime detected" if os.path.isdir(os.path.join(home, ".t3/runtime")) else "",
        "containers": "Container CLI detected" if installed("docker", "podman") else "",
        "web_hosting": "Web server configured" if "Web server" in services else
                       "Web hosting command detected" if installed("basaltwater-web", "nginx") else "",
        "shared_storage": "Samba configured" if "Samba / SMB" in access else
                          "Shared storage command detected" if installed("smbd", "mount.cifs", "sshfs") else "",
        "remote_desktop": "Remote desktop configured" if "Remote desktop" in access else
                          "xrdp command detected" if installed("xrdp") else "",
        "github": "GitHub CLI detected" if installed("gh") else "",
        "browser": "Managed browser launcher detected" if installed("basaltwater-playwright-mcp") else "",
        "godot": "Godot command detected" if installed("godot", "godot4") else "",
        "homebox": "HomeBox configured" if "HomeBox" in services else "",
        "gogs": "Gogs configured" if "Gogs" in services else
                "Gogs command detected" if installed("gogs") else "",
        "antistatic": "Antistatic configured" if services & {"Antistatic lobby", "Antistatic DB"} else "",
        "notifications": "Notification ingest enabled" if features.get("notification_ingest") is True else "",
        "privilege": "Privilege approvals configured" if "Privilege approvals" in services else "",
    }
    return {key: {**template, "availability": capabilities.get(template.get("requires", ""), "")}
            for key, template in PROMPT_TEMPLATES.items()
            if "requires" not in template or capabilities[template["requires"]]}


def _render_templates(selected: str, templates: dict[str, dict[str, Any]]) -> str:
    active_scope = templates.get(selected, {}).get("scope", "repository")
    groups = []
    for scope, label in (("repository", "Repository work"), ("host", "Host checks")):
        links = []
        for key, template in templates.items():
            if template["scope"] != scope:
                continue
            mode = "Inspect only" if template["mode"] == "inspect" else "Workspace changes"
            interval = {"once": "Once", "daily": "Daily", "weekly": "Weekly"}[template["interval"]]
            runtime = template.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
            current = ' aria-current="true"' if key == selected else ""
            permissions = []
            if template["network"]:
                permissions.append("command network")
            if template.get("temporary_files"):
                permissions.append("temporary writes")
            if template.get("web_search") == "live":
                permissions.append("live web search")
            settings = " · ".join([mode, interval, f"{runtime} min cap", *permissions])
            availability = f'<small>{_escape(template["availability"])}</small>' if template.get("availability") else ""
            links.append(f'<a class="agent-template" href="/agents?template={_escape(key)}"{current}><strong>{_escape(template["title"])}</strong><span>{_escape(template["description"])}</span>{availability}<small>{_escape(settings)}</small></a>')
        groups.append(f'<details class="agent-template-group"{" open" if scope == active_scope else ""}><summary>{label} · {len(links)} templates</summary>{"".join(links)}</details>')
    return "".join(groups)


def render_agents(state: Any, style: str, query: dict[str, str], *, error: str = "", submitted: dict[str, Any] | None = None, prepared: bool = False) -> str:
    """Render forms, diagnostics, schedules, and history without running agents."""

    manager: AgentTasks = state.agent_tasks
    snapshot = {"tasks": [], "runs": []}
    storage_error = False
    try:
        snapshot = manager.snapshot()
    except (OSError, RuntimeError, ValueError) as exc:
        storage_error = True
        error = error or str(exc)
    tasks, runs = snapshot["tasks"], snapshot["runs"]
    now = time.time()
    diagnostics = state.agent_diagnostics.snapshot()
    ready = manager.available() and not manager.error and not storage_error
    codex_installed = bool(_tool_path("codex", manager.home))
    templates = _available_templates(state.manifest, manager.home)
    defaults: dict[str, Any] = {"title": "", "prompt": "", "directory": manager.home,
                               "mode": "inspect", "interval": "once", "model": "", "network": False, "id": "",
                               "effort": "", "timeout_minutes": DEFAULT_TIMEOUT_MINUTES,
                               "web_search": "disabled", "session_history": False, "custom_model": "", "temporary_files": False,
                               "failure_limit": DEFAULT_FAILURE_LIMIT}
    source_note = ""
    if "template" in query:
        template = templates.get(query["template"])
        if template is None:
            error = error or "This template is unavailable: its required feature or software was not detected. Choose another template or write a custom prompt."
        else:
            defaults.update(template)
            if template["scope"] == "repository":
                defaults["directory"] = ""
    if "edit" in query:
        task = next((task for task in tasks if task["id"] == query["edit"]), None)
        if task is None:
            error = error or "Prompt task was not found"
        else:
            defaults.update(task)
    if "copy" in query or "reuse" in query:
        if "copy" in query:
            source = next((task for task in tasks if task["id"] == query["copy"]), None)
            source_note = "Copied saved task settings. Choose a name and directory, then save a draft, run, or schedule the new task."
        else:
            source = next((run["task"] for run in runs if run["id"] == query["reuse"]), None)
            source_note = "Reusing the prompt and execution settings recorded for this run. Repetition defaults to Once; review the settings before submitting."
        if source is None:
            error = error or "The source task or run is no longer available"
            source_note = ""
        else:
            defaults.update(source)
            defaults["id"] = ""
            if "reuse" in query:
                defaults["interval"] = "once"
    if submitted:
        defaults.update(submitted)
    if prepared:
        source_note = "Prepared from Agent tools. No task has been saved or queued. Review the prompt, model, runtime, permissions, and repetition before submitting."
    editing = bool(defaults["id"])
    alert = f'<aside class="status failed" role="alert"><strong>Agent tools need attention</strong><p>{_escape(error or manager.error)}</p></aside>' if error or manager.error else ""
    if not ready:
        alert += '<p class="empty">Prompt execution requires a non-root panel account with its own home directory. Diagnostics remain available.</p>'
    elif not codex_installed:
        alert += '<p class="empty">Install and sign in to Codex as the panel account to run prompts. T3 Code is optional.</p>'
    disabled = " disabled" if not ready or not codex_installed else ""
    save_disabled = " disabled" if not ready else ""
    options = "".join(f'<option value="{key}"{" selected" if key == defaults["interval"] else ""}>{label}</option>' for key, label in (
        ("once", "Once"), ("hourly", "Every hour"), ("daily", "Every 24 hours"), ("weekly", "Every 7 days")))
    modes = "".join(f'<option value="{key}"{" selected" if key == defaults["mode"] else ""}>{label}</option>' for key, label in (
        ("inspect", "Inspect only"), ("workspace", "Workspace changes")))
    models = [("", "Configured default")] + [(model["slug"], model["name"]) for model in codex_models(manager.home)]
    if defaults["model"] and defaults["model"] not in {key for key, _ in models}:
        models.append((defaults["model"], "Custom · " + str(defaults["model"])))
    model_options = "".join(f'<option value="{_escape(key)}"{" selected" if key == defaults["model"] else ""}>{_escape(label)}</option>' for key, label in models)
    effort_options = "".join(f'<option value="{key}"{" selected" if key == defaults["effort"] else ""}>{label}</option>' for key, label in [("", "Configured default")] + [(level, "Extra high (xhigh)" if level == "xhigh" else "Maximum (max)" if level == "max" else level.capitalize()) for level in EFFORTS])
    search_options = "".join(f'<option value="{key}"{" selected" if key == defaults["web_search"] else ""}>{label}</option>' for key, label in zip(WEB_SEARCH_MODES, ("Disabled", "Cached results", "Live web search")))
    composer = f'''<div class="agent-panel"><h2>{"Edit prompt task" if editing else "Review prepared task" if prepared else "New prompt task"}</h2>
{f'<p class="agent-help">{source_note}</p>' if source_note else ''}
<form class="agent-form" method="post" action="/actions/agent-task/save">
<input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><input type="hidden" name="id" value="{_escape(defaults['id'])}">
<div class="agent-fields"><label>Task name<input name="title" value="{_escape(defaults['title'])}" maxlength="120" required placeholder="Weekly dependency update"></label>
<label>Working directory<input name="directory" value="{_escape(defaults['directory'])}" required placeholder="~/repos/project"></label></div>
<label>Prompt<textarea name="prompt" maxlength="{MAX_PROMPT_BYTES}" required placeholder="Describe the task and what a successful result should include.">{_escape(defaults['prompt'])}</textarea></label>
<div class="agent-fields"><label>Model<select name="model">{model_options}</select></label><label>Reasoning effort<select name="effort">{effort_options}</select></label></div>
<p class="agent-help">Model choices use Codex's local cache; availability and effort support depend on your account and model. Use Additional options for a custom model ID.</p>
<div class="agent-fields"><label>Maximum runtime (minutes)<input type="number" name="timeout_minutes" min="1" max="{MAX_TIMEOUT_MINUTES}" step="1" value="{_escape(defaults['timeout_minutes'])}" required></label><label>Repeat<select name="interval">{options}</select></label></div>
<p class="agent-help">1 minute to 7 days, including time spent waiting. A duration cap is not an exact spending limit.</p>
<label>Pause schedule after failures<input type="number" name="failure_limit" min="0" max="{MAX_FAILURE_LIMIT}" step="1" value="{_escape(defaults['failure_limit'])}" required></label>
<p class="agent-help">Default 3 consecutive failed or interrupted runs; 0 keeps repeating. A completed run or Resume resets the count. Cancellation does not count as failure.</p>
<label>Execution mode<select name="mode">{modes}</select></label>
<label class="agent-check"><input type="checkbox" name="network" value="1"{" checked" if defaults['network'] else ""}><span>Allow command network access for workspace changes, including downloading dependencies.</span></label>
<p class="agent-help">Inspection uses Codex's read-only sandbox. Workspace changes may edit the selected directory inside this account's home, plus temporary directories when allowed below. Provider requests still use the network in either mode. Configured hooks and MCP integrations retain their own permissions.</p>
<details{' open' if defaults['custom_model'] else ''}><summary>Additional options</summary>
<label>Custom model ID <span class="agent-help">(overrides the model selector when entered)</span><input name="custom_model" maxlength="100" value="{_escape(defaults['custom_model'])}" placeholder="Provider model ID"></label>
<label>Web search<select name="web_search">{search_options}</select></label>
<p class="agent-help">Controls Codex's web-search tool separately from shell-command network access.</p>
<label class="agent-check"><input type="checkbox" name="temporary_files" value="1"{" checked" if defaults['temporary_files'] else ""}><span>Allow temporary file writes for workspace changes (/tmp and TMPDIR). Needed by many test and package tools.</span></label>
<label class="agent-check"><input type="checkbox" name="session_history" value="1"{" checked" if defaults['session_history'] else ""}><span>Keep Codex session history on this account. Panel run history is retained either way.</span></label></details>
<div class="agent-buttons">{'<button name="submit" value="save"' + save_disabled + '>Save changes</button>' if editing else '<button name="submit" value="run"' + disabled + '>Run now</button><button class="agent-secondary" name="submit" value="schedule"' + disabled + '>Create schedule</button><button class="agent-secondary" name="submit" value="draft"' + save_disabled + '>Save draft</button>'}
<a href="/agents">Clear form</a></div>
<p class="agent-help">Runs execute as {_escape(state.manifest['username'])}. Repeated prompts use the same directory. Create schedule starts after one interval; Run now also enables repetition when selected. Save draft stores the task without running or scheduling it.</p></form></div>'''
    template_links = _render_templates(query.get("template", ""), templates)
    helper = f'''<aside class="agent-panel"><h2>Start from a template</h2><p class="agent-help">Use <a class="refresh-link" href="/agent-tools">Agent tools</a> to prepare system reviews, workspace cleanup, or data imports and exports with explicit source and output paths.</p><p class="agent-help">Review the prompt, choose a working directory, and adjust repetition before submitting. Additional templates appear for configured features or detected commands; detection does not verify service health or authentication.</p>{template_links}
<details><summary>Execution limits</summary><p class="agent-help">One prompt runs at a time using its saved runtime cap, model, effort, and permissions. Schedules run while this panel service is running; missed intervals produce at most one catch-up run. Host restarts interrupt active work. No root execution or sandbox bypass is offered.</p></details></aside>'''
    rows = []
    for task in tasks:
        latest = next((run for run in reversed(runs) if run["task"]["id"] == task["id"]), None)
        running = bool(latest and latest["status"] == "running")
        task_status = "Running" if running else "Queued" if task["queued"] else "Scheduled" if task["enabled"] else "Draft" if task.get("draft") else "Auto-paused" if task.get("auto_paused") else "Paused" if INTERVALS[task["interval"]] else "One-time"
        controls = _action_form(state.csrf_token, task["id"], "cancel", "Cancel run") if running or task["queued"] else _action_form(state.csrf_token, task["id"], "run", "Run now")
        if INTERVALS[task["interval"]]:
            controls += _action_form(state.csrf_token, task["id"], "pause" if task["enabled"] else "resume", "Pause" if task["enabled"] else "Start schedule" if task.get("draft") else "Resume")
        controls += f'<a href="/agents?copy={task["id"]}">Duplicate</a>'
        if not running and not task["queued"]:
            controls += f'<a href="/agents?edit={task["id"]}">Edit</a>'
            controls += _action_form(state.csrf_token, task["id"], "delete", "Remove")
        last = _escape(latest["status"]) if latest else "Not run"
        failure_count = task.get("consecutive_failures", 0)
        failure_limit = task.get("failure_limit", DEFAULT_FAILURE_LIMIT)
        failure_label = f'{failure_count} consecutive failure' + ('' if failure_count == 1 else 's')
        failure_note = f'{failure_label} · pause at {failure_limit}' if failure_limit else f'{failure_label} · auto-pause off'
        last_timing = f'{_time(latest["started_at"])} · {_duration(_run_seconds(latest, now))} {"elapsed" if running else "duration"}' if latest else ""
        rows.append(f'''<tr><td><strong>{_escape(task['title'])}</strong><small><code>{_escape(task['directory'])}</code></small><small>{_escape(task['mode'])} · command network {"on" if task['network'] else "off"} · {_escape(task['timeout_minutes'])} min cap</small><small>{_escape(task['model'] or 'Configured model')} · effort {_escape(task['effort'] or 'default')}</small></td>
<td>{task_status}<small>{_escape(task['interval'])}</small><small>{failure_note}</small></td><td>{_time(task['next_run']) if task['enabled'] else '—'}</td><td>{last}<small>{last_timing}</small></td><td><div class="agent-buttons">{controls}</div></td></tr>''')
    schedules = ('<div class="agent-panel agent-table-wrap"><table class="agent-table"><caption class="agent-help">Saved prompt tasks and recurring schedules · all times UTC</caption><thead><tr><th>Task / directory</th><th>Schedule</th><th>Next run</th><th>Last run</th><th>Actions</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>') if rows else '<p class="empty">No prompt tasks yet. Run a prompt once or create a recurring schedule above.</p>'
    history = []
    for run in reversed(runs):
        tone = "success" if run["status"] == "completed" else "error" if run["status"] in {"failed", "interrupted"} else "warning"
        elapsed = _run_seconds(run, now)
        timing = f'{_duration(elapsed)} elapsed · {_duration(max(0, run["task"]["timeout_minutes"] * 60 - elapsed))} remaining at page load' if run["status"] == "running" else f'{_duration(elapsed)} duration'
        exit_note = f' · exit code {_escape(run["exit_code"])}' if run.get("exit_code") is not None else ""
        history.append(f'''<details class="agent-panel agent-run"><summary><span class="event-head"><strong>{_escape(run['task']['title'])}</strong><span class="badge {tone}">{_escape(run['status'])}</span></span><span class="agent-help">{_time(run['started_at'])} · {_escape(run['task']['directory'])}</span></summary>
<p>{_escape(_redact_log_message(run['message']))}</p><p class="agent-help">{timing}{exit_note} · Finished {_time(run['finished_at'])} · {_escape(run['task']['mode'])} · model {_escape(run['task']['model'] or 'configured default')} · effort {_escape(run['task']['effort'] or 'default')} · {_escape(run['task']['timeout_minutes'])} min cap · command network {'on' if run['task']['network'] else 'off'} · temporary writes {'on' if run['task']['temporary_files'] else 'off'} · web search {_escape(run['task']['web_search'])} · Codex history {'kept' if run['task']['session_history'] else 'ephemeral'}</p>
<a class="refresh-link" href="/agents?reuse={run['id']}">Reuse run settings</a>
<details><summary>Prompt used for this run</summary><pre>{_escape(run['task']['prompt'])}</pre></details><pre>{_escape(_redact_log_message(run['output'])) if run['output'] else 'Output will appear when the run finishes.' if run['status'] == 'running' else 'No output recorded.'}</pre></details>''')
    diagnostics_html = _render_diagnostics(diagnostics, state.csrf_token)
    t3_html = ""
    if state.t3_update_available():
        t3_html = f'''<div class="action"><div><strong>T3 Code maintenance</strong><p>{_escape(state.action_message or 'Update the installed T3 Code service and verify readiness.')}</p></div><form method="post" action="/actions/t3-update"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><input type="hidden" name="return" value="agents"><button{' disabled' if state.action_status == 'running' else ''}>Update T3 Code</button></form></div>'''
    workbench = f'<div class="agent-columns">{composer}{helper}</div>'
    if tasks and not query and not submitted:
        workbench = f'<details><summary>Create a prompt task</summary>{workbench}</details>'
    task_section = f'''<section aria-labelledby="agent-tasks-heading"><div class="section-heading"><h2 id="agent-tasks-heading">Prompt tasks</h2><span class="count">{len(tasks)} saved · {sum(t['enabled'] for t in tasks)} repeating · {sum(t['queued'] for t in tasks)} queued</span></div>{schedules}</section>'''
    header = f'''<header class="dashboard-header"><div><p class="eyebrow">Basaltwater web panel</p><h1>Agents</h1><p class="lede">Prompt tasks and runtime diagnostics on <code>{_escape(state.manifest['host'])}</code>.</p></div><a class="refresh-link" href="/agents">Refresh status</a></header>'''
    active = next((run for run in runs if run["status"] == "running"), None)
    active_html = ""
    if active:
        elapsed = _run_seconds(active, now)
        active_html = f'''<aside class="agent-panel agent-active" role="status"><strong>Running: {_escape(active['task']['title'])}</strong><p class="agent-help">{_duration(elapsed)} elapsed · {_duration(max(0, active['task']['timeout_minutes'] * 60 - elapsed))} remaining at page load · <code>{_escape(active['task']['directory'])}</code></p><p class="agent-help">Refresh status for updated timing. Other work stays queued until this run finishes.</p></aside>'''
    content = f'''{alert}{_render_run_summary(tasks, runs, now)}{active_html}{task_section + workbench if tasks and not prepared else workbench + task_section}
<section aria-labelledby="agent-diagnostics-heading"><div class="section-heading"><h2 id="agent-diagnostics-heading">Runtime diagnostics</h2><span class="count">Loaded on request</span></div>{diagnostics_html}{t3_html}</section>
<section aria-labelledby="agent-history-heading"><div class="section-heading"><h2 id="agent-history-heading">Run history</h2><span class="count">Latest {len(runs)} runs</span></div><div class="agent-history">{''.join(history) or '<p class="empty">No prompt runs have been recorded.</p>'}</div></section>'''
    return render_document(title=f"Agents · {state.manifest['host']}", style=style + _STYLE,
                           header=header, content=content, navigation=panel_navigation(current="agents"),
                           footer='<footer><a href="/">Back to dashboard</a><span>Refresh status to see completed runs</span></footer>')


def _render_diagnostics(snapshot: dict[str, Any], csrf: str) -> str:
    form = f'''<form class="job-load" method="post" action="/actions/agent-diagnostics"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button{' disabled' if snapshot['status'] == 'running' else ''}>Check agent readiness</button></form>'''
    if snapshot["status"] != "loaded":
        return form + '<p class="empty">' + _escape(snapshot.get("message") or ("Checks are running. Refresh status to see results." if snapshot["status"] == "running" else "Check installation versions, credential metadata, T3 Code when present, and maintenance holds. Checks do not run prompts or repair the host.")) + '</p>'
    cards = []
    for tool in snapshot["tools"]:
        installed = tool["installed"]
        credential = "Needs attention" if tool.get("credential_healthy") is False else "File present; authentication not verified" if tool.get("credential") else "No credential file"
        if tool["tool"] == "codex" and tool.get("credential_healthy") is True:
            credential = "Local credential check passed"
        cards.append(f'''<div class="agent-panel"><strong>{_escape({'codex': 'Codex', 'claude': 'Claude Code', 'opencode': 'OpenCode', 'gh': 'GitHub CLI'}[tool['tool']])}</strong><dl class="agent-runtime"><div><dt>Installation</dt><dd>{'Installed' if installed else 'Not installed'}</dd></div><div><dt>Version</dt><dd>{_escape(tool.get('version') or 'Unavailable' if installed else '—')}</dd></div><div><dt>Credentials</dt><dd>{credential if installed else '—'}</dd></div></dl></div>''')
    t3 = snapshot["t3"]
    if t3:
        checks = "".join(f'<div><dt>{_escape(name.replace("_", " "))}</dt><dd>{"Passed" if result is True else "Needs attention"}</dd></div>' for name, result in t3["checks"].items())
        cards.append(f'<div class="agent-panel"><strong>T3 Code</strong><p class="agent-help">{_escape(t3.get("version") or "Version unavailable")} · {_escape(t3["status"])}</p><details><summary>Readiness details</summary><dl class="agent-runtime">{checks}</dl></details></div>')
    else:
        cards.append('<div class="agent-panel"><strong>T3 Code</strong><p class="agent-help">Not installed. Prompt tasks use the Codex CLI directly.</p></div>')
    hold = snapshot["maintenance"]
    record = snapshot["record"]
    record_note = f'Last saved readiness {_escape(record.get("recorded_at", "Unknown time"))} · boot {"current" if record.get("current_boot") is True else "previous or unknown"}' if record else _escape(snapshot["record_error"] or "No saved readiness record")
    cards.append(f'<div class="agent-panel"><strong>Maintenance hold</strong><p class="agent-help">{_escape(hold["status"])}{ " · until " + _escape(hold.get("expires_at")) if hold["active"] else ""}</p><p class="agent-help">{record_note}</p><a class="refresh-link" href="/jobs">Inspect scheduled jobs</a></div>')
    return form + f'<p class="count">Checked {_time(snapshot["checked_at"])} · credential checks inspect local metadata, not provider authentication</p><div class="grid">' + "".join(cards) + '</div>'
