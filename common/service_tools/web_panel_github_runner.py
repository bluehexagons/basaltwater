"""Explicitly loaded runner state and one-time pending registration form."""

from __future__ import annotations

import html
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

from common.service_tools.web_panel_diagnostics import _bounded_command
from common.service_tools.web_panel_jobs import parse_job_query
from common.service_tools.web_panel_templates import NavigationItem, render_document
from lib.atomic_io import read_json_file
from lib.github_runner_config import APP, CREDENTIAL_FILES, EVENTS, MAX_OUTCOMES, OUTCOMES, POLICY, SERVICE, validate_policy
from lib.github_runner_registration import ENDPOINT
from lib.validators import validate_github_login


_COLLECTOR = threading.BoundedSemaphore(1)


@dataclass
class RunnerSnapshot:
    policy: dict = field(default_factory=dict)
    service: dict = field(default_factory=dict)
    outcomes: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    recovery_required: bool = False


def parse_runner_query(raw: str) -> bool:
    return parse_job_query(raw)


def _outcome(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("allowed")) is not bool:
        raise ValueError("Invalid outcome")
    for name, pattern in (("time", r"[0-9T:+.Z-]{1,40}"), ("reason", r"[a-z-]{1,64}"), ("run_id", r"[0-9]{0,20}")):
        if not isinstance(value.get(name), str) or not re.fullmatch(pattern, value[name]):
            raise ValueError("Invalid outcome")
    identity = value.get("identity")
    if not isinstance(identity, str) or (identity and not validate_github_login(identity)):
        raise ValueError("Invalid outcome identity")
    if value.get("event") not in (*EVENTS, "unsupported"):
        raise ValueError("Invalid outcome event")
    return {name: value[name] for name in ("time", "allowed", "reason", "identity", "event", "run_id")}


def collect_runner_status() -> RunnerSnapshot:
    result = RunnerSnapshot()
    if not _COLLECTOR.acquire(blocking=False):
        result.issues.append("A runner inspection is running. Try again shortly.")
        return result
    try:
        try:
            result.policy = validate_policy(read_json_file(POLICY, max_bytes=16 * 1024))
            if result.policy["registration"] == "pending" and any(os.path.lexists(APP + "/" + name) for name in CREDENTIAL_FILES):
                result.recovery_required = True
                result.issues.append("Partial registration requires deliberate recovery. Follow the runner guide over SSH before retrying.")
        except (OSError, ValueError, TypeError):
            result.issues.append("Runner policy is unavailable or invalid.")
        output, issue = _bounded_command([
            "systemctl", "show", SERVICE, "--no-pager",
            "--property=LoadState,ActiveState,SubState,UnitFileState,Result",
        ])
        if issue:
            result.issues.append(issue)
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        result.service = {name: fields.get(name, "unavailable")[:80]
                          for name in ("LoadState", "ActiveState", "SubState", "UnitFileState", "Result")}
        try:
            outcomes = read_json_file(OUTCOMES, max_bytes=64 * 1024)
            if not isinstance(outcomes, list) or len(outcomes) > MAX_OUTCOMES:
                raise ValueError("Invalid outcomes")
            result.outcomes = [_outcome(row) for row in reversed(outcomes)]
        except FileNotFoundError:
            result.issues.append("No gate outcomes recorded yet.")
        except (OSError, ValueError, TypeError):
            result.issues.append("Gate outcomes are unavailable or invalid.")
        return result
    finally:
        _COLLECTOR.release()


def _registration_form(policy: dict, csrf: str, secure: bool) -> str:
    if policy.get("registration") != "pending":
        return ""
    organization = policy.get("organization")
    target = organization or policy["repository"]
    settings = (f"https://github.com/organizations/{organization}/settings/actions/runners/new" if organization else
                f"https://github.com/{target}/settings/actions/runners/new")
    instructions = f'''<section aria-labelledby="registration-heading"><h2 id="registration-heading">Register this runner</h2>
<p>In <a class="inline-link" href="{html.escape(settings, quote=True)}">GitHub runner settings for {html.escape(target)}</a>,
select New self-hosted runner and copy only the token following <code>--token</code> in the configuration command.
The registration token expires after one hour. Use a token for this {"organization" if organization else "repository"};
do not enter a personal access token or GitHub App key.</p>'''
    if organization:
        instructions += '<p>In GitHub, restrict the selected runner group to the private repositories that may use this machine.</p>'
    if not secure or not csrf:
        return instructions + '<p>Open this panel over HTTPS to submit a token, or register through setup with <code>--ci-registration-token-file</code>.</p></section>'
    return instructions + f'''<form class="diagnostic-filters" method="post" action="{ENDPOINT}" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">
<label>One-time registration token<input type="password" name="token" required minlength="20" maxlength="4096"
pattern="[A-Za-z0-9_\\-]{{20,4096}}" autocomplete="off" spellcheck="false" autocapitalize="none"></label>
<div><button type="submit">Register runner</button></div></form>
<p>The token is used for this attempt only. Registration can take several minutes. If GitHub rejects an expired token,
create a fresh token in the same settings and submit it again. Refresh status after an interrupted request before retrying.</p></section>'''


def render_runner_page(load: bool, style: str, host: str, *, navigation: Iterable[NavigationItem],
                       csrf: str = "", secure: bool = False, error: str = "") -> str:
    content = '<p class="empty">Load runner status to inspect this host.</p>'
    if load:
        snapshot = collect_runner_status()
        content = "".join('<p role="status">' + html.escape(issue) + '</p>' for issue in snapshot.issues)
        facts = {
            "Registration": {"pending": "Pending registration", "registered": "Registered"}.get(
                snapshot.policy.get("registration"), "Unavailable") if not snapshot.recovery_required else "Recovery required",
            "Scope": "Organization" if snapshot.policy.get("organization") else "Repository",
            "Organization" if snapshot.policy.get("organization") else "Repository":
                snapshot.policy.get("organization") or snapshot.policy.get("repository", "Unavailable"),
            "Runner group": snapshot.policy.get("runner_group") or "Default",
            "Runner version": snapshot.policy.get("runner_version", "Unavailable"),
            "Labels": ", ".join(["self-hosted", "Linux", "X64", *snapshot.policy.get("labels", [])]) if snapshot.policy else "Unavailable",
            "Allowed users (read-only)": ", ".join(snapshot.policy.get("allowed_users", [])) or "Unavailable",
            "Service": snapshot.service.get("ActiveState", "Unavailable"),
            "Service detail": snapshot.service.get("SubState", "Unavailable"),
            "Start at boot": snapshot.service.get("UnitFileState", "Unavailable"),
            "Last service result": snapshot.service.get("Result", "Unavailable"),
        }
        content += '<section><h2>Configured runner</h2><dl class="job-facts">' + "".join(
            f'<div><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>' for label, value in facts.items()
        ) + '</dl></section>'
        if not snapshot.recovery_required:
            content += _registration_form(snapshot.policy, csrf, secure)
        content += '<section><h2>Recent gate outcomes</h2>'
        if not snapshot.outcomes:
            content += '<p class="empty">No gate outcomes to display.</p>'
        for row in snapshot.outcomes:
            outcome = "Allowed" if row["allowed"] else "Denied"
            content += '<article class="event"><strong>' + outcome + '</strong><p>' + html.escape(
                f'{row["time"]} · {row["event"]} · {row["identity"] or "unknown identity"} · {row["reason"]} · run {row["run_id"] or "unknown"}'
            ) + '</p></article>'
        content += '</section>'
    if error:
        content = '<p class="status failed" role="alert">' + html.escape(error) + '</p>' + content
    return render_document(
        title=f"GitHub runner · {host}", style=style,
        header=f'<header><p class="eyebrow">Basaltwater web panel</p><h1>GitHub runner</h1><p class="lede">Local runner on {html.escape(host)}</p></header>',
        content='<form method="get" action="/github-runner"><button name="load" value="1">Load runner status</button></form>'
                '<p>Service state is local; confirm GitHub connectivity in the configured scope’s runner settings. '
                'The last 100 gate outcomes are runner-writable telemetry. The allowlist is managed through setup.</p>' + content,
        navigation=navigation,
        footer='<footer><a href="/">Back to dashboard</a><span>Loaded on request · no automatic refresh</span></footer>',
    )
