"""Deterministic VM-local build publishing and reviewed editorial workflows.

No operation accepts shell commands, passwords, repository VDFs, or imported
approval flags. Steam default releases and provider post editors are handoffs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse

from lib.atomic_io import write_text_atomic
from lib.project_manifest import load_manifest
from lib.publishing_artifacts import checked_directory, relative_path, scan, snapshot
from lib.publishing_languages import language_tag, parse_publishing
from lib.publishing_store import PublishingStore, digest, identifier, now, private_directory, safe_text
from lib.validators import validate_username

PROVIDERS = ("butler", "steamcmd")
POST_CAPABILITIES = {"steamcmd": "human-editor-handoff", "butler": "human-editor-handoff"}


def provider(value: str) -> str:
    if value not in PROVIDERS:
        raise ValueError("Select butler or steamcmd")
    return value


def target(value: str, service: str) -> str:
    safe_text(value, limit=160)
    pattern = r"[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*:[a-z0-9][a-z0-9_-]*" if service == "butler" else r"[1-9][0-9]{0,9}"
    if not re.fullmatch(pattern, value):
        raise ValueError("Use owner/game:channel for itch.io or a numeric Steam AppID")
    return value


def project_languages(project: dict) -> dict:
    manifest = load_manifest(project["repository"])
    return parse_publishing(manifest.publishing if manifest else {})["languages"]


def project_revision(project: dict) -> str:
    return digest({"project": project, "languages": project_languages(project)})


def editor_link(project: dict) -> str:
    if project["provider"] == "steamcmd":
        return "https://partner.steamgames.com/apps/landing/" + project["target"]
    owner_game = project["target"].split(":", 1)[0]
    return "https://" + owner_game.replace("/", ".itch.io/", 1) + "/devlog"


class Publishing:
    def __init__(self, home: str | None = None):
        self.store = PublishingStore(home)
        self.home = self.store.home

    def status(self) -> dict:
        """Local saved state only: no subprocesses or provider requests."""
        result = self.store.snapshot()
        result["tools"] = []
        for name in PROVIDERS:
            binary = shutil.which(name, path=f"{self.home}/.local/bin:/usr/local/bin:/usr/bin:/bin")
            result["tools"].append({"provider": name, "installed": bool(binary), "path": binary,
                                    "post_delivery": POST_CAPABILITIES[name]})
        for project in result["projects"]:
            try:
                project["languages"] = project_languages(project)
                project["language_coverage"] = "English and Spanish structural coverage; provider rendering needs live qualification"
            except (ValueError, OSError):
                project["languages"] = None
        return result

    def save_project(self, project_id: str, repository: str, service: str, destination: str,
                     *, depot: str = "", username: str = "", record: str = ".basaltwater/publishing-complete.json") -> dict:
        service = provider(service)
        project = {"id": identifier(project_id), "repository": str(checked_directory(repository)),
                   "provider": service, "target": target(destination, service), "record": relative_path(record)}
        if service == "steamcmd":
            validate_username(username)
            if not re.fullmatch(r"[A-Za-z0-9_]{2,64}", username):
                raise ValueError("Invalid Steam account name")
            project.update(depot=target(depot, service), username=username)
        project_languages(project)
        with self.store.transaction() as db:
            return self.store.put(db, "projects", project)

    def prepare(self, project_id: str) -> dict:
        with self.store.transaction() as db:
            project = self.store.get(db, "projects", project_id)
        artifact_id = self.store.new_id()
        directory = private_directory(self.store.root / "artifacts") / artifact_id
        artifact = snapshot(project["repository"], project["record"], directory)
        artifact.update(id=artifact_id, project=project_id, project_revision=project_revision(project), created=now())
        # Common public release-note files require a separately reviewed exact
        # revision. Other in-game writing remains the project's responsibility.
        artifact["public_text"] = [entry["path"] for entry in artifact["entries"]
                                   if Path(entry["path"]).name.lower().startswith(("changelog", "release-notes", "patch-notes"))]
        with self.store.transaction() as db:
            self.store.put(db, "artifacts", artifact)
        return artifact

    def draft(self, project_id: str, language: str, title: str, body: str, *, source: str = "",
              replaces: str = "", release: str = "", publish_at: float = 0, late_minutes: int = 60) -> dict:
        """Import supplied writing/translation as immutable, unreviewed text."""
        safe_text(title, limit=200)
        if not isinstance(body, str) or not body.strip() or len(body.encode()) > 64 * 1024 or "\x00" in body:
            raise ValueError("Body must contain 1–65536 UTF-8 bytes without NUL")
        if type(late_minutes) is not int or not 1 <= late_minutes <= 1440:
            raise ValueError("Publication lateness must be 1–1440 minutes")
        if type(publish_at) not in (int, float) or publish_at < 0 or publish_at > 32503680000:
            raise ValueError("Invalid publication UTC instant")
        with self.store.transaction() as db:
            project = self.store.get(db, "projects", project_id)
            language = language_tag(language)
            languages = project_languages(project)
            if language not in languages["supported"]:
                raise ValueError("Language is not supported by the project manifest")
            if source:
                original = self.store.get(db, "drafts", source)
                if original["project"] != project_id or original["language"] != languages["source"] or original["state"] in {"superseded", "stale"}:
                    raise ValueError("Translation must reference the current source-language draft")
            elif language != languages["source"]:
                raise ValueError("Translations must name their source revision")
            if release:
                gate = self.store.get(db, "releases", release)
                if gate["project"] != project_id:
                    raise ValueError("Release belongs to another project")
            record = {"id": self.store.new_id(), "project": project_id, "project_revision": project_revision(project),
                      "language": language, "title": title, "body": body, "source": source, "release": release,
                      "publish_at": publish_at, "late_minutes": late_minutes, "created": now(),
                      "destination": project["target"], "provider": project["provider"], "format": "plain-text", "state": "needs-review"}
            record["hash"] = digest(record)
            if replaces:
                old = self.store.get(db, "drafts", replaces)
                if old["project"] != project_id or old["language"] != language:
                    raise ValueError("Replacement must have the same project and language")
                self._revoke(db, old, "superseded")
                for translation in self.store.records(db, "drafts"):
                    if translation["source"] == replaces:
                        self._revoke(db, translation, "stale")
            return self.store.put(db, "drafts", record)

    def _revoke(self, db, draft: dict, state: str) -> None:
        db.execute("DELETE FROM reviews WHERE revision=?", (draft["id"],))
        draft["state"] = state
        self.store.put(db, "drafts", draft)

    def review_from_panel(self, draft_id: str, expected_hash: str, principal: str, *, approve: bool) -> None:
        """Only called by the authenticated human panel route, never the CLI.

        The VM account/root can alter this database; this is a workflow boundary,
        not isolation from unrestricted code executing as the panel account.
        """
        safe_text(principal, limit=80)
        with self.store.transaction() as db:
            draft = self.store.get(db, "drafts", draft_id)
            self._current_draft(db, draft)
            if expected_hash != draft["hash"]:
                raise ValueError("Review form is stale; review the exact current revision")
            if approve:
                db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?)", (draft_id, expected_hash, principal, now()))
                draft["state"] = "approved"
                self.store.put(db, "drafts", draft)
            else:
                self._revoke(db, draft, "changes-requested")

    def _current_draft(self, db, draft: dict) -> dict:
        project = self.store.get(db, "projects", draft["project"])
        if draft["project_revision"] != project_revision(project) or draft["state"] in {"superseded", "stale", "withdrawn"}:
            raise ValueError("Draft configuration/source is stale; create and review a new revision")
        if draft["source"]:
            source = self.store.get(db, "drafts", draft["source"])
            if source["state"] in {"superseded", "stale", "withdrawn"}:
                raise ValueError("Translation source is stale")
        return project

    def _approved(self, db, draft: dict) -> dict:
        project = self._current_draft(db, draft)
        row = db.execute("SELECT hash FROM reviews WHERE revision=?", (draft["id"],)).fetchone()
        if not row or row[0] != draft["hash"]:
            raise ValueError("Human review is required for this exact destination/language revision")
        return project

    def export(self, draft_id: str, *, dispatch: bool = False) -> dict:
        """Produce the exact reviewed text; never claim remote publication."""
        with self.store.transaction() as db:
            draft = self.store.get(db, "drafts", draft_id)
            project = self._approved(db, draft)
            if dispatch:
                if draft["state"] not in {"approved", "awaiting-editor"}:
                    raise ValueError("This revision has already been published or is held")
                current = now()
                if draft["publish_at"] and not draft["publish_at"] <= current <= draft["publish_at"] + draft["late_minutes"] * 60:
                    raise ValueError("Publication is outside its reviewed time window")
                if draft["release"] and self.store.get(db, "releases", draft["release"])["state"] not in {"operator-confirmed", "verified"}:
                    raise ValueError("Release gate awaits completion")
                draft["state"] = "awaiting-editor"
                self.store.put(db, "drafts", draft)
            return {"id": draft_id, "hash": draft["hash"], "language": draft["language"], "title": draft["title"],
                    "body": draft["body"], "format": draft["format"], "editor": editor_link(project),
                    "delivery": POST_CAPABILITIES[project["provider"]], "published": False}

    def confirm_post_from_panel(self, draft_id: str, url: str, principal: str) -> dict:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or len(url) > 2048:
            raise ValueError("Use the HTTPS address of the published post")
        with self.store.transaction() as db:
            draft = self.store.get(db, "drafts", draft_id)
            project = self._approved(db, draft)
            host = "steamcommunity.com" if project["provider"] == "steamcmd" else project["target"].split("/", 1)[0] + ".itch.io"
            expected_path = "/games/" + project["target"] + "/announcements/" if project["provider"] == "steamcmd" else "/" + project["target"].split("/", 1)[1].split(":")[0] + "/devlog/"
            if parsed.hostname != host or not parsed.path.startswith(expected_path) or draft["state"] != "awaiting-editor":
                raise ValueError("Post must await handoff and match the configured game/provider")
            draft.update(state="operator-confirmed", url=url, confirmed_by=safe_text(principal, limit=80), confirmed_at=now())
            return self.store.put(db, "drafts", draft)

    def authorize_artifact_text(self, artifact_id: str, path: str, draft_id: str) -> None:
        with self.store.transaction() as db:
            artifact = self.store.get(db, "artifacts", artifact_id)
            draft = self.store.get(db, "drafts", draft_id)
            self._approved(db, draft)
            entry = next((item for item in artifact["entries"] if item["path"] == path), None)
            if draft["project"] != artifact["project"] or not entry or entry["sha256"] != hashlib.sha256(draft["body"].encode()).hexdigest():
                raise ValueError("Reviewed body must exactly match the bundled public text file")
            artifact.setdefault("text_reviews", {})[path] = draft_id
            self.store.put(db, "artifacts", artifact)

    def _valid_artifact(self, db, artifact: dict) -> dict:
        project = self.store.get(db, "projects", artifact["project"])
        if project_revision(project) != artifact["project_revision"]:
            raise ValueError("Project changed after preparation; prepare again")
        expected = self.store.root / "artifacts" / artifact["id"]
        if artifact["snapshot"] != str(expected):
            raise ValueError("Invalid artifact snapshot")
        if digest(scan(checked_directory(str(expected)))) != artifact["digest"]:
            raise ValueError("Snapshot changed; prepare again")
        for path in artifact["public_text"]:
            review = artifact.get("text_reviews", {}).get(path)
            if not review:
                raise ValueError("Bundled release notes require human review")
            self._approved(db, self.store.get(db, "drafts", review))
        return project

    def upload(self, artifact_id: str, *, job: str = "") -> dict:
        with self.store.transaction() as db:
            artifact = self.store.get(db, "artifacts", artifact_id)
            project = self._valid_artifact(db, artifact)
            identity = digest({"provider": project["provider"], "destination": project["target"],
                               "depot": project.get("depot", ""), "digest": artifact["digest"]})
            previous = db.execute("SELECT run FROM dispatches WHERE identity=?", (identity,)).fetchone()
            if previous:
                return self.store.get(db, "runs", previous[0])
            record = {"id": self.store.new_id(), "project": project["id"], "artifact": artifact_id, "project_config": project,
                      "identity": identity, "state": "queued", "created": now(), "job": job, "operation": "upload"}
            db.execute("INSERT INTO dispatches VALUES(?,?)", (identity, record["id"]))
            return self.store.put(db, "runs", record)

    def cancel(self, run_id: str) -> None:
        with self.store.transaction() as db:
            run = self.store.get(db, "runs", run_id)
            if run["state"] == "queued":
                run["state"] = "cancelled"
                db.execute("DELETE FROM dispatches WHERE identity=?", (run["identity"],))
            elif run["state"] == "running":
                run["cancel_requested"] = True
            else:
                raise ValueError("Run has already finished")
            self.store.put(db, "runs", run)

    def reconcile_from_panel(self, run_id: str, outcome: str, receipt: str, principal: str) -> None:
        if outcome not in {"uploaded", "not-submitted"}:
            raise ValueError("Choose an observed outcome")
        safe_text(receipt, limit=512)
        with self.store.transaction() as db:
            run = self.store.get(db, "runs", run_id)
            if run["state"] not in {"unknown", "uploaded-unverified"}:
                raise ValueError("Run does not need reconciliation")
            run.update(state="operator-confirmed" if outcome == "uploaded" else "not-submitted", receipt=receipt,
                       confirmed_by=safe_text(principal, limit=80), confirmed_at=now())
            self.store.put(db, "runs", run)
            if outcome == "not-submitted":
                db.execute("DELETE FROM dispatches WHERE identity=?", (run["identity"],))

    def create_release(self, run_id: str, branch: str = "default") -> dict:
        safe_text(branch, limit=64)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", branch):
            raise ValueError("Invalid branch")
        with self.store.transaction() as db:
            run = self.store.get(db, "runs", run_id)
            if run["state"] not in {"uploaded", "operator-confirmed"} or not str(run.get("receipt", "")).isdigit():
                raise ValueError("An uploaded, identified Steam BuildID is required")
            project = self.store.get(db, "projects", run["project"])
            if project["provider"] != "steamcmd":
                raise ValueError("For itch.io promotion, configure a target channel and re-upload the retained artifact")
            record = {"id": self.store.new_id(), "project": project["id"], "run": run_id,
                      "build_id": run["receipt"], "branch": branch, "state": "awaiting-steamworks",
                      "link": "https://partner.steamgames.com/apps/builds/" + project["target"], "created": now()}
            return self.store.put(db, "releases", record)

    def confirm_release_from_panel(self, release_id: str, build_id: str, principal: str) -> None:
        with self.store.transaction() as db:
            release = self.store.get(db, "releases", release_id)
            if release["state"] != "awaiting-steamworks" or build_id != release["build_id"]:
                raise ValueError("Confirm the exact BuildID selected on Steamworks")
            release.update(state="operator-confirmed", confirmed_at=now(), confirmed_by=safe_text(principal, limit=80))
            self.store.put(db, "releases", release)

    def schedule(self, project_id: str, interval_minutes: int, *, enabled: bool = True) -> dict:
        if type(interval_minutes) is not int or not 5 <= interval_minutes <= 43200:
            raise ValueError("Polling interval must be 5–43200 minutes")
        with self.store.transaction() as db:
            project = self.store.get(db, "projects", project_id)
            record = {"id": self.store.new_id(), "project": project_id, "project_revision": project_revision(project),
                      "interval": interval_minutes * 60, "next_at": now(), "state": "enabled" if enabled else "paused", "failures": 0}
            return self.store.put(db, "jobs", record)

    def job_action(self, job_id: str, action: str) -> None:
        if action not in {"pause", "resume"}:
            raise ValueError("Choose pause or resume")
        with self.store.transaction() as db:
            job = self.store.get(db, "jobs", job_id)
            if action == "resume":
                project = self.store.get(db, "projects", job["project"])
                job["project_revision"] = project_revision(project)
                job["failures"] = 0
            job["state"] = "paused" if action == "pause" else "enabled"
            self.store.put(db, "jobs", job)

    def tick(self) -> None:
        """Called by the panel's existing scheduler owner, never an LLM task."""
        with self.store.transaction() as db:
            due = [job for job in self.store.records(db, "jobs") if job["state"] == "enabled" and job["next_at"] <= now()]
            for job in due:
                job["next_at"] = now() + job["interval"]
                self.store.put(db, "jobs", job)
        for job in due:
            try:
                with self.store.transaction() as db:
                    current = self.store.get(db, "jobs", job["id"])
                    project = self.store.get(db, "projects", job["project"])
                    if current["state"] != "enabled":
                        continue
                    if job["project_revision"] != project_revision(project):
                        raise ValueError("Project configuration changed")
                    if any(run["project"] == job["project"] and run["state"] in {"queued", "running", "unknown", "uploaded-unverified"} for run in self.store.records(db, "runs")):
                        continue
                artifact = self.prepare(job["project"])
                run = self.upload(artifact["id"], job=job["id"])
                # Duplicate preparations can be discarded after the durable
                # dispatch ledger has selected the existing operation.
                if run["artifact"] != artifact["id"]:
                    shutil.rmtree(artifact["snapshot"])
                    with self.store.transaction() as db:
                        db.execute("DELETE FROM records WHERE kind='artifacts' AND id=?", (artifact["id"],))
            except (OSError, RuntimeError, ValueError):
                with self.store.transaction() as db:
                    current = self.store.get(db, "jobs", job["id"])
                    current["failures"] += 1
                    current["last_error"] = "Preparation or configuration failed; inspect the project and completed artifact"
                    if current["failures"] >= 3:
                        current["state"] = "paused"
                    self.store.put(db, "jobs", current)
        with self.store.transaction() as db:
            due_posts = [draft["id"] for draft in self.store.records(db, "drafts") if draft["state"] == "approved" and draft["publish_at"] and draft["publish_at"] <= now()]
        for draft_id in due_posts:
            try:
                self.export(draft_id, dispatch=True)
            except ValueError:
                # Keep release-gated posts held; expire late ones explicitly.
                with self.store.transaction() as db:
                    draft = self.store.get(db, "drafts", draft_id)
                    if now() > draft["publish_at"] + draft["late_minutes"] * 60:
                        draft["state"] = "window-expired"
                        self.store.put(db, "drafts", draft)

    def start_worker(self) -> None:
        """Detached worker survives a panel restart; flock prevents duplicates."""
        subprocess.Popen([sys.executable, "-m", "lib.publishing_worker", "--home", str(self.home)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(Path(__file__).resolve().parent.parent), close_fds=True)
