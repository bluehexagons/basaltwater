"""Agent diagnostics and prompt task controls for the authenticated panel."""

from __future__ import annotations

import copy
import html
import json
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from common.service_tools.web_panel_diagnostics import _redact_log_message
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_templates import render_heading
from common.service_tools.web_panel_agent_hub import agent_document
from lib.agent_cli import _tool_path, inspect_agent_tools, inspect_t3code
from lib.agent_maintenance import inspect_agent_maintenance
from lib.agent_readiness import load_agent_readiness_record
from lib.agent_task_api import worker_status
from lib.agent_task_context import validate_follow_up
from lib.agent_task_followups import follow_up_requires_review, follow_up_state, prepare_follow_up
from lib.agent_task_policy import task_policy_issues
from lib.agent_task_templates import ACTIONABLE_RESULT_PROMPT, HOST_REVIEW_PROMPT, HOST_REVIEW_SETTINGS, NETWORK_REVIEW_PROMPT, STORAGE_REVIEW_PROMPT, UPDATE_REVIEW_PROMPT
from lib.agent_tasks import (
    AgentTasks, DEFAULT_FAILURE_LIMIT, DEFAULT_TIMEOUT_MINUTES, EFFORTS, MAX_FAILURE_LIMIT,
    MAX_PROMPT_BYTES, MAX_REPEAT_MINUTES, MAX_TIMEOUT_MINUTES, WEB_SEARCH_MODES, codex_models, repeat_seconds, validate_task,
)


_REPOSITORY_EDIT_PREFLIGHT = (
    "Read this repository's agent instructions. Verify that the selected directory belongs to the intended Git checkout and inspect Git status, including untracked files. "
    "In an existing or shared checkout, if there are existing changes, stop and report that they need review before another editing run. "
    "In a Basaltwater-managed persistent task worktree, inspect unfinished changes against the saved task checkpoint and continue only this task's authorized work; report unrelated changes. "
    "Do not stash, reset, clean, commit, or switch branches to clear existing changes. "
)


PROMPT_TEMPLATES = {
    "maintenance": {
        "scope": "host", "description": "Inspect resources, services, update timers, and agent readiness.",
        "title": "Host maintenance review", **HOST_REVIEW_SETTINGS, "interval": "once",
        "prompt": HOST_REVIEW_PROMPT + "Inspect this host's resource usage, failed services, update timers, reboot state, and agent readiness using available Basaltwater diagnostics. Check T3 Code only if installed. Summarize issues, evidence, and recommended repairs. Do not change the host or expose credentials.",
    },
    "updates": {
        "scope": "host", "title": "Check Debian update health",
        "description": "Check package consistency, held updates, index age, and reboot evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + UPDATE_REVIEW_PROMPT,
    },
    "network": {
        "scope": "host", "title": "Check network and clock readiness",
        "description": "Inspect routes, resolver and clock state, and configured endpoint reachability.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + NETWORK_REVIEW_PROMPT,
    },
    "repository": {
        "scope": "repository", "description": "Inspect repository state and identify maintenance work.",
        "title": "Repository health check", "mode": "inspect", "interval": "once", "network": False,
        "prompt": "Read this repository's agent instructions and inspect Git status, dependency manifests, CI configuration, and recent test evidence. Identify failing checks, stale dependencies, and maintenance gaps. Report findings with file references and suggested next steps. Do not modify files or publish changes.",
    },
    "ci-repair": {
        "scope": "repository", "title": "Repair failing checks",
        "description": "Reproduce a test, lint, or build failure and prepare a focused fix.",
        "mode": "workspace", "interval": "once", "network": True, "temporary_files": True, "timeout_minutes": 60,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Identify failing tests, lint checks, or builds from available local evidence. Reproduce the failure using the existing tooling, then make the smallest justified fix and rerun relevant checks. Do not weaken checks or remove tests to make them pass. If a failure cannot be reproduced or tooling is unavailable, report the missing evidence rather than guessing. Summarize the cause, changes, and validation. Do not commit, push, publish, or deploy.",
    },
    "dependencies": {
        "scope": "repository", "description": "Prepare dependency changes and validation for review.",
        "title": "Update repository dependencies", "mode": "workspace", "interval": "once", "network": True, "temporary_files": True,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Update dependencies using the repository's existing package manager, lockfiles, and update policy. Prefer compatible updates within its declared constraints; report major upgrades needing separate migration work. Run the relevant checks, explain changes and compatibility concerns, and leave the result ready for review. Do not commit, push, publish, or deploy.",
    },
    "documentation": {
        "scope": "repository", "title": "Update repository documentation",
        "description": "Reconcile setup instructions, examples, and references with the code.",
        "mode": "workspace", "interval": "once", "network": False, "temporary_files": True, "timeout_minutes": 30,
        "prompt": _REPOSITORY_EDIT_PREFLIGHT + "Compare its README, setup instructions, CLI or API examples, and configuration references with the current implementation. Correct concrete discrepancies and broken local references using the established documentation format. Preserve accurate content, avoid marketing copy, and do not invent capabilities or unverified results. Run existing documentation checks when available, and report changes and any examples that could not be verified. Do not change application behavior, commit, push, publish, or deploy.",
    },
    "security-review": {
        "scope": "repository", "title": "Review repository security",
        "description": "Review sensitive code paths and dependency advisories with evidence.",
        "mode": "inspect", "interval": "once", "network": False, "web_search": "live", "timeout_minutes": 30,
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
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Identify this host's configured backup jobs using available Basaltwater diagnostics, timer state, and readable job metadata or logs. Check the last successful backup, recent failures, next scheduled run, destination capacity when available, retention metadata, and any existing integrity-check or restore-test evidence. If a backup is running, report its state. Distinguish successful job completion from verified recoverability; report missing configuration or inaccessible evidence explicitly. Summarize issues and recommended follow-up. Do not start backups, restore data, change retention, delete files, or expose backup contents or credentials.",
    },
    "storage": {
        "scope": "host", "title": "Review storage cleanup",
        "description": "Select a safe cleanup, request approval to apply it, and verify reclaimed space.",
        "mode": "inspect", "interval": "once", "network": False, "timeout_minutes": 15,
        "prompt": STORAGE_REVIEW_PROMPT,
    },
    "incident": {
        "scope": "host", "title": "Triage a service incident",
        "description": "Correlate failed units, recent errors, and resource pressure.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 20,
        "prompt": HOST_REVIEW_PROMPT + "Use available Basaltwater diagnostics and readable service-manager state or recent logs to identify unhealthy services on this host. Check resource pressure, unit state, recent failures, dependency failures, and relevant configuration evidence. Check T3 Code only if installed. Correlate timestamps and distinguish symptoms from likely causes. If no failure is evident, report that and the checks performed. Summarize affected services, evidence, likely causes, and recommended next diagnostic or repair steps. Do not restart services, change configuration, install packages, or expose credentials.",
    },
    "certificates": {
        "scope": "host", "title": "Check certificate expiry",
        "description": "Inspect certificate lifetimes, renewal jobs, and configuration gaps.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 10,
        "prompt": HOST_REVIEW_PROMPT + "Inspect public certificates served by explicitly configured HTTPS endpoints with bounded TLS probes, readable public certificate metadata, and renewal timer or job state. Do not assume access to root-owned certificate directories. Report expiry dates and remaining lifetimes, highlighting certificates expired or expiring within 30 days. Check recent renewal failures where evidence is readable. Distinguish local certificate metadata from a verified live endpoint; report inaccessible or missing evidence. Recommend follow-up for renewal or configuration gaps. Do not read private keys, issue or replace certificates, change trust stores, reload services, or expose credentials.",
    },
    "t3-readiness": {
        "requires": "t3", "scope": "host", "title": "Check T3 Code readiness",
        "description": "Check the user service, runtime, agent tools, and saved readiness evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured or installed T3 Code runtime using available Basaltwater agent diagnostics without repair options. Check user-service state, runtime and installed version evidence, terminal agent availability, local credential metadata, Git identity readiness, and maintenance holds. Distinguish file presence from verified authentication. Report failures and missing evidence with suggested next steps. Do not update tools, restart services, rotate credentials, read or reveal pairing passwords or credential contents, or change the host.",
    },
    "containers": {
        "requires": "containers", "scope": "host", "title": "Review container health",
        "description": "Inspect Docker or Podman health, resource use, and storage pressure.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Use the installed Docker or Podman CLI for bounded read-only inspection of local containers, health status, restart counts, resource usage, and image or volume storage metadata. Check available recent error evidence without dumping environment variables or secrets. Command presence does not prove daemon access; report unavailable sockets, permissions, or health checks explicitly. Identify failing containers and cleanup candidates with evidence. Do not start, stop, restart, exec into, pull, build, prune, remove, or change containers, images, volumes, or networks.",
    },
    "web-hosting": {
        "requires": "web_hosting", "scope": "host", "title": "Review web hosting",
        "description": "Check gateway registrations, Nginx state, and local upstream evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect basaltwater-web forward list --json and preview list --json, readable Nginx metadata, service state and bounded error evidence. Cover ordinary forwards as well as managed previews; old preview names may be ordinary forwards. Identify missing roots, failed upstreams and certificate/route gaps. Check ownership and active use before proposing exact repairs or retirement; age or one failed probe does not prove abandonment. Use the appropriate preview stop or forward remove command in an approved follow-up, with readiness rechecks and verification. Route removal alone frees no project storage; handle retained project files separately. Distinguish saved configuration, TCP readiness and verified HTTP/public health. Report denied evidence. Do not publish, modify routes/configuration, reload services, renew certificates or expose private data in this review.",
    },
    "shared-storage": {
        "requires": "shared_storage", "scope": "host", "title": "Check shared storage",
        "description": "Check configured SMB shares and existing CIFS or SSHFS mounts.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect configured Samba shares and existing CIFS or SSHFS mount metadata using available Basaltwater diagnostics and readable local configuration. Check local service state, mount presence, ownership, relevant errors, and existing backup coverage. Keep checks bounded: avoid traversing unavailable network mounts, reading shared file contents, or printing credentials and mount passwords. An installed client does not prove a share is configured or reachable; report missing evidence. Do not mount, unmount, reconnect, chmod, change share configuration, delete files, or copy data.",
    },
    "remote-desktop": {
        "requires": "remote_desktop", "scope": "host", "title": "Check remote desktop readiness",
        "description": "Inspect xrdp, session-manager state, and recent connection failures.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 10,
        "prompt": HOST_REVIEW_PROMPT + "Inspect configured remote desktop access, xrdp and session-manager service state, readable session configuration, and bounded recent connection or startup errors. Report missing desktop components, service failures, and relevant permission or certificate evidence. Local readiness does not prove a remote client can connect; distinguish those checks. Do not open or capture another user's desktop, terminate sessions, restart services, modify accounts or configuration, or expose credentials.",
    },
    "github-actions": {
        "requires": "github", "scope": "repository", "title": "Review GitHub Actions failures",
        "description": "Read recent CI runs with GitHub CLI and identify recurring failures.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 20,
        "prompt": "The execution directory is empty scratch space. Read repository instructions and Git status from the source directory named in the task context, using git -C SOURCE. Identify its GitHub repository from the existing remote without printing embedded credentials or changing it. Use gh --repo OWNER/REPO and read-only subcommands to inspect a bounded number of recent completed Actions runs and relevant failed-job logs. Report running checks as pending; do not watch or repeatedly poll them. Compare failures with local workflow configuration; separate likely code, infrastructure, flaky-test, and permission causes. Redact credentials and report unavailable authentication or repository access explicitly. Produce an evidence-backed triage report. Do not modify files, rerun or cancel workflows, create issues or comments, commit, push, publish, or deploy.",
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
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured HomeBox service using available Basaltwater diagnostics, readable service metadata, and bounded recent error logs. Identify inventory database and attachment storage paths from configuration without reading their contents. Check available capacity and existing backup-job evidence covering both database and attachments; distinguish job completion from restore verification. Report missing coverage, service failures, and inaccessible evidence. Do not change inventory, query private records, start backups, restore data, copy database files, restart services, or expose credentials.",
    },
    "gogs-health": {
        "requires": "gogs", "scope": "host", "title": "Check Gogs repository hosting",
        "description": "Review service errors, repository storage, and existing backup evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured or installed Gogs service using readable local service and configuration metadata and bounded recent error logs. Identify repository, database, and attachment storage locations without reading private repository or database contents. Check capacity, relevant ownership evidence, and existing backup coverage. Distinguish configured locations from verified availability and report inaccessible evidence. Recommend follow-up for failures or protection gaps. Do not change users or repositories, run repository hooks, migrate data, restart services, start backups, or expose credentials.",
    },
    "antistatic-health": {
        "requires": "antistatic", "scope": "host", "title": "Check Antistatic services",
        "description": "Review configured lobby and database service state and recent errors.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured Antistatic lobby and database services using available Basaltwater diagnostics, readable service metadata, and bounded recent error logs. Check service state, dependency failures, resource pressure, database storage capacity, and existing backup-job evidence where available. Distinguish configuration from verified application readiness and report unavailable checks. Do not query player records, send game requests, modify databases, restart services, start backups, or expose credentials.",
    },
    "notifications-health": {
        "requires": "notifications", "scope": "host", "title": "Check notification delivery",
        "description": "Inspect receiver configuration and existing delivery evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 10,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured Basaltwater panel notification receiver using available diagnostics and readable non-secret configuration or existing delivery logs. Check panel and proxy service state, accepted-event timestamps, sender failure or retry evidence, and relevant throttling or authentication errors. No recent events alone does not prove delivery is broken; report expected cadence only when configured evidence supports it. Redact sender links and bearer tokens. Do not reveal or rotate tokens, send test notifications or messages, change sender configuration, or restart services.",
    },
    "privilege-health": {
        "requires": "privilege", "scope": "host", "title": "Check privilege approval readiness",
        "description": "Review broker service, policy metadata, and available audit evidence.",
        **HOST_REVIEW_SETTINGS, "interval": "once", "timeout_minutes": 15,
        "prompt": HOST_REVIEW_PROMPT + "Inspect the configured Basaltwater privilege approval broker using available diagnostics, readable service and policy metadata, and sanitized audit evidence. Check broker service state, socket or endpoint configuration, policy coverage, and recent denial or execution errors. Report inaccessible evidence and distinguish configured permissions from verified readiness. Do not read approval credentials, submit or approve requests, execute privileged commands, change policies or allowlists, restart services, or expose secrets.",
    },
}

PROMPT_TEMPLATES["daily-maintenance"] = {
    **PROMPT_TEMPLATES["maintenance"], "title": "Daily maintenance checks", "interval": "daily",
    "description": "Schedule a read-only host review every day; no automatic repairs.",
}
for _template in PROMPT_TEMPLATES.values():
    _template["prompt"] += " " + ACTIONABLE_RESULT_PROMPT
del _template

_STYLE = """
.agent-columns { display: grid; gap: 16px; max-width: 960px; margin: 18px 0; }
.agent-panel { padding: 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
.agent-panel h2 { margin-bottom: 12px; }
.agent-form { display: grid; gap: 12px; }
.agent-form label { display: block; font-size: .85rem; font-weight: 650; }
.agent-form input:not([type=checkbox]), .agent-form textarea { display: block; width: 100%; min-height: 44px;
  margin-top: 5px; padding: 9px 10px; background: var(--bg); color: var(--text); border: 1px solid var(--line); border-radius: 8px; font: inherit; }
.agent-form textarea { resize: vertical; min-height: 160px; font-size: .85rem; }
.agent-form textarea[rows="2"] { min-height: 80px; }
.agent-form input:not([type=checkbox]), .agent-form textarea, .agent-form select { font-weight: 400; }
.agent-form textarea:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.agent-form select { background: var(--bg); width: 100%; }
.agent-form details[open] { display: grid; gap: 12px; }
.agent-form fieldset { min-width: 0; margin: 0; padding: 16px; border: 1px solid var(--line); border-radius: 10px; display: grid; gap: 12px; }
.agent-form legend { font-weight: 700; padding: 0 6px; }
.agent-form > details { border-top: 1px solid var(--accent-soft); padding-top: 4px; margin: 0; }
.agent-form > details > summary { width: 100%; font-weight: 650; }
.agent-form > details > summary small { font-weight: 400; color: var(--muted); }
.agent-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.agent-check { display: flex !important; gap: 9px; align-items: start; font-weight: 400 !important; }
.agent-check input { margin-top: 4px; width: 18px; height: 18px; flex: none; accent-color: var(--accent); }
.agent-help { color: var(--muted); font-size: .78rem; margin: 0; }
.agent-panel > .agent-help + .agent-help { margin-top: 10px; }
.agent-panel > .agent-help { margin-bottom: 12px; }
.agent-buttons { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.agent-buttons form { margin: 0; }
.agent-buttons button { min-height: 44px; font-size: .8rem; }
.agent-buttons button:disabled { cursor: not-allowed; }
.agent-buttons a { color: var(--accent); min-height: 44px; display: inline-flex; align-items: center; }
.agent-secondary { background: var(--accent-soft); color: var(--accent); }
.agent-primary { background: var(--accent); color: var(--panel) !important; text-decoration: none; }
.agent-primary:hover, header .agent-primary:hover { background: var(--text); }
.agent-task-list { display: grid; gap: 12px; list-style: none; padding: 0; margin: 12px 0; }
.agent-task { padding: 18px 20px; border: 1px solid var(--line); border-left: 4px solid var(--workspace); border-radius: 10px; background: var(--panel); }
.agent-task-head { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
.agent-task h3 { font-size: 1rem; margin: 0; }
.agent-task p { margin: 6px 0; }
.agent-task small { display: block; color: var(--muted); margin-top: 4px; }
.agent-task-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 16px 0; font-size: .85rem; }
.agent-task-facts dt { color: var(--muted); margin-bottom: 4px; }
.agent-task-facts dd { margin: 0; }
.agent-task > details { border-top: 1px solid var(--accent-soft); }
.agent-task > details > summary { font-size: .85rem; }
.agent-task-attention { border-left-color: var(--warning); }
.agent-task-active { border-left-color: var(--accent); }
.agent-helper { padding: 12px 18px; }
.agent-helper > details { margin: 0; }
.agent-helper > details > summary { padding: 0; display: list-item; }
.agent-start { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.agent-start p { flex: 1 1 260px; margin: 0; }
.agent-template { display: block; padding: 12px 0; border-top: 1px solid var(--accent-soft); text-decoration: none; color: var(--accent); }
.agent-template span { display: block; color: var(--muted); font-size: .8rem; margin-top: 4px; }
.agent-template small { display: block; color: var(--muted); font-size: .75rem; margin-top: 5px; }
.agent-template[aria-current=true] strong { text-decoration: underline; text-underline-offset: 3px; }
.agent-template-group { margin-top: 12px; }
.agent-template-group[open] { padding: 0 16px 8px; border: 1px solid var(--line); border-radius: 10px; }
.agent-template-group > summary { width: 100%; font-weight: 700; }
.agent-runtime { margin: 14px 0 0; display: grid; gap: 8px; }
.agent-runtime div { display: flex; justify-content: space-between; gap: 12px; }
.agent-runtime dt { color: var(--muted); flex: none; }
.agent-runtime dd { margin: 0; text-align: right; }
.agent-run summary { width: 100%; }
.agent-run .event-head { align-items: center; }
.agent-run p { margin: 4px 0; }
.agent-result-preview { display: block; color: var(--text); font-size: .85rem; line-height: 1.5; margin-top: 10px; overflow-wrap: anywhere; }
.agent-follow-up { border-left: 4px solid var(--warning); margin: 14px 0; }
.agent-completed { margin-top: 20px; }
.agent-summary { margin: 18px 0; }
.agent-history { display: grid; gap: 10px; }
.agent-history .agent-run { margin: 0; }
.agent-active { margin-bottom: 18px; border-left: 3px solid var(--accent); }
.agent-active p { margin: 6px 0; }
.agent-run:target { border-color: var(--accent); scroll-margin-top: 24px; }
@media (max-width: 700px) { .agent-task-facts { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 560px) {
  .agent-fields { grid-template-columns: minmax(0, 1fr); }
  .agent-buttons button { width: auto; }
  .agent-task-head { flex-direction: column; gap: 8px; }
  .agent-panel, .agent-task { padding: 14px; }
  .agent-form fieldset { padding: 12px; }
}
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


def _other_schedules(tasks: list[dict[str, Any]]) -> str:
    once = sum(task["enabled"] and task["interval"] == "once" and not repeat_seconds(task) for task in tasks)
    events = sum(task["enabled"] and task["interval"] == "event" for task in tasks)
    return f" · {once} one-time · {events} event" if once or events else ""


def _render_run_summary(tasks: list[dict[str, Any]], runs: list[dict[str, Any]], pending: dict[str, list[dict[str, Any]]]) -> str:
    finished = [run for run in runs if run["status"] != "running"]
    completed = sum(run["status"] == "completed" and run.get("outcome") not in {"blocked", "failed"} for run in finished)
    failed = sum(run["status"] in {"failed", "interrupted"} or run.get("outcome") in {"blocked", "failed"} for run in finished)
    cancelled = sum(run["status"] == "cancelled" for run in finished)
    cards = (
        ("Work queue", f'{sum(run["status"] == "running" for run in runs)} running', f'{sum(task["queued"] for task in tasks)} queued · executes one at a time'),
        ("Schedules", f'{sum(task["enabled"] and bool(repeat_seconds(task)) for task in tasks)} repeating', f'{sum(task.get("draft", False) for task in tasks)} drafts · {sum(task.get("auto_paused", False) for task in tasks)} paused after failures' + _other_schedules(tasks)),
        ("Recent outcomes", f"{completed} completed", f"{failed} failed/interrupted · {cancelled} cancelled · latest {len(finished)} finished runs"),
        ("Needs review", str(sum(_task_needs_review(task, runs, pending) for task in tasks)), "Pending follow-up, auto-paused or latest result failed, blocked or interrupted"),
    )
    return '<dl class="overview-grid agent-summary" aria-label="Agent workflow summary">' + "".join(
        f'<div class="metric"><dt>{label}</dt><dd><span class="metric-value">{_escape(value)}</span><span class="metric-description">{_escape(note)}</span></dd></div>'
        for label, value, note in cards
    ) + '</dl>'


def render_agent_activity(state: Any) -> str:
    """Summarize saved panel activity without invoking tools or diagnostics."""

    heading = f'''<section aria-labelledby="agent-activity-heading"><div class="section-heading">
{render_heading("Agent activity", "agents", heading_id="agent-activity-heading")}<a class="refresh-link" href="/agents">Manage prompt tasks →</a></div>'''
    manager = state.agent_tasks
    try:
        snapshot = manager.snapshot()
    except (OSError, RuntimeError, ValueError):
        return heading + '<p class="empty">Agent activity is unavailable. Open Agents to inspect account and task-storage readiness.</p></section>'
    tasks, runs = snapshot["tasks"], snapshot["runs"]
    now = time.time()
    active = next((run for run in runs if run["status"] == "running"), None)
    latest = next((run for run in reversed(runs) if run["status"] != "running"), None)
    schedules = [task for task in tasks if task["enabled"]]
    timed = [task for task in schedules if task["next_run"] is not None]
    upcoming = min(timed, key=lambda task: task["next_run"]) if timed else None
    paused = sum(task.get("auto_paused", False) for task in tasks)
    last_by_task = {run["task"]["id"]: run for run in runs}
    failed = sum(last_by_task.get(task["id"], {}).get("status") in {"failed", "interrupted"}
                 or last_by_task.get(task["id"], {}).get("outcome") in {"blocked", "failed"} for task in tasks)
    drafts = sum(task.get("draft", False) for task in tasks)
    draft_label = f'{drafts} draft' + ('' if drafts == 1 else 's')
    saved_label = f'{len(tasks)} saved task' + ('' if len(tasks) == 1 else 's')
    failed_label = f'{failed} saved task' + ('' if failed == 1 else 's')
    cards = (
        ("Work queue", "1 running" if active else "Idle", f'{sum(task["queued"] for task in tasks)} queued · one run at a time', ""),
        ("Schedules", f'{sum(bool(repeat_seconds(task)) for task in schedules)} repeating', f'{draft_label} · {saved_label}' + _other_schedules(tasks), ""),
        ("Needs review", f'{paused} auto-paused', f'{failed_label} last failed or interrupted · retained history', "warning" if paused or failed else ""),
        ("Latest finished run", ((latest.get("outcome") if latest["status"] == "completed" else None) or latest["status"]).capitalize() if latest else "Not run", latest["task"]["title"] if latest else "No retained finished runs", "failed" if latest and (latest["status"] in {"failed", "interrupted"} or latest.get("outcome") in {"blocked", "failed"}) else ""),
    )
    metrics = '<dl class="overview-grid">' + "".join(
        f'<div class="metric"><dt>{label}</dt><dd><span class="metric-value {tone}">{_escape(value)}</span><span class="metric-description">{_escape(note)}</span></dd></div>'
        for label, value, note, tone in cards
    ) + '</dl>'
    notes = []
    if manager.error:
        notes.append('<p class="endpoint"><strong>Scheduler needs attention.</strong> Open Agents for details; saved counts do not prove it is executing work.</p>')
    if active:
        elapsed = _run_seconds(active, now)
        remaining = max(0, active["task"]["timeout_minutes"] * 60 - elapsed)
        notes.append(f'<p class="endpoint"><a class="refresh-link" href="/agents?run={active["id"]}#run-{active["id"]}">Running: {_escape(active["task"]["title"])}</a> · {_duration(elapsed)} elapsed · {_duration(remaining)} remaining at page load</p>')
    if upcoming:
        due = "Overdue; waiting for scheduler" if upcoming["next_run"] <= now else "Next scheduled"
        notes.append(f'<p class="endpoint">{due}: {_escape(upcoming["title"])} · {_time(upcoming["next_run"])}</p>')
    if latest:
        notes.append(f'<p class="endpoint"><a class="refresh-link" href="/agents?run={latest["id"]}#run-{latest["id"]}">Review latest run</a> · {_time(latest["finished_at"])} · {_duration(_run_seconds(latest, now))} duration</p>')
    notes.append('<a class="refresh-link" href="/agent-tools">Prepare a system checkup or data task →</a>')
    return heading + metrics + '<div class="agent-activity-notes">' + "".join(notes) + '</div></section>'


class AgentDiagnostics:
    """Run bounded existing checks on request, without blocking form submission."""

    def __init__(self, home: str, *, features: Callable[[], PanelFeatures]) -> None:
        self.home = home
        self._features = features
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
            features = self._features()
            tools = inspect_agent_tools([name for name in ("codex", "claude", "opencode", "gh")
                                         if name in features.tools], home=self.home)
            t3 = inspect_t3code(home=self.home, fix=False) if features.t3 else None
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
    if set(query) == {"view"} and query["view"][0] in {"new", "templates", "dashboard", "tasks", "history", "diagnostics"}:
        return {"view": query["view"][0]}
    if set(query) == {"template"} and query["template"][0] in PROMPT_TEMPLATES:
        return {"template": query["template"][0]}
    for name in ("receipt", "follow-up"):
        if set(query) == {name}:
            parts = query[name][0].split(".")
            if len(parts) == 2 and all(len(part) == 32 and all(c in "0123456789abcdef" for c in part) for part in parts):
                return {name: query[name][0]}
    for name in ("edit", "copy", "reuse", "run", "context"):
        if set(query) == {name} and len(query[name][0]) == 32 and all(c in "0123456789abcdef" for c in query[name][0]):
            return {name: query[name][0]}
    raise ValueError("Invalid agent view")


def _action_form(csrf: str, identifier: str, action: str, label: str) -> str:
    return f'''<form method="post" action="/actions/agent-task"><input type="hidden" name="csrf" value="{_escape(csrf)}">
<input type="hidden" name="id" value="{_escape(identifier)}"><button class="agent-secondary" name="action" value="{action}">{label}</button></form>'''


def _task_needs_review(task: dict[str, Any], runs: list[dict[str, Any]], pending: dict[str, list[dict[str, Any]]]) -> bool:
    latest = next((run for run in reversed(runs) if run["task"]["id"] == task["id"]), None)
    return bool(task.get("auto_paused") or pending.get(task["id"]) or latest and (
        latest["status"] in {"failed", "interrupted"} or latest.get("outcome") in {"failed", "blocked"}))


def _render_inspection_access(task: dict[str, Any], *, blocked: bool = False) -> str:
    if task.get("mode") != "inspect":
        return ""
    from common.service_tools.web_panel_agent_tools import TOOLS

    template = next((key for key, value in PROMPT_TEMPLATES.items()
                     if value["scope"] == "host" and value.get("workspace") == "run-directory"
                     and value["title"] == task.get("title")), None)
    tool = next((key for key, (title, _, group, mode, _, _) in TOOLS.items()
                 if group == "System" and mode == "workspace" and title == task.get("title")), None)
    if template is None and tool is None and not blocked:
        return ""
    target, label = "/agents?view=templates", "Review task templates"
    if template:
        target, label = "/agents?template=" + template, "Review current host-check template"
    elif tool:
        target, label = "/agent-tools?tool=" + tool, "Review current guided task"
    return f'''<aside class="agent-panel"><strong>Inspection access is restricted</strong>
<p class="agent-help">Inspect only can block local service sockets and endpoint probes. Saved tasks and Reuse run settings keep their original access.</p>
<a class="refresh-link" href="{target}">{label}</a>
<p class="agent-help">Live host-check templates use command network access and a private run directory, with prompts that prohibit host changes. Review and submit a new task, then pause the old schedule if replacing it. Opening a template does not change or queue saved work.</p></aside>'''


def _available_templates(manifest: dict[str, Any], home: str, selected: PanelFeatures) -> dict[str, dict[str, Any]]:
    """Use saved configuration and executable presence, without executing tools."""

    def labels(section: str) -> set[str]:
        return {row["label"] for row in manifest.get(section, [])
                if isinstance(row, dict) and isinstance(row.get("label"), str)}

    def installed(*names: str) -> bool:
        return any(_tool_path(name, home) for name in names)

    services, access = labels("services"), labels("access")
    features = manifest.get("features", {})
    capabilities = {
        "t3": "T3 Code configured" if selected.t3 else "",
        "containers": "Container CLI detected" if installed("docker", "podman") else "",
        "web_hosting": "Web server configured" if "Web server" in services else
                       "Web hosting command detected" if installed("basaltwater-web", "nginx") else "",
        "shared_storage": "Samba configured" if "Samba / SMB" in access else
                          "Shared storage command detected" if installed("smbd", "mount.cifs", "sshfs") else "",
        "remote_desktop": "Remote desktop configured" if "Remote desktop" in access else
                          "xrdp command detected" if installed("xrdp") else "",
        "github": "GitHub CLI detected" if "gh" in selected.tools else "",
        "browser": "Managed browser launcher detected" if installed("basaltwater-playwright-mcp") else "",
        "godot": "Godot command detected" if installed("godot", "godot4") else "",
        "homebox": "HomeBox configured" if "HomeBox" in services else "",
        "gogs": "Gogs configured" if "Gogs" in services else
                "Gogs command detected" if installed("gogs") else "",
        "antistatic": "Antistatic configured" if services & {"Antistatic lobby", "Antistatic DB"} else "",
        "notifications": "Notification ingest enabled" if features.get("notification_ingest") is True else "",
        "privilege": "Privilege approvals configured" if "Privilege approvals" in services else "",
    }
    available = {key: {**template, "availability": capabilities.get(template.get("requires", ""), "")}
                 for key, template in PROMPT_TEMPLATES.items()
                 if ("requires" not in template or capabilities[template["requires"]])
                 and (template["scope"] != "repository" or "git" in selected.tools)}
    for template in available.values():
        issues = task_policy_issues(template)
        if issues:
            template["availability"] = " ".join(filter(None, (
                template["availability"], "Policy conflict: " + " ".join(issues),
            )))
    return available


def _render_templates(selected: str, templates: dict[str, dict[str, Any]]) -> str:
    active_scope = templates.get(selected, {}).get("scope", "host")
    groups = []
    for scope, label in (("host", "Host checks and maintenance"), ("repository", "Repository work")):
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
            if template.get("workspace") == "run-directory":
                permissions.append("private run directory")
            if template.get("web_search") == "live":
                permissions.append("live web search")
            settings = " · ".join([mode, interval, f"{runtime} min cap", *permissions])
            availability = f'<small>{_escape(template["availability"])}</small>' if template.get("availability") else ""
            links.append(f'<a class="agent-template" href="/agents?template={_escape(key)}"{current}><strong>{_escape(template["title"])}</strong><span>{_escape(template["description"])}</span>{availability}<small>{_escape(settings)}</small></a>')
        if links:
            groups.append(f'<details class="agent-template-group"{" open" if scope == active_scope else ""}><summary>{label} · {len(links)} templates</summary>{"".join(links)}</details>')
    return "".join(groups)


def render_agents(state: Any, style: str, query: dict[str, str], *, error: str = "", notice: str = "", submitted: dict[str, Any] | None = None, prepared: bool = False) -> str:
    """Render forms, diagnostics, schedules, and history without running agents."""

    features = state.features()
    if query.get("view") == "diagnostics" or not features.prompts and query:
        return agent_document(state, style + _STYLE, title="Agent readiness", current="diagnostics",
            description="Inspect installed runtimes, sign-in readiness, and maintenance holds.",
            content=_render_diagnostics(state.agent_diagnostics.snapshot(), state.csrf_token) + _render_t3_update(state))
    manager: AgentTasks = state.agent_tasks
    snapshot = {"tasks": [], "runs": []}
    storage_error = False
    try:
        snapshot = manager.snapshot()
    except (OSError, RuntimeError, ValueError) as exc:
        storage_error = True
        error = error or str(exc)
    tasks, runs = snapshot["tasks"], snapshot["runs"]
    pending_by_task = {}
    for task in tasks:
        try:
            pending_by_task[task["id"]] = manager.contexts.pending_follow_ups(task["id"])
        except (OSError, ValueError, RuntimeError):
            # Keep recent evidence usable if an older receipt is unavailable.
            pending_by_task[task["id"]] = [run for run in runs if run["task"]["id"] == task["id"]
                and run.get("follow_up") and _follow_up_status(manager, run)["status"] == "pending"]
    if (not query or query.get("view") == "dashboard") and submitted is None and not prepared:
        return _render_dashboard(state, style, tasks, runs, pending_by_task, error=error)
    history_view = query.get("view") == "history" or bool(set(query) & {"run", "receipt", "context"})
    if "run" in query and not any(run["id"] == query["run"] for run in runs):
        error = error or "This run is no longer in retained history. Open a recent run below."
    now = time.time()
    account_ready = manager.available()
    ready = account_ready and not manager.error and not storage_error
    codex_installed = bool(_tool_path("codex", manager.home))
    templates = _available_templates(state.manifest, manager.home, features)
    defaults: dict[str, Any] = {"title": "", "prompt": "", "directory": manager.home,
                               "mode": "inspect", "interval": "once", "model": "", "network": False, "id": "",
                               "effort": "", "timeout_minutes": DEFAULT_TIMEOUT_MINUTES,
                               "web_search": "disabled", "session_history": False, "custom_model": "", "temporary_files": False,
                               "failure_limit": DEFAULT_FAILURE_LIMIT, "repeat_minutes": 60,
                               "persistence": "fresh", "workspace": "existing", "continue_until_done": False,
                               "completion_criteria": "", "continuation_minutes": 5, "max_runs": 0,
                               "deadline_at": 0, "start_at": 0, "token_budget": 0, "daily_limit": 0,
                               "backoff_minutes": 0, "history_limit": 100}
    defaults.update(writable_directories=[], operator_requirements=[], service_restarts=[],
                    source_task_id="", source_run_id="", keep=False)
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
            defaults["source_task_id"] = defaults["source_run_id"] = ""
            if "reuse" in query:
                defaults["interval"] = "once"
                defaults["continue_until_done"] = False
                defaults["start_at"] = 0
    if "follow-up" in query:
        try:
            identifier, run_id = query["follow-up"].split(".")
            receipt = manager.contexts.receipt(identifier, run_id)
            proposal = validate_follow_up(receipt.get("follow_up"))
            if proposal is None:
                raise ValueError("This run did not request a follow-up")
            defaults.update(prepare_follow_up(manager, identifier, run_id, review=True))
            defaults["id"] = ""
            source_note = ("Requested by a previous run: " + _redact_log_message(proposal["reason"])
                           + " Review the evidence, actions, directory, and requested access before submitting. "
                           "No task has been saved or queued. Privileged actions still require the configured Admin approval workflow.")
            if follow_up_requires_review(proposal):
                source_note += (" This older proposal did not record all access prerequisites. "
                                "Check its write targets and operator prerequisites below before approving. "
                                "Any service action still needs separate authorization.")
        except (OSError, ValueError, RuntimeError) as exc:
            error = error or f"Follow-up request unavailable: {exc}"
    if submitted:
        defaults.update(submitted)
    if prepared:
        source_note = "Prepared from Agent tools. No task has been saved or queued. Review the prompt, model, runtime, permissions, and repetition before submitting."
    editing = bool(defaults["id"])
    composing = prepared or submitted is not None or bool(set(query) & {"template", "edit", "copy", "reuse", "follow-up"}) or query.get("view") == "new"
    alert_title = "Task storage unavailable" if storage_error else "Agent tools need attention"
    retry = ""
    if storage_error:
        retry = ('<p>Your settings are shown below. Copy your prompt and settings before reloading to retry storage.</p>'
                 if composing else '<p><a class="inline-link" href="/agents">Retry task storage</a></p>')
    alert = f'<aside class="status failed" role="alert"><strong>{alert_title}</strong><p>{_escape(error or manager.error)}</p>{retry}</aside>' if error or manager.error else ""
    if notice:
        alert += f'<aside class="status" role="status"><strong>Service approval</strong><p>{_escape(notice)}</p></aside>'
    if not account_ready:
        alert += '<p class="empty">Prompt execution requires a non-root panel account with its own home directory. Diagnostics remain available.</p>'
    elif not codex_installed:
        alert += '<p class="empty">Install and sign in to Codex as the panel account to run prompts. T3 Code is optional.</p>'
    policy_issues = task_policy_issues(defaults)
    if composing and policy_issues:
        notices = "".join(f"<li>{_escape(issue)}</li>" for issue in policy_issues)
        alert += ('<aside class="status failed" role="alert"><strong>Execution policy conflict</strong>'
                  f'<ul>{notices}</ul><p>These settings cannot launch under the local policy. '
                  'Adjust the task settings or save a draft for review. The runner preserves administrator restrictions.</p></aside>')
    disabled = " disabled" if not ready or not codex_installed else ""
    save_disabled = " disabled" if not ready else ""
    service_approval = ""
    approve_label = "Approve once"
    if defaults["source_task_id"] and defaults["service_restarts"] and not editing:
        try:
            decision = follow_up_state(manager, defaults["source_task_id"], defaults["source_run_id"])
        except (OSError, ValueError, RuntimeError):
            decision = {"requests": {}}
        service_approval = _render_service_requests(decision)
        if decision["requests"]:
            approve_label = "Check approval and launch"
            service_approval += '<p class="agent-help">Submitted service units cannot be removed here. Block the source proposal to cancel unclaimed requests before preparing a revised task.</p>'
        service_approval += ('<p class="agent-help">Approve once requests any missing service approvals. '
            'Open the approval link with your separate approval password, then return here and select '
            'Check approval and launch. Status is checked when you submit; this page does not poll.</p>'
            '<button class="agent-secondary" name="submit" value="privilege"' + save_disabled
            + '>Request or refresh service approval</button>')
    options = "".join(f'<option value="{key}"{" selected" if key == defaults["interval"] else ""}>{label}</option>' for key, label in (
        ("once", "Once"), ("event", "On external event"), ("hourly", "Every hour"), ("daily", "Every 24 hours"), ("weekly", "Every 7 days"), ("custom", "Custom interval")))
    modes = "".join(f'<option value="{key}"{" selected" if key == defaults["mode"] else ""}>{label}</option>' for key, label in (
        ("inspect", "Inspect only"), ("workspace", "Workspace changes")))
    models = [("", "Configured default")] + [(model["slug"], model["name"]) for model in codex_models(manager.home)]
    if defaults["model"] and defaults["model"] not in {key for key, _ in models}:
        models.append((defaults["model"], "Custom · " + str(defaults["model"])))
    model_options = "".join(f'<option value="{_escape(key)}"{" selected" if key == defaults["model"] else ""}>{_escape(label)}</option>' for key, label in models)
    effort_options = "".join(f'<option value="{key}"{" selected" if key == defaults["effort"] else ""}>{label}</option>' for key, label in [("", "Configured default")] + [(level, "Extra high (xhigh)" if level == "xhigh" else "Maximum (max)" if level == "max" else level.capitalize()) for level in EFFORTS])
    search_options = "".join(f'<option value="{key}"{" selected" if key == defaults["web_search"] else ""}>{label}</option>' for key, label in zip(WEB_SEARCH_MODES, ("Disabled", "Cached results", "Live web search")))
    context_options = "".join(f'<option value="{key}"{" selected" if key == defaults["persistence"] else ""}>{label}</option>' for key, label in (
        ("fresh", "Fresh conversation each run"), ("checkpoint", "Carry forward a checkpoint"), ("conversation", "Resume the same conversation")))
    workspace_options = "".join(f'<option value="{key}"{" selected" if key == defaults["workspace"] else ""}>{label}</option>' for key, label in (
        ("existing", "Use the selected directory"), ("directory", "Persistent task directory"),
        ("worktree", "Persistent Git worktree"), ("run-directory", "Empty directory for each run"))
        if key != "worktree" or "git" in features.tools)
    deadline = defaults["deadline_at"]
    if isinstance(deadline, (int, float)):
        deadline = datetime.fromtimestamp(deadline, timezone.utc).isoformat().replace("+00:00", "Z") if deadline else ""
    start = defaults["start_at"]
    if isinstance(start, (int, float)):
        start = datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z") if start else ""
    context_label = {"fresh": "Fresh conversation", "checkpoint": "Checkpoint", "conversation": "Same conversation"}.get(defaults["persistence"], defaults["persistence"])
    workspace_label = {"existing": "Selected directory", "directory": "Task directory", "worktree": "Git worktree", "run-directory": "New run directory"}.get(defaults["workspace"], defaults["workspace"])
    composer = f'''<div class="agent-panel">{render_heading("Edit prompt task" if editing else "Review prepared task" if prepared else "New prompt task", "agents")}
{f'<p class="agent-help">{_escape(source_note)}</p>' if source_note else ''}
{_render_inspection_access(defaults)}<form class="agent-form" method="post" action="/actions/agent-task/save">
<input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><input type="hidden" name="id" value="{_escape(defaults['id'])}">
{('<input type="hidden" name="source_task_id" value="' + _escape(defaults['source_task_id']) + '"><input type="hidden" name="source_run_id" value="' + _escape(defaults['source_run_id']) + '">') if defaults['source_task_id'] and not editing else ''}
<div class="agent-fields"><label>Task name<input name="title" value="{_escape(defaults['title'])}" maxlength="120" required placeholder="Weekly dependency update"></label>
<label>Working directory<input name="directory" value="{_escape(defaults['directory'])}" required placeholder="~/repos/project"></label></div>
<label>Prompt<textarea name="prompt" maxlength="{MAX_PROMPT_BYTES}" required placeholder="Describe the task and what a successful result should include.">{_escape(defaults['prompt'])}</textarea></label>
<fieldset><legend>Access and permissions</legend>
<label>Execution mode<select name="mode">{modes}</select></label>
<label class="agent-check"><input type="checkbox" name="network" value="1"{" checked" if defaults['network'] else ""}><span>Allow command network access for workspace changes, including downloading dependencies.</span></label>
<label class="agent-check"><input type="checkbox" name="temporary_files" value="1"{" checked" if defaults['temporary_files'] else ""}><span>Allow temporary file writes for workspace changes (/tmp and TMPDIR). Needed by many test and package tools.</span></label>
<details{' open' if defaults['writable_directories'] or defaults['operator_requirements'] or defaults['source_task_id'] else ''}><summary>Additional write access and prerequisites</summary>
<label>Additional writable directories<textarea name="writable_directories" rows="2" placeholder="/home/agent/.nvm">{_escape(chr(10).join(defaults['writable_directories']))}</textarea></label>
<p class="agent-help">One existing account-owned directory per line. These grants accompany the execution directory; a private task directory does not grant writes to the source directory. Credential and task-control stores cannot be added.</p>
<label>Operator prerequisites<textarea name="operator_requirements" rows="2" placeholder="Actions needed before this task can run">{_escape(chr(10).join(defaults['operator_requirements']))}</textarea></label>
<p class="agent-help">{'Approve once confirms that the prerequisites listed here are satisfied and authorizes this task. Complete any outside preparation first; you do not need to remove resolved lines. Save draft keeps them unresolved. Service approvals remain separate.' if defaults['source_task_id'] and not editing else 'One prerequisite per line. Complete these outside the unattended task, then remove resolved lines. Tasks with unresolved prerequisites cannot launch.'}</p>
{('<label>Service restart prerequisites<textarea name="service_restarts" rows="2">' + _escape(chr(10).join(defaults['service_restarts'])) + '</textarea></label><p class="agent-help">These actions execute before the agent starts, including before saving a draft. If a repair must happen first, remove its restart here and edit the prompt to perform only that preparation and propose a separate restart verification afterward.</p>' + service_approval) if defaults['source_task_id'] and not editing else ''}
</details>
<p class="agent-help">Inspection uses Codex's read-only sandbox and can block local service sockets and endpoint probes. Workspace changes may edit the execution directory inside this account's home, plus temporary directories when allowed. Host service reviews use command network access and a private run directory; their prompts prohibit host changes. Provider requests still use the network in either mode. Configured hooks and MCP integrations retain their own permissions.</p></fieldset>
<div class="agent-fields"><label>Maximum runtime (minutes)<input type="number" name="timeout_minutes" min="1" max="{MAX_TIMEOUT_MINUTES}" step="1" value="{_escape(defaults['timeout_minutes'])}" required></label><label>Repeat<select name="interval">{options}</select></label></div>
<p class="agent-help">1 minute to 7 days, including time spent waiting. A duration cap is not an exact spending limit.</p>
<details{' open' if submitted is not None or defaults['model'] or defaults['effort'] or defaults['custom_model'] or defaults['web_search'] != 'disabled' else ''}><summary>Model and web search <small>· {_escape(defaults['custom_model'] or defaults['model'] or 'Configured model')} · effort {_escape(defaults['effort'] or 'default')} · search {_escape(defaults['web_search'])}</small></summary>
<div class="agent-fields"><label>Model<select name="model">{model_options}</select></label><label>Reasoning effort<select name="effort">{effort_options}</select></label></div>
<p class="agent-help">Model choices use Codex's local cache; availability and effort support depend on your account and model.</p>
<label>Custom model ID <span class="agent-help">(overrides the model selector when entered)</span><input name="custom_model" maxlength="100" value="{_escape(defaults['custom_model'])}" placeholder="Provider model ID"></label>
<label>Web search<select name="web_search">{search_options}</select></label>
<p class="agent-help">Controls Codex's web-search tool separately from shell-command network access.</p></details>
<details{' open' if submitted is not None or defaults['persistence'] != 'fresh' or defaults['workspace'] != 'existing' or defaults['session_history'] else ''}><summary>Context and workspace <small>· {_escape(context_label)} · {_escape(workspace_label)}</small></summary>
<div class="agent-fields"><label>Context persistence<select name="persistence">{context_options}</select></label><label>Workspace<select name="workspace">{workspace_options}</select></label></div>
<p class="agent-help">Checkpoints carry findings and next steps into a new conversation. Conversation persistence resumes the same Codex session. Managed workspaces require Workspace changes; a worktree starts from the source repository's committed HEAD and keeps this task's unfinished changes. Task directories start empty. Context and run evidence are stored separately.</p>
<label class="agent-check"><input type="checkbox" name="session_history" value="1"{" checked" if defaults['session_history'] else ""}><span>Keep fresh Codex session history on this account. Conversation persistence always keeps its session. Run evidence is retained either way.</span></label></details>
<details{' open' if submitted is not None or defaults['interval'] != 'once' or start or defaults['failure_limit'] != DEFAULT_FAILURE_LIMIT else ''}><summary>Scheduling options <small>· start time, custom interval and failure policy</small></summary>
<label>First run with timezone<input name="start_at" value="{_escape(start)}" placeholder="2026-11-01T09:00:00-06:00"></label>
<p class="agent-help">Optional start time for Create schedule. Once runs once at this time; repeating tasks use it to start their cadence. An overdue start produces one catch-up run. Start task starts immediately and consumes a one-time schedule. Event tasks wait for notifications and leave this blank.</p>
<label>Custom repeat interval (minutes)<input type="number" name="repeat_minutes" min="1" max="{MAX_REPEAT_MINUTES}" step="1" value="{_escape(defaults['repeat_minutes'] or 60)}" required></label>
<p class="agent-help">Applies when Repeat is Custom interval. 1 minute to 30 days; for example, 360 for every 6 hours or 20160 for every 2 weeks. Intervals missed during a run are skipped.</p>
<label>Pause schedule after failures<input type="number" name="failure_limit" min="0" max="{MAX_FAILURE_LIMIT}" step="1" value="{_escape(defaults['failure_limit'])}" required></label>
<p class="agent-help">Default 3 consecutive failed, blocked, or interrupted runs; 0 keeps repeating. Progress, verified completion, or Resume resets the count. Cancellation does not count as failure.</p></details>
<details{' open' if submitted is not None or defaults['continue_until_done'] or defaults['history_limit'] != 100 or any(defaults[key] for key in ('max_runs', 'deadline_at', 'token_budget', 'daily_limit', 'backoff_minutes')) else ''}><summary>Goal and usage limits</summary>
<label class="agent-check"><input type="checkbox" name="continue_until_done" value="1"{' checked' if defaults['continue_until_done'] else ''}><span>Continue until the completion criteria are met, then stop this task.</span></label>
<label>Completion criteria<textarea name="completion_criteria" maxlength="{MAX_PROMPT_BYTES}" placeholder="What evidence proves this goal is done?">{_escape(defaults['completion_criteria'])}</textarea></label>
<div class="agent-fields"><label>Goal continuation interval (minutes)<input type="number" name="continuation_minutes" min="1" max="{MAX_REPEAT_MINUTES}" value="{_escape(defaults['continuation_minutes'])}" required></label><label>Maximum run attempts<input type="number" name="max_runs" min="0" max="1000000" value="{_escape(defaults['max_runs'])}" required></label></div>
<label>Deadline with timezone<input name="deadline_at" value="{_escape(deadline)}" placeholder="2026-11-01T18:00:00Z"></label>
<div class="agent-fields"><label>Token budget<input type="number" name="token_budget" min="0" max="1000000000000" value="{_escape(defaults['token_budget'])}" required></label><label>Run attempts per UTC day<input type="number" name="daily_limit" min="0" max="1000" value="{_escape(defaults['daily_limit'])}" required></label></div>
<div class="agent-fields"><label>Minimum failure backoff (minutes)<input type="number" name="backoff_minutes" min="0" max="{MAX_REPEAT_MINUTES}" value="{_escape(defaults['backoff_minutes'])}" required></label><label>Retained evidence runs<input type="number" name="history_limit" min="1" max="1000" value="{_escape(defaults['history_limit'])}" required></label></div>
<p class="agent-help">A goal needs completion criteria and at least one total limit: attempts, deadline, or tokens. A Once goal uses its continuation interval; other goals use their repeat cadence. Zero disables an individual usage limit. Token usage is checked after a run and can exceed the budget during that run; runtime and deadline caps stop active processes. Attempts include failed and interrupted runs. Retention applies to generated receipts and logs, preserving workspace files.</p></details>
<label class="agent-check"><input type="checkbox" name="keep" value="1"{' checked' if defaults['keep'] else ''}><span>Keep this one-time task in saved tasks after it finishes.</span></label>
<div class="agent-buttons">{'<button name="submit" value="save"' + save_disabled + '>Save changes</button>' if editing else '<button name="submit" value="run"' + disabled + '>' + (approve_label if defaults['source_task_id'] else 'Start task') + '</button>' + ('' if defaults['source_task_id'] else '<button class="agent-secondary" name="submit" value="schedule"' + disabled + '>Create schedule</button>') + '<button class="agent-secondary" name="submit" value="draft"' + save_disabled + '>Save draft</button>'}
<a href="/agents?view=tasks">Back to tasks</a></div>
<p class="agent-help">Runs execute as {_escape(state.manifest['username'])}. Start task runs now and activates the selected repetition, event trigger, or goal continuation. Create schedule uses the first-run time or waits one interval. Save draft stays inactive. Saving changes preserves an existing schedule's active or paused state.</p></form></div>'''
    template_links = _render_templates(query.get("template", ""), templates)
    helper = f'''<aside class="agent-panel agent-helper"><details><summary>Start from a template</summary><p class="agent-help">Selecting a template replaces this form. Review its prompt, directory, permissions, and repetition before submitting.</p>{template_links}</details></aside>'''
    rows, completed_rows = [], []
    active_ids = {run["task"]["id"] for run in runs if run["status"] == "running"}
    ordered_tasks = sorted(tasks, key=lambda task: 0 if task["id"] in active_ids else 1 if task["queued"] else 2 if _task_needs_review(task, runs, pending_by_task) else 3)
    for task in ordered_tasks:
        latest = next((run for run in reversed(runs) if run["task"]["id"] == task["id"]), None)
        running = bool(latest and latest["status"] == "running")
        task_status = "Running" if running else "Queued" if task["queued"] else "Scheduled" if task["enabled"] else "Draft" if task.get("draft") else "Auto-paused" if task.get("auto_paused") else "Paused" if repeat_seconds(task) else "One-time"
        controls = _action_form(state.csrf_token, task["id"], "cancel", "Cancel run" if running else "Cancel queued run") if running or task["queued"] else _action_form(state.csrf_token, task["id"], "run", "Run once")
        if repeat_seconds(task) or task["interval"] == "event" or task["start_at"] > now or task["enabled"] and task["start_at"]:
            controls += _action_form(state.csrf_token, task["id"], "pause" if task["enabled"] else "resume", "Pause" if task["enabled"] else "Start schedule" if task.get("draft") else "Resume")
        if latest:
            controls += f'<a class="refresh-link" href="/agents?run={latest["id"]}#run-{latest["id"]}">{"View live run" if running else "View latest result"}</a>'
        more = f'<a href="/agents?copy={task["id"]}">Duplicate</a><a href="/agents?context={task["id"]}">Context and evidence</a>'
        if not running and not task["queued"]:
            controls += f'<a href="/agents?edit={task["id"]}">Edit</a>'
            more += _action_form(state.csrf_token, task["id"], "delete", "Remove")
            if task["persistence"] != "fresh":
                more += _action_form(state.csrf_token, task["id"], "reset-context", "Reset context")
        last = _escape((latest.get("outcome") if latest["status"] == "completed" else None) or latest["status"]) if latest else "Not run"
        failure_count = task.get("consecutive_failures", 0)
        failure_limit = task.get("failure_limit", DEFAULT_FAILURE_LIMIT)
        failure_label = f'{failure_count} consecutive failure' + ('' if failure_count == 1 else 's')
        failure_note = f'{failure_label} · pause at {failure_limit}' if failure_limit else f'{failure_label} · auto-pause off'
        last_timing = f'{_time(latest["started_at"])} · {_duration(_run_seconds(latest, now))} {"elapsed" if running else "duration"}' if latest else ""
        interval_label = f'Every {task["repeat_minutes"]} minutes' if task["interval"] == "custom" else {"once": "Once", "event": "On external event", "hourly": "Every hour", "daily": "Every 24 hours", "weekly": "Every 7 days"}[task["interval"]]
        if task["continue_until_done"]:
            interval_label += " · until goal complete"
        stop_note = f'<p class="agent-help">{_escape(task["stop_reason"])}</p>' if task.get("stop_reason") else ""
        next_due = task["next_run"] if task["enabled"] else None
        if next_due is not None or task["queued"] and task["not_before"]:
            next_due = max(next_due or 0, task["not_before"])
        attention = _task_needs_review(task, runs, pending_by_task)
        tone = "warning" if attention or running or task["queued"] else "success" if task["enabled"] else ""
        due_label = _time(next_due) if next_due is not None else "Waiting in queue" if task["queued"] else "Not scheduled"
        run_note = '<p class="agent-help">Run once keeps this schedule ' + ('active.' if task['enabled'] else 'inactive. Use ' + ('Start schedule' if task.get('draft') else 'Resume') + ' to enable it.') + '</p>' if repeat_seconds(task) and not running and not task['queued'] else ""
        if next_due is not None and next_due <= now:
            due_label += " · due; waiting for worker"
        proposals = pending_by_task.get(task['id'], [])
        pending = bool(proposals)
        target_rows = completed_rows if (task['interval'] == 'once' and not repeat_seconds(task)
            and task['attempts'] and not task['keep'] and not task['draft'] and not task['enabled']
            and not running and not task['queued'] and not pending) else rows
        target_rows.append(f'''<li><article class="agent-task{' agent-task-active' if running or task['queued'] else ' agent-task-attention' if attention else ''}" aria-labelledby="task-{task['id']}">
<div class="agent-task-head"><div><h3 id="task-{task['id']}">{_escape(task['title'])}</h3><p class="agent-help"><code>{_escape(task['directory'])}</code></p></div><span class="badge {tone}">{task_status}</span></div>
{('<p class="agent-help">Follow-up in <a href="/agents?context=' + _escape(task['source_task_id']) + '">original task history</a></p>') if task['source_task_id'] else ''}
{('<span class="badge warning">Follow-up requested</span>') if pending else ''}
{('<p class="agent-result-preview">' + _escape(_redact_log_message(latest.get('summary') or latest.get('output') or latest.get('message') or '')[:500]) + '</p>') if latest else ''}
{''.join(_render_follow_up(run, manager, state.csrf_token) for run in proposals)}
{stop_note}{_render_inspection_access(task)}<dl class="agent-task-facts"><div><dt>Schedule</dt><dd>{_escape(interval_label)}</dd></div><div><dt>Next run · UTC</dt><dd>{due_label}</dd></div><div><dt>Latest result</dt><dd>{last.capitalize()}<small>{last_timing}</small></dd></div></dl>
<div class="agent-buttons">{controls}</div>{run_note}
<details><summary>Task settings and more actions</summary><p class="agent-help">{'Inspect only' if task['mode'] == 'inspect' else 'Workspace changes'} · command network {"on" if task['network'] else "off"} · {_escape(task['timeout_minutes'])} min cap · {_escape(task['model'] or 'Configured model')} · effort {_escape(task['effort'] or 'default')}</p>
<p class="agent-help">{failure_note} · {_escape(task['persistence'])} · {_escape(task['workspace'])} · {task['attempts']} attempts · {task['tokens_used']} reported tokens</p><div class="agent-buttons">{more}</div><p class="agent-help">Remove deletes the saved task; retained run history stays available. Reset context keeps workspace files and receipts.</p></details></article></li>''')
    schedules = '<ul class="agent-task-list">' + "".join(rows) + '</ul>' if rows else '''<div class="agent-panel agent-start"><p><strong>No prompt tasks yet.</strong><br>Write a prompt, choose a starting template, or use Agent tools for a guided system or data task.</p><a class="refresh-link agent-primary" href="/agents?view=new">New task</a><a class="refresh-link" href="/agents?view=templates">Browse templates</a></div>'''
    if completed_rows and not rows:
        schedules = '<p class="empty">No active or saved routine tasks. Completed one-time tasks are below.</p>'
    if storage_error:
        schedules = '<p class="empty">Saved tasks could not be loaded. Restore access to task storage, then retry to see schedules and results.</p>'
    elif completed_rows:
        schedules += '<details class="agent-completed"><summary>Completed one-time tasks · ' + str(len(completed_rows)) + '</summary><ul class="agent-task-list">' + ''.join(completed_rows) + '</ul></details>'
    history = []
    for run in reversed(runs) if history_view else []:
        tone = "error" if run["status"] in {"failed", "interrupted"} or run.get("outcome") in {"blocked", "failed"} else "success" if run["status"] == "completed" else "warning"
        elapsed = _run_seconds(run, now)
        timing = f'{_duration(elapsed)} elapsed · {_duration(max(0, run["task"]["timeout_minutes"] * 60 - elapsed))} remaining at page load' if run["status"] == "running" else f'{_duration(elapsed)} duration'
        exit_note = f' · exit code {_escape(run["exit_code"])}' if run.get("exit_code") is not None else ""
        outcome_label = (run.get("outcome") or "completed") if run["status"] == "completed" else run["status"]
        follow_status = _follow_up_status(manager, run).get('status') if run.get('follow_up') else None
        history.append(f'''<details id="run-{run['id']}" class="agent-panel agent-run"{' open' if query.get('run') == run['id'] else ''}><summary><span class="event-head"><strong>{_escape(run['task']['title'])}</strong><span><span class="badge {tone}">{_escape(outcome_label)}</span>{'<span class="badge warning">Follow-up requested</span>' if follow_status == 'pending' else ''}</span></span><span class="agent-help">{_time(run['started_at'])} · {_escape(run['task']['directory'])}</span><span class="agent-result-preview">{_escape(_redact_log_message(run.get('summary') or run.get('output') or run.get('message') or '')[:350])}</span></summary>
<p>{_escape(_redact_log_message(run['message']))}</p><p class="agent-help">{timing}{exit_note} · Finished {_time(run['finished_at'])}</p>
<h3>Report</h3><pre>{_escape(_redact_log_message(run['output'])) if run['output'] else 'Output will appear when the run finishes.' if run['status'] == 'running' else 'No output recorded.'}</pre>
<details><summary>Execution settings</summary><p class="agent-help">{_escape(run['task']['mode'])} · model {_escape(run['task']['model'] or 'configured default')} · effort {_escape(run['task']['effort'] or 'default')} · {_escape(run['task']['timeout_minutes'])} min cap · command network {'on' if run['task']['network'] else 'off'} · temporary writes {'on' if run['task']['temporary_files'] else 'off'} · web search {_escape(run['task']['web_search'])} · Codex history {'kept' if run['task']['session_history'] or run['task']['persistence'] == 'conversation' else 'ephemeral'}</p></details>
<p class="agent-help">Process: {_escape(run['status'])} · Task outcome: {_escape(run.get('outcome') or 'Not reported by this older run')} · context {_escape(run['task']['persistence'])} · workspace {_escape(run.get('execution_directory') or run['task']['directory'])}</p>
<a class="refresh-link" href="/agents?reuse={run['id']}">Reuse run settings</a>
{_render_inspection_access(run['task'], blocked=run.get('outcome') == 'blocked')}
{_render_follow_up(run, manager, state.csrf_token)}
{('<p class="agent-help">Environment feedback: ' + _escape(_redact_log_message(run['environment_feedback'])) + ' · <a href="/agent-inbox">Operator inbox</a></p>') if run.get('environment_feedback') else ''}
<details><summary>Prompt used for this run</summary><pre>{_escape(run['task']['prompt'])}</pre></details></details>''')
    workbench = f'<div class="agent-columns">{helper}{composer}</div>'
    task_count = "Unavailable" if storage_error else f"{len(tasks)} saved · {sum(t['enabled'] and bool(repeat_seconds(t)) for t in tasks)} repeating · {sum(t['queued'] for t in tasks)} queued{_other_schedules(tasks)}"
    task_section = f'''<section aria-labelledby="agent-tasks-heading"><div class="section-heading">{render_heading("Prompt tasks", "agents", heading_id="agent-tasks-heading")}<span class="count">{task_count}</span></div>{schedules}</section>'''
    active = next((run for run in runs if run["status"] == "running"), None)
    active_html = ""
    if active:
        elapsed = _run_seconds(active, now)
        active_html = f'''<aside class="agent-panel agent-active" role="status"><strong>Running: {_escape(active['task']['title'])}</strong><p class="agent-help">{_duration(elapsed)} elapsed · {_duration(max(0, active['task']['timeout_minutes'] * 60 - elapsed))} remaining at page load · <code>{_escape(active['task']['directory'])}</code></p><p class="agent-help">Refresh status for updated timing. Other work stays queued until this run finishes.</p></aside>'''
    worker_html = ""
    try:
        worker = worker_status(manager)
        worker_html = '<p class="agent-help">Task worker: ' + ("Running independently" if worker["installed"] and worker["running"] else "Running" if worker["running"] else "Installed; not running" if worker["installed"] else "Panel fallback; refresh setup for independent scheduling") + '</p>'
    except (OSError, ValueError, RuntimeError):
        worker_html = '<p class="agent-help">Task worker status unavailable.</p>'
    context_html = _render_task_context(manager, query["context"]) if "context" in query else ""
    if "receipt" in query:
        context_html += _render_receipt(manager, query["receipt"], state.csrf_token)
    refresh_href = "/agents?view=history" if history_view else "/agents?view=tasks"
    if "run" in query:
        refresh_href = "/agents?" + urllib.parse.urlencode({"run": query["run"]}) + "#run-" + query["run"]
    elif "context" in query or "receipt" in query:
        refresh_href = "/agents?" + urllib.parse.urlencode({key: value for key, value in query.items() if key in {"context", "receipt"}})
    overview = f'<div class="section-heading"><div>{worker_html}</div><a class="refresh-link" href="{_escape(refresh_href)}">Refresh status</a></div>{active_html}'
    catalog = f'<section>{render_heading("Task templates", "agent-tools")}<p class="endpoint">Choose a starting point, then review and submit its prompt. Selecting a template never runs or schedules work. Templates follow configured features and detected commands; detection does not verify health or sign-in.</p>{template_links}</section>'
    main = catalog if query.get('view') == 'templates' else workbench if composing else overview + task_section
    history_count = "Unavailable" if storage_error else f"Latest {len(runs)} runs · {_duration(sum(_run_seconds(run, now) for run in runs if run['status'] != 'running'))} recorded runtime"
    history_empty = "Run history could not be loaded. Retry after restoring task storage." if storage_error else "No prompt runs have been recorded. Run a task to see its report here."
    if history_view:
        main = overview + context_html + f'''<section aria-labelledby="agent-history-heading"><div class="section-heading">{render_heading("Run history", "jobs", heading_id="agent-history-heading")}<span class="count">{history_count}</span></div><div class="agent-history">{''.join(history) or '<p class="empty">' + history_empty + '</p>'}</div></section>'''
    actions = '<a class="refresh-link" href="/agents?view=templates">Templates</a><a class="refresh-link agent-primary" href="/agents?view=new">New task</a>'
    return agent_document(state, style + _STYLE, title="Run history" if history_view else "Task templates" if query.get('view') == 'templates' else "Prompt tasks",
        current="history" if history_view else "tasks", content=alert + main, actions=actions,
        description="Reports, evidence, and follow-up decisions." if history_view else "Run once, schedule a routine, or continue a bounded goal.")


def _render_dashboard(state: Any, style: str, tasks: list[dict[str, Any]], runs: list[dict[str, Any]],
                      pending: dict[str, list[dict[str, Any]]], *, error: str = "") -> str:
    """Summarize saved evidence without running tools or rendering other tabs."""
    from common.service_tools import web_panel_maintenance as maintenance
    from lib.agent_inbox import AgentInbox

    features = state.features()
    manager = state.agent_tasks
    alert = '<aside class="status failed" role="alert">' + _escape(error or manager.error) + '</aside>' if error or manager.error else ''
    metrics = _render_run_summary(tasks, runs, pending) if not error else ''
    attention = []
    for task in tasks:
        if not _task_needs_review(task, runs, pending):
            continue
        latest = next((run for run in reversed(runs) if run['task']['id'] == task['id']), None)
        label = f"{len(pending[task['id']])} pending follow-up(s)" if pending.get(task['id']) else task.get('stop_reason') or 'Latest result needs review'
        attention.append(f'<li><a href="/agents?view=tasks"><strong>{_escape(task["title"])}</strong></a><small>{_escape(label)}</small>'
                         + ('<p>' + _escape(_redact_log_message(latest.get('summary') or latest.get('message') or '')[:220]) + '</p>' if latest else '') + '</li>')
    recent = []
    for run in reversed(runs[-5:]):
        outcome = run.get('outcome') or run['status']
        recent.append(f'<li><a href="/agents?run={run["id"]}#run-{run["id"]}"><strong>{_escape(run["task"]["title"])}</strong></a>'
                      f'<small>{_escape(outcome.capitalize())} · {_time(run["started_at"])}</small>'
                      '<p>' + _escape(_redact_log_message(run.get('summary') or run.get('message') or '')[:280]) + '</p></li>')
    worker_note = 'Task worker status unavailable'
    try:
        worker = worker_status(manager)
        worker_note = 'Task worker running independently' if worker['running'] else 'Task worker installed; not running' if worker['installed'] else 'Panel scheduling fallback; rerun setup for the independent worker'
    except (OSError, ValueError, RuntimeError):
        pass
    left = '<section class="agent-dashboard-box"><div class="section-heading">' + render_heading('Needs attention', 'agents') + '<a href="/agents?view=tasks">Tasks</a></div>'
    left += '<ul class="agent-dashboard-list">' + ''.join(attention[:6]) + '</ul>' if attention else '<p class="endpoint">No saved tasks need review.</p>'
    if len(attention) > 6:
        left += f'<p class="endpoint">{len(attention) - 6} more tasks need review.</p>'
    left += '</section><section class="agent-dashboard-box"><div class="section-heading">' + render_heading('Recent runs', 'jobs') + '<a href="/agents?view=history">All run history</a></div>'
    left += '<ul class="agent-dashboard-list">' + ''.join(recent) + '</ul>' if recent else '<p class="empty">No runs recorded. Start a one-time task or maintenance check.</p>'
    left += '</section>'
    right = ''
    if 'maintenance-agent' in features.pages:
        right += '<section class="agent-dashboard-box">' + render_heading('Maintenance', 'maintenance')
        try:
            policy, records = maintenance.load_policy(), maintenance.load_reports()
            latest = records['reports'][-1] if records['reports'] else None
            right += '<p><strong>' + ('Automatic checks enabled' if policy['enabled'] else 'Manual checks only') + '</strong></p>'
            right += '<p class="endpoint">Automatic host checks ' + ('on' if policy['enabled'] and policy['host_checks'] else 'off') + ' · security audits ' + ('on' if policy['enabled'] and policy['security'] else 'off') + ' · housekeeping ' + ('on' if policy['enabled'] and policy['routines'] else 'off') + '</p>'
            right += '<p class="endpoint">Last check: ' + _escape(latest['checked_at'] if latest else 'Not run') + (f' · {len(latest["issues"])} findings' if latest else '') + '</p>'
            right += f'<form method="post" action="/actions/maintenance-agent"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><button name="submit" value="run">Check once now</button></form>'
        except (OSError, ValueError, RuntimeError):
            right += '<p class="empty">Maintenance state unavailable. Open its tab for recovery guidance.</p>'
        right += '<p><a href="/maintenance-agent">Settings and maintenance records</a></p></section>'
    if 'agent-inbox' in features.pages:
        right += '<section class="agent-dashboard-box">' + render_heading('Operator inbox', 'notifications')
        try:
            mail = AgentInbox(manager.home).list('operator')
            right += f'<p><strong>{len(mail)} unread message(s)</strong></p><p class="endpoint">Agent environment feedback and account-local messages.</p>'
            if mail:
                right += '<p>' + _escape(_redact_log_message(mail[-1]['message'])[:180]) + '</p>'
        except (OSError, ValueError, RuntimeError):
            right += '<p class="empty">Inbox state unavailable.</p>'
        right += '<a href="/agent-inbox">Read and archive feedback</a></section>'
    diagnostics = state.agent_diagnostics.snapshot()
    right += '<section class="agent-dashboard-box">' + render_heading('Runtime readiness', 'diagnostics')
    right += '<p class="endpoint">' + _escape(worker_note) + '</p><p class="endpoint">Readiness: ' + _escape(diagnostics['status'].replace('_', ' ')) + '</p>'
    right += '<a href="/agents?view=diagnostics">Installation and sign-in checks</a></section>'
    actions = '<a class="refresh-link" href="/agents">Refresh status</a>'
    if features.prompts:
        actions += '<a class="refresh-link agent-primary" href="/agents?view=new">New task</a>'
    return agent_document(state, style + _STYLE, title="Agents dashboard", current="dashboard", actions=actions,
        description="Current work, findings, maintenance, and feedback in one place.",
        content=alert + metrics + '<div class="agent-dashboard-grid"><div>' + left + '</div><div>' + right + '</div></div>')


def _render_task_context(manager: AgentTasks, identifier: str) -> str:
    try:
        context = manager.context_snapshot(identifier)
        history = manager.contexts.history(identifier)
    except (OSError, ValueError, RuntimeError):
        return '<aside class="status failed" role="alert">Task context is unavailable. Inspect its private storage and saved task ID.</aside>'
    workspace = context.get("workspace") or {}
    receipts = "".join(f'<li>{_time(run["started_at"])} · {_escape(run.get("outcome") or run["status"])} · {_escape(_redact_log_message(run.get("message", "")))} · <a href="/agents?receipt={identifier}.{_escape(run["id"])}">Inspect receipt</a><br><code>{_escape(run.get("log_path") or "No provider log")}</code></li>' for run in history)
    return f'''<section class="agent-panel" aria-label="Task context"><h2>Task context and evidence</h2>
<p class="agent-help">Task <code>{_escape(identifier)}</code> · {_escape(context['directory'])}</p>
<p>Conversation: <code>{_escape(context.get('session_id') or 'No saved conversation')}</code></p>
<p>Managed workspace: <code>{_escape(workspace.get('path') or 'Not selected')}</code></p>
{'<p class="agent-help">A persistence change will reset this context when its next run starts.</p>' if context.get('reset_pending') else ''}
<h3>Saved checkpoint</h3><pre>{_escape(_redact_log_message(context['checkpoint'])) if context['checkpoint'] else 'No checkpoint saved yet.'}</pre>
<h3>Retained evidence</h3><ol>{receipts}</ol><p class="agent-help">Resetting context keeps workspaces and receipts. Native T3 thread binding awaits stable-release qualification.</p></section>'''


def _render_receipt(manager: AgentTasks, reference: str, csrf: str = "") -> str:
    try:
        identifier, run_id = reference.split(".")
        run = manager.contexts.receipt(identifier, run_id)
    except (OSError, ValueError, RuntimeError):
        return '<aside class="status failed" role="alert">This retained run receipt is unavailable. Check its task and run IDs and evidence retention.</aside>'
    def display(value: object) -> str:
        return _escape(_redact_log_message(str(value)))
    return f'''<section class="agent-panel" aria-label="Run receipt"><h2>Retained run receipt</h2>
<p>Task <code>{_escape(identifier)}</code> · Run <code>{_escape(run_id)}</code></p>
<p>{display(run['task'].get('title', 'Task'))} · Process {display(run.get('status', 'unknown'))} · Outcome {display(run.get('outcome', 'not reported'))}</p>
<h3>Verification evidence</h3><pre>{display(json.dumps(run.get('evidence', []), ensure_ascii=False, indent=2))}</pre>
<h3>Checkpoint from this run</h3><pre>{display(run.get('checkpoint', ''))}</pre>
<h3>Output</h3><pre>{display(run.get('output', ''))}</pre>
{_render_inspection_access(run['task'], blocked=run.get('outcome') == 'blocked')}
{_render_follow_up(run, manager, csrf)}
<details><summary>Original task settings</summary><pre>{display(json.dumps(run['task'], ensure_ascii=False, indent=2))}</pre></details>
<p class="agent-help">Historical evidence does not change task permissions or reactivate removed work.</p></section>'''


def _follow_up_status(manager: AgentTasks, run: dict[str, Any]) -> dict[str, Any]:
    try:
        return follow_up_state(manager, run['task']['id'], run['id'])
    except (OSError, ValueError, RuntimeError):
        return {"status": "unavailable", "requests": {}}


def _render_service_requests(status: dict[str, Any]) -> str:
    from common.service_tools.web_panel_admin import _https_url

    rows = []
    for unit, request in status.get('requests', {}).items():
        url = _https_url(request.get('review_url'))
        rows.append('<p class="agent-help">' + _escape(unit) + ': ' + _escape(request.get('state', 'unknown'))
            + (' · one-time exact command approval' if request.get('operation') == 'command.run' else '')
            + (f' · <a href="{_escape(url)}" target="_blank" rel="noopener noreferrer">Open service approval ↗</a>' if url else '') + '</p>')
    return ''.join(rows)


def _render_follow_up(run: dict[str, Any], manager: AgentTasks, csrf: str) -> str:
    proposal = run.get("follow_up")
    if not isinstance(proposal, dict):
        return ""
    reference = urllib.parse.urlencode({"follow-up": run["task"]["id"] + "." + run["id"]})
    status = _follow_up_status(manager, run)
    pending = status['status'] == 'pending'
    actions = ''
    if pending:
        legacy = follow_up_requires_review(proposal)
        def button(action: str, label: str) -> str:
            return f'''<form method="post" action="/actions/agent-task"><input type="hidden" name="csrf" value="{_escape(csrf)}"><input type="hidden" name="id" value="{_escape(run['task']['id'])}"><input type="hidden" name="run_id" value="{_escape(run['id'])}"><button name="action" value="{action}">{label}</button></form>'''
        if not legacy:
            actions = button('follow-up-approve', 'Check approval and launch' if status['requests'] else 'Approve once')
        actions += f'<a class="refresh-link" href="/agents?{_escape(reference)}">Review follow-up task</a>'
        actions += button('follow-up-block', 'Block')
        if proposal.get('service_restarts'):
            actions += button('follow-up-privilege', 'Request service approval')
        if legacy:
            actions += '<p class="agent-help">This older proposal needs an access review. Select Review follow-up task, check its permissions and prerequisites, then Approve once.</p>'
        elif proposal.get('operator_requirements'):
            actions += '<p class="agent-help">Approve once confirms that the prerequisites listed above are satisfied and authorizes this task. Complete any outside preparation first; service approvals remain separate.</p>'
    else:
        actions = '<p class="agent-help">Follow-up ' + _escape(status['status']) + '.</p>'
    requests = _render_service_requests(status)
    access_details = []
    for label, field in (('Additional writes', 'writable_directories'), ('Operator prerequisites', 'operator_requirements'), ('Service restarts', 'service_restarts')):
        if proposal.get(field):
            access_details.append('<p class="agent-help">' + label + ': ' + _escape(_redact_log_message(', '.join(proposal[field]))) + '</p>')
    return f'''<aside class="agent-panel agent-follow-up"><h3>Follow-up requested</h3>
<strong>{_escape(proposal.get('title', ''))}</strong>
<p>{_escape(_redact_log_message(str(proposal.get('reason', ''))))}</p>
<p class="agent-help">Requested access: {_escape(proposal.get('mode'))} · {_escape(proposal.get('workspace'))} · command network {'on' if proposal.get('network') else 'off'} · temporary writes {'on' if proposal.get('temporary_files') else 'off'} · <code>{_escape(proposal.get('directory'))}</code></p>
{''.join(access_details)}{requests}<div class="agent-buttons">{actions}</div>
<details><summary>Proposed actions and verification</summary><pre>{_escape(_redact_log_message(proposal.get('prompt', '')))}</pre></details>
<p class="agent-help">Approval queues one attempt. Service requests use the separate privilege portal and must succeed before launch. Blocking stops this proposal; it cannot undo a service action already executed.</p></aside>'''


def _render_diagnostics(snapshot: dict[str, Any], csrf: str) -> str:
    form = f'''<form class="job-load" method="post" action="/actions/agent-diagnostics"><input type="hidden" name="csrf" value="{_escape(csrf)}"><button{' disabled' if snapshot['status'] == 'running' else ''}>Check agent readiness</button></form>'''
    if snapshot["status"] != "loaded":
        return form + '<p class="empty">' + _escape(snapshot.get("message") or ("Checks are running. Refresh status to see results." if snapshot["status"] == "running" else "Check selected agent installation versions, credential metadata, configured T3 Code, and maintenance holds. Checks do not run prompts or repair the host.")) + '</p>'
    cards = []
    for tool in snapshot["tools"]:
        installed = tool["installed"]
        if not installed:
            continue
        credential = "Needs attention" if tool.get("credential_healthy") is False else "File present; authentication not verified" if tool.get("credential") else "No credential file"
        if tool["tool"] == "codex" and tool.get("credential_healthy") is True:
            credential = "Local credential check passed"
        cards.append(f'''<div class="agent-panel"><strong>{_escape({'codex': 'Codex', 'claude': 'Claude Code', 'opencode': 'OpenCode', 'gh': 'GitHub CLI'}[tool['tool']])}</strong><dl class="agent-runtime"><div><dt>Installation</dt><dd>{'Installed' if installed else 'Not installed'}</dd></div><div><dt>Version</dt><dd>{_escape(tool.get('version') or 'Unavailable' if installed else '—')}</dd></div><div><dt>Credentials</dt><dd>{credential if installed else '—'}</dd></div></dl></div>''')
    t3 = snapshot["t3"]
    if t3:
        checks = "".join(f'<div><dt>{_escape(name.replace("_", " "))}</dt><dd>{"Passed" if result is True else "Needs attention"}</dd></div>' for name, result in t3["checks"].items())
        cards.append(f'<div class="agent-panel"><strong>T3 Code</strong><p class="agent-help">{_escape(t3.get("version") or "Version unavailable")} · {_escape(t3["status"])}</p><details><summary>Readiness details</summary><dl class="agent-runtime">{checks}</dl></details></div>')
    hold = snapshot["maintenance"]
    record = snapshot["record"]
    record_note = f'Last saved readiness {_escape(record.get("recorded_at", "Unknown time"))} · boot {"current" if record.get("current_boot") is True else "previous or unknown"}' if record else _escape(snapshot["record_error"] or "No saved readiness record")
    cards.append(f'<div class="agent-panel"><strong>Maintenance hold</strong><p class="agent-help">{_escape(hold["status"])}{ " · until " + _escape(hold.get("expires_at")) if hold["active"] else ""}</p><p class="agent-help">{record_note}</p><a class="refresh-link" href="/jobs">Inspect scheduled jobs</a></div>')
    return form + f'<p class="count">Checked {_time(snapshot["checked_at"])} · credential checks inspect local metadata, not provider authentication</p><div class="grid">' + "".join(cards) + '</div>'


def _render_t3_update(state: Any) -> str:
    if not state.t3_update_available():
        return ""
    running = state.action_status == "running"
    message = state.action_message or "Update the installed T3 Code service and verify readiness. Active sessions may reconnect."
    if running:
        started_at = state.action_started_at
        if isinstance(started_at, (int, float)):
            minutes, seconds = divmod(max(0, int(time.monotonic() - started_at)), 60)
            message += f" Still running · {minutes}m {seconds:02d}s elapsed."
        message += " Refresh status for progress. The update continues in the background."
    output = '<details><summary>View command output</summary><pre>' + _escape(state.action_output) + '</pre></details>' if state.action_output else ''
    role = "alert" if state.action_status == "failed" else "status"
    return f'''<section aria-label="T3 Code maintenance"><div class="action"><div><strong>T3 Code maintenance</strong><p role="{role}">{_escape(message)}</p></div><form method="post" action="/actions/t3-update"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><input type="hidden" name="return" value="agents"><button{' disabled' if running else ''}>{'Update in progress…' if running else 'Update T3 Code'}</button></form></div>{output}<a class="refresh-link" href="/agents?view=diagnostics">Refresh status</a></section>'''
