"""Publishing management in the existing authenticated web panel."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import os

from common.service_tools.web_panel_templates import panel_navigation, render_document
from lib.publishing import editor_link


def utc_instant(value: str) -> float:
    if not value:
        return 0
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Publication time must include a UTC offset or Z")
    return instant.astimezone(timezone.utc).timestamp()


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _input(name: str, label: str, value: str = "", *, kind: str = "text", required: bool = True) -> str:
    return f'<label>{_escape(label)}<input name="{name}" type="{kind}" value="{_escape(value)}" {"required" if required else ""}></label>'


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{_escape(value)}">'


def _select(name: str, label: str, items: list[tuple[str, str]], *, optional: bool = False) -> str:
    options = '<option value="">None</option>' if optional else ""
    options += "".join(f'<option value="{_escape(value)}">{_escape(text)}</option>' for value, text in items)
    return f'<label>{_escape(label)}<select name="{name}">{options}</select></label>'


def render_publishing(state, page_style: str, *, error: str = "") -> str:
    """Render saved observations and exact text only; GET never runs a tool."""
    try:
        snapshot = state.publishing.status()
    except (OSError, RuntimeError, ValueError):
        snapshot = {key: [] for key in ("projects", "tools", "accounts", "artifacts", "runs", "jobs", "drafts", "releases")}
        error = "Publishing state is unavailable. Check the VM account's private state directory and permissions."
    secure = state.credential_transport_available() and os.geteuid() != 0
    def form(action: str, fields: str, label: str) -> str:
        if not secure:
            return '<p class="count">' + _escape(label) + ': requires HTTPS and a non-root panel account.</p>'
        return ('<form method="post" action="/actions/publishing/' + action + '">' + _hidden("csrf", state.csrf_token) +
                fields + '<button>' + _escape(label) + '</button></form>')
    project_options = [(item["id"], item["id"] + " → " + item["target"]) for item in snapshot["projects"]]
    draft_options = [(item["id"], item["title"] + " · " + item["language"] + " · " + item["id"][:8]) for item in snapshot["drafts"]]
    source_options = [(item["id"], item["title"] + " · " + item["language"]) for item in snapshot["drafts"] if not item["source"]]
    release_options = [(item["id"], item["build_id"] + " · " + item["branch"]) for item in snapshot["releases"]]
    content = '<aside class="status failed" role="alert">' + _escape(error) + '</aside>' if error else ""
    if not secure:
        content += '<p class="empty">Publishing changes and logins require the panel HTTPS address. Use the VM CLI over SSH for recovery.</p>'
    content += '''<nav class="catalog-nav" aria-label="Publishing sections"><a href="#accounts">Accounts</a><a href="#projects">Projects</a><a href="#builds">Builds</a><a href="#writing">Writing &amp; translations</a><a href="#releases">Releases</a></nav>
<p>Steam default releases and rollbacks happen on Steamworks. Public writing, including each translation, requires your review. Steam announcements and itch.io posts currently use a reviewed export and human editor handoff.</p>
<section id="accounts"><h2>Publishing accounts</h2><div class="grid">'''
    accounts = {item["id"]: item for item in snapshot["accounts"]}
    for tool in snapshot["tools"]:
        service = tool["provider"]
        account = accounts.get(service, {})
        label = "itch.io / Butler" if service == "butler" else "Steam / SteamCMD"
        status = account.get("state", "not checked") if tool["installed"] else "tool not installed"
        content += '<article class="publishing-card"><h3>' + label + '</h3><p>' + _escape(status) + '</p>'
        content += '<p>Native session: ' + _escape(tool["local_session"]) + '. File presence does not prove provider acceptance.</p>'
        if account.get("observed_at"):
            content += '<p class="count">Saved observation: ' + _escape(datetime.fromtimestamp(account["observed_at"], timezone.utc).isoformat()) + '</p>'
        fields = _hidden("provider", service)
        if service == "steamcmd":
            fields += _input("username", "Steam account name", account.get("username", ""))
        if tool["installed"]:
            content += form("login", fields, "Sign in / verify session")
            content += form("logout", _hidden("provider", service) + _input("confirmation", "Type remove to erase the local session"), "Remove local session")
        else:
            content += '<p>From the controller, add <code>--publishing-tool ' + service + '</code> to the saved VM setup.</p>'
        link = "https://itch.io/docs/butler/login.html" if service == "butler" else "https://partner.steamgames.com/doc/sdk/uploading"
        content += '<p><a href="' + link + '">Authentication and upload guide</a></p></article>'
    for session in state.publishing_auth.views():
        content += '<article class="publishing-card"><h3>' + _escape(session["provider"]) + ' sign-in</h3><p>' + _escape(session["message"]) + '</p>'
        if session.get("link"):
            content += '<p><a href="' + _escape(session["link"]) + '" rel="noreferrer">Open itch.io sign-in</a></p>'
        if session["state"] == "running":
            if session["challenge"] in {"password", "guard", "redirect"}:
                content += form("login-input", _hidden("id", session["id"]) + _input("response", "Login response", kind="password"), "Submit response")
            content += form("login-cancel", _hidden("id", session["id"]), "Cancel sign-in")
        content += '</article>'
    content += '<details class="publishing-card"><summary>Optional Steam beta promotion API access</summary><p>A separate Steam publisher API key enables observed beta branch promotion. It stays on the VM. Default/public releases always use Steamworks.</p>'
    content += form("steam-api", _input("key", "Steam publisher API key", kind="password"), "Save VM-local API key") + '</details>'
    content += '</div><p class="count">Credentials stay in the VM account’s native store. Removing a local session does not revoke provider access; revoke compromised access on the provider website.</p></section>'
    content += '<section id="projects"><h2>Projects and destinations</h2>'
    for project in snapshot["projects"]:
        languages = project.get("languages")
        content += '<article class="publishing-card"><h3>' + _escape(project["id"]) + '</h3><p>' + _escape(project["target"]) + '</p><p><code>' + _escape(project["repository"]) + '</code></p>'
        content += '<p>' + (_escape("Source: " + languages["source"] + " · Supported: " + ", ".join(languages["supported"])) if languages else "Manifest unavailable or invalid") + '</p>'
        content += '<p class="count">Language settings come from basaltwater.json; absent settings mean English only. English and Spanish have initial structural coverage; other languages and provider rendering require qualification.</p>'
        content += '<p><a href="' + _escape(editor_link(project)) + '">Open provider project</a></p>'
        content += form("prepare", _hidden("project", project["id"]), "Prepare completed build")
        content += form("schedule", _hidden("project", project["id"]) + _input("interval", "Upload polling interval (minutes)", "60", kind="number"), "Schedule unattended uploads") + '</article>'
    content += '<details><summary>Configure a project</summary><p>Changing a project invalidates prepared builds and writing reviews. For Steam, choose one app and depot per destination; create additional destinations for other depots.</p>'
    content += form("project", _input("id", "Project/destination ID") + _input("repository", "Project directory on this VM") +
                    _select("provider", "Provider", [("butler", "itch.io / Butler"), ("steamcmd", "Steam / SteamCMD")]) +
                    _input("target", "itch.io owner/game:channel or Steam AppID") + _input("depot", "Steam DepotID", required=False) +
                    _input("username", "Steam account name", required=False) + _input("record", "Completed build record", ".basaltwater/publishing-complete.json"), "Save destination") + '</details></section>'
    content += '<section id="builds"><h2>Prepared builds and upload history</h2><p>Record completed exports with <code>basaltw publish complete</code> before preparation. itch.io channel uploads can become immediately available to players. Steam uploads never promote a branch.</p>'
    for artifact in snapshot["artifacts"][:20]:
        content += '<details class="publishing-card"><summary>' + _escape(artifact["project"] + " · " + artifact["build_id"] + " · " + artifact["id"][:8]) + '</summary><p>SHA-256 <code>' + _escape(artifact["digest"]) + '</code></p>'
        content += '<p>' + str(artifact["file_count"]) + ' files · retained snapshot</p>'
        for path in artifact["public_text"]:
            content += '<p>Review required: ' + _escape(path) + '</p>'
            content += form("artifact-review", _hidden("artifact", artifact["id"]) + _hidden("path", path) + _select("draft", "Exact reviewed text revision", draft_options), "Attach reviewed release notes")
        content += form("upload", _hidden("artifact", artifact["id"]), "Upload this exact build")
        if project_options:
            content += form("promote-itch", _hidden("artifact", artifact["id"]) + _select("destination", "Another configured channel of this itch.io game", project_options), "Promote retained artifact to channel")
        content += form("artifact-remove", _hidden("artifact", artifact["id"]), "Remove unused retained snapshot") + '</details>'
    for run in snapshot["runs"][:40]:
        content += '<article class="publishing-card"><h3>' + _escape(run["project"] + " · " + run["state"]) + '</h3><p>' + _escape(run.get("message", "")) + '</p><p>Run <code>' + _escape(run["id"]) + '</code> · Receipt <code>' + _escape(run.get("receipt", "not identified")) + '</code></p>'
        if run["state"] in {"queued", "running"}:
            content += form("cancel", _hidden("run", run["id"]), "Cancel upload")
        if run["state"] in {"unknown", "uploaded-unverified"}:
            content += form("reconcile", _hidden("run", run["id"]) + _select("outcome", "Observed outcome", [("uploaded", "Uploaded — I checked the provider"), ("not-submitted", "Not submitted — safe to retry")]) + _input("receipt", "BuildID / evidence from provider"), "Record reconciliation")
        if run["operation"] == "upload" and run["state"] in {"uploaded", "operator-confirmed"} and run["project_config"]["provider"] == "steamcmd":
            content += form("release", _hidden("run", run["id"]) + _input("branch", "Branch to release", "default"), "Prepare Steamworks release handoff")
            content += form("beta-prepare", _hidden("run", run["id"]) + _input("branch", "Explicit non-default beta branch"), "Observe and prepare beta promotion")
        content += '</article>'
    content += '<h3>Unattended upload jobs</h3>'
    for job in snapshot["jobs"]:
        content += '<article class="publishing-card"><p>' + _escape(job["project"] + " · " + job["state"]) + '</p>'
        content += '<p class="count">Every ' + str(job["interval"] // 60) + ' minutes · failures ' + str(job["failures"]) + '</p>'
        content += '<p>Next poll: ' + _escape(datetime.fromtimestamp(job["next_at"], timezone.utc).isoformat()) + '</p>'
        if job.get("last_error"):
            content += '<p>' + _escape(job["last_error"]) + '</p>'
        content += form("job", _hidden("job", job["id"]) + _hidden("action", "pause" if job["state"] == "enabled" else "resume"), "Pause" if job["state"] == "enabled" else "Resume with current project configuration") + '</article>'
    content += '</section><section id="writing"><h2>Writing and translations</h2><p>Draft or translate with an agent, then import the final text below. Review each language and destination separately. Exported plain text needs a final formatting check in the provider editor.</p>'
    content += form("writing-task", _select("project", "Project", project_options) + _input("language", "Draft/translation language", "es") +
                    _select("source", "Source revision for translation", source_options, optional=True) + _input("instructions", "Writing instructions / facts to use"), "Prepare agent writing task")
    content += '<details><summary>Import a draft or translation</summary>' + form("draft", _select("project", "Project", project_options) + _input("language", "Language", "en") +
                    _input("title", "Final title") + '<label>Final body<textarea name="body" rows="10" required></textarea></label>' +
                    _select("source", "Source revision (required for translations)", source_options, optional=True) +
                    _select("replaces", "Replaces revision", draft_options, optional=True) + _select("release", "Wait for release", release_options, optional=True) +
                    _input("publish_at", "Handoff time, ISO 8601 with Z or offset (blank: deliberate handoff)", required=False) +
                    _input("late_minutes", "Allowed lateness (minutes)", "60", kind="number"), "Save unreviewed revision") + '</details>'
    drafts = {item["id"]: item for item in snapshot["drafts"]}
    for draft in snapshot["drafts"][:40]:
        content += '<article class="publishing-card"><h3>' + _escape(draft["title"]) + '</h3><p>' + _escape(draft["project"] + " · " + draft["destination"] + " · " + draft["language"] + " · " + draft["state"]) + '</p><p class="count">Revision <code>' + draft["id"] + '</code></p>'
        content += '<div class="publishing-comparison">'
        if draft["source"] in drafts:
            source = drafts[draft["source"]]
            content += '<div><h4>Source (' + _escape(source["language"]) + ')</h4><pre>' + _escape(source["title"] + "\n\n" + source["body"]) + '</pre></div>'
        content += '<div><h4>Final text (' + _escape(draft["language"]) + ')</h4><pre>' + _escape(draft["title"] + "\n\n" + draft["body"]) + '</pre></div></div>'
        timing = datetime.fromtimestamp(draft["publish_at"], timezone.utc).isoformat() if draft["publish_at"] else "Deliberate handoff"
        content += '<p>Timing: ' + _escape(timing) + ' · lateness ' + str(draft["late_minutes"]) + ' minutes · release gate ' + _escape(draft["release"] or "none") + '</p>'
        if draft["review"]:
            content += '<p>Reviewed by ' + _escape(draft["review"]["principal"]) + '</p>'
            content += form("export", _hidden("draft", draft["id"]), "Export reviewed text")
            if draft["state"] == "approved":
                content += form("handoff", _hidden("draft", draft["id"]), "Begin provider-editor handoff")
        if draft["state"] not in {"superseded", "stale", "operator-confirmed"}:
            content += form("review", _hidden("draft", draft["id"]) + _hidden("hash", draft["hash"]) +
                            _select("decision", "Human review decision", [("changes", "Changes requested / revoke approval"), ("approve", "I reviewed this exact final language and destination text")]), "Record human review")
        if draft["state"] == "awaiting-editor":
            project = next(item for item in snapshot["projects"] if item["id"] == draft["project"])
            content += '<p><a href="' + _escape(editor_link(project)) + '">Open provider post editor</a>. Recheck any formatting or text you change before publishing.</p>'
            content += form("post-confirm", _hidden("draft", draft["id"]) + _input("url", "Published post address", kind="url") +
                            _input("confirmation", "Type reviewed to confirm the provider's final text matches this revision"), "Record published post")
        content += '</article>'
    content += '</section><section id="releases"><h2>Steam release handoffs</h2><p>Choose the exact BuildID on Steamworks. Confirm it here only after the site has released it. Dependent posts stay held until confirmation. This also applies to default branch rollbacks.</p>'
    for release in snapshot["releases"]:
        content += '<article class="publishing-card"><h3>' + _escape(release["project"] + " · BuildID " + release["build_id"]) + '</h3><p>' + _escape(release["branch"] + " · " + release["state"]) + '</p><p><a href="' + _escape(release["link"]) + '">Open Steamworks Builds</a></p>'
        if release["state"] == "prepared-beta":
            content += '<p>Observed previous BuildID: ' + _escape(release["previous_build"]) + ' · Audience: unknown · preparation valid for five minutes</p>'
            content += form("beta-promote", _hidden("release", release["id"]), "Promote this BuildID to this beta branch")
        if release["state"] in {"awaiting-steamworks", "unknown"}:
            content += form("release-confirm", _hidden("release", release["id"]) + _input("build_id", "BuildID now live on this branch"), "Record human Steamworks release")
        content += '</article>'
    content += '</section>'
    return render_document(title="Publishing", style=page_style + _STYLE,
                           header='<header><p class="eyebrow">Basaltwater web panel</p><h1>Game publishing</h1><p class="lede">Manage VM publishing accounts, builds and reviewed release writing.</p><a class="refresh-link" href="/publishing">Refresh saved status</a></header>',
                           content=content, navigation=panel_navigation(current="publishing"),
                           footer='<footer><span>Publishing sessions stay on this VM</span><span>Human-reviewed public writing</span></footer>')


FIELDS = {
    "steam-api": {"key"}, "beta-prepare": {"run", "branch"}, "beta-promote": {"release"}, "promote-itch": {"artifact", "destination"},
    "login": {"provider", "username"}, "login-input": {"id", "response"}, "login-cancel": {"id"}, "logout": {"provider", "confirmation"},
    "project": {"id", "repository", "provider", "target", "depot", "username", "record"}, "prepare": {"project"},
    "upload": {"artifact"}, "artifact-review": {"artifact", "path", "draft"}, "artifact-remove": {"artifact"}, "cancel": {"run"},
    "reconcile": {"run", "outcome", "receipt"}, "release": {"run", "branch"}, "release-confirm": {"release", "build_id"},
    "schedule": {"project", "interval"}, "job": {"job", "action"}, "writing-task": {"project", "language", "source", "instructions"},
    "draft": {"project", "language", "title", "body", "source", "replaces", "release", "publish_at", "late_minutes"},
    "review": {"draft", "hash", "decision"}, "export": {"draft"}, "handoff": {"draft"}, "post-confirm": {"draft", "url", "confirmation"},
}


def publishing_action(state, action: str, values: dict[str, str]) -> dict | None:
    if action not in FIELDS or set(values) - FIELDS[action]:
        raise ValueError("Invalid publishing action")
    manager = state.publishing
    principal = state.manifest["username"]
    if action == "steam-api":
        from lib.publishing_steam import set_api_key_from_panel

        set_api_key_from_panel(manager, values["key"])
    elif action == "beta-prepare":
        from lib.publishing_steam import prepare_beta

        prepare_beta(manager, values["run"], values["branch"])
    elif action == "beta-promote":
        from lib.publishing_steam import queue_beta

        queue_beta(manager, values["release"])
        manager.start_worker()
    elif action == "promote-itch":
        manager.promote_itch(values["artifact"], values["destination"])
        manager.start_worker()
    elif action == "login":
        state.publishing_auth.begin(values["provider"], values.get("username", ""))
    elif action == "login-input":
        state.publishing_auth.respond(values["id"], values["response"])
    elif action == "login-cancel":
        state.publishing_auth.cancel(values["id"])
    elif action == "logout":
        if values["confirmation"] != "remove":
            raise ValueError("Confirm removal")
        state.publishing_auth.logout(values["provider"])
    elif action == "project":
        manager.save_project(values["id"], values["repository"], values["provider"], values["target"], depot=values.get("depot", ""), username=values.get("username", ""), record=values["record"])
    elif action == "prepare":
        manager.prepare(values["project"])
    elif action == "upload":
        manager.upload(values["artifact"])
        manager.start_worker()
    elif action == "artifact-review":
        manager.authorize_artifact_text(values["artifact"], values["path"], values["draft"])
    elif action == "artifact-remove":
        manager.remove_artifact(values["artifact"])
    elif action == "cancel":
        manager.cancel(values["run"])
    elif action == "reconcile":
        manager.reconcile_from_panel(values["run"], values["outcome"], values["receipt"], principal)
    elif action == "release":
        manager.create_release(values["run"], values["branch"])
    elif action == "release-confirm":
        manager.confirm_release_from_panel(values["release"], values["build_id"], principal)
    elif action == "schedule":
        manager.schedule(values["project"], int(values["interval"]))
    elif action == "job":
        manager.job_action(values["job"], values["action"])
    elif action == "writing-task":
        return manager.writing_task(values["project"], values["language"], values["instructions"], source=values.get("source", ""))
    elif action == "draft":
        manager.draft(values["project"], values["language"], values["title"], values["body"], source=values.get("source", ""),
                      replaces=values.get("replaces", ""), release=values.get("release", ""), publish_at=utc_instant(values.get("publish_at", "")), late_minutes=int(values["late_minutes"]))
    elif action == "review":
        if values["decision"] not in {"approve", "changes"}:
            raise ValueError("Choose a review decision")
        manager.review_from_panel(values["draft"], values["hash"], principal, approve=values["decision"] == "approve")
    elif action == "export":
        return manager.export(values["draft"])
    elif action == "handoff":
        manager.export(values["draft"], dispatch=True)
    elif action == "post-confirm":
        if values["confirmation"] != "reviewed":
            raise ValueError("Confirm final provider text review")
        manager.confirm_post_from_panel(values["draft"], values["url"], principal)
    return None


_STYLE = '''
.publishing-card {padding:18px; margin:12px 0; background:var(--panel); border:1px solid var(--line); border-radius:12px;}
.publishing-card h3 {margin-top:0;}
form {margin:12px 0; display:flex; flex-wrap:wrap; align-items:end; gap:12px;}
label {display:block; flex:1 1 200px; font-weight:650;}
input,textarea {display:block; width:100%; min-height:44px; margin-top:6px; padding:8px; background:var(--panel); color:var(--text); border:1px solid var(--line); border-radius:8px; font:inherit;}
textarea {min-width:260px;}
.publishing-comparison {display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); gap:16px;}
.publishing-comparison pre {max-height:32rem;}
'''
