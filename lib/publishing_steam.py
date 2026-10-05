"""Optional Steam beta promotion with fresh observations and no default path."""

from __future__ import annotations

import http.client
import json
import os
import re
import urllib.parse

from lib.atomic_io import write_text_atomic
from lib.publishing import Publishing, project_revision
from lib.publishing_store import digest, file_lock, now, private_directory, private_file

MAX_RESPONSE = 1024 * 1024


def beta_branch(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) or value.lower() in {"public", "default"}:
        raise ValueError("Automatic promotion requires an explicit non-default beta branch")
    return value


def set_api_key_from_panel(publishing: Publishing, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{32}", value):
        raise ValueError("Invalid Steam publisher API key")
    with file_lock(private_directory(publishing.store.root) / "steamcmd.lock"):
        path = publishing.store.root / "steam-api.key"
        private_file(path)
        write_text_atomic(str(path), value, mode=0o600)


def request(publishing: Publishing, method: str, fields: dict, *, mutate: bool = False) -> dict:
    allowed = {"GetAppBetas", "GetAppBuilds", "SetAppBuildLive"}
    if method not in allowed:
        raise ValueError("Unsupported Steam operation")
    if mutate != (method == "SetAppBuildLive"):
        raise ValueError("Invalid Steam request mode")
    allowed_fields = {"appid", "buildid", "betakey"} if mutate else {"appid", "count"} if method == "GetAppBuilds" else {"appid"}
    if set(fields) - allowed_fields or "appid" not in fields:
        raise ValueError("Unsupported Steam request fields")
    for name in ("appid", "buildid", "count"):
        if name in fields and (not re.fullmatch(r"[1-9][0-9]{0,9}", str(fields[name])) or int(fields[name]) > 4294967295):
            raise ValueError("Invalid Steam numeric identifier")
    if method == "SetAppBuildLive":
        beta_branch(fields.get("betakey"))
        if set(fields) != {"appid", "buildid", "betakey"}:
            raise ValueError("Steam promotion accepts only app, build and beta branch")
    path = publishing.store.root / "steam-api.key"
    private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="ascii") as source:
        key = source.read(33)
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", key):
        raise ValueError("Steam publisher API key is unavailable")
    encoded = urllib.parse.urlencode({"key": key, **fields})
    endpoint = f"/ISteamApps/{method}/{'v2' if mutate else 'v1'}/"
    connection = http.client.HTTPSConnection("partner.steam-api.com", timeout=30)
    try:
        if mutate:
            connection.request("POST", endpoint, body=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"})
        else:
            connection.request("GET", endpoint + "?" + encoded)
        response = connection.getresponse()
        payload = response.read(MAX_RESPONSE + 1)
        # No redirects, response reflection, URL logging, or default confirmation.
        if response.status != 200 or len(payload) > MAX_RESPONSE:
            raise RuntimeError("Steam response unavailable; inspect the operation before retrying")
        value = json.loads(payload)
        if not isinstance(value, dict) or not isinstance(value.get("response"), dict):
            raise ValueError("Unsupported Steam response schema")
        return value["response"]
    finally:
        connection.close()


def branches(response: dict) -> dict[str, dict]:
    values = response.get("betas")
    if isinstance(values, dict):
        if any(not isinstance(value, dict) for value in values.values()):
            raise ValueError("Invalid Steam branch response")
        values = [dict(value, name=name) for name, value in values.items()]
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError("Unsupported Steam branch response; use a Steamworks handoff")
    result = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise ValueError("Invalid Steam branch response")
        name = value["name"]
        build_id = str(value.get("buildid", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not build_id.isascii() or not build_id.isdigit() or name in result:
            raise ValueError("Invalid Steam branch observation")
        if "isdefault" in value and type(value["isdefault"]) is not bool:
            raise ValueError("Unsupported Steam default-branch indicator")
        result[name] = {"build_id": build_id, "default": name.lower() in {"public", "default"} or value.get("isdefault") is True}
    if "public" not in result:
        raise ValueError("Default branch identity cannot be verified; use Steamworks")
    return result


def observe(publishing: Publishing, project: dict, branch: str, build_id: str) -> dict:
    branch = beta_branch(branch)
    observed = branches(request(publishing, "GetAppBetas", {"appid": project["target"]}))
    selected = observed.get(branch)
    if not selected or selected["default"]:
        raise ValueError("Selected branch is unavailable or aliases the default branch")
    builds = request(publishing, "GetAppBuilds", {"appid": project["target"], "count": 100}).get("builds")
    if isinstance(builds, dict):
        builds = list(builds.values())
    if not isinstance(builds, list) or not any(isinstance(item, dict) and str(item.get("buildid")) == build_id for item in builds):
        raise ValueError("BuildID does not belong to this app's observed build history")
    return {"previous_build": selected["build_id"], "observed_at": now(), "audience": "unknown"}


def prepare_beta(publishing: Publishing, run_id: str, branch: str) -> dict:
    branch = beta_branch(branch)
    with publishing.store.transaction() as db:
        run = publishing.store.get(db, "runs", run_id)
        project = publishing.store.get(db, "projects", run["project"])
        if project["provider"] != "steamcmd" or run["state"] not in {"uploaded", "operator-confirmed"} or not str(run.get("receipt", "")).isdigit():
            raise ValueError("Select an identified uploaded Steam build")
        if run["operation"] != "upload" or run["project_config"] != project:
            raise ValueError("Select an upload for the current destination configuration")
    with file_lock(private_directory(publishing.store.root) / "steamcmd.lock"):
        observation = observe(publishing, project, branch, run["receipt"])
    release = {"id": publishing.store.new_id(), "project": project["id"], "project_revision": project_revision(project),
               "run": run_id, "build_id": run["receipt"], "branch": branch, "state": "prepared-beta", **observation,
               "link": "https://partner.steamgames.com/apps/builds/" + project["target"], "created": now()}
    with publishing.store.transaction() as db:
        return publishing.store.put(db, "releases", release)


def queue_beta(publishing: Publishing, release_id: str) -> dict:
    with publishing.store.transaction() as db:
        release = publishing.store.get(db, "releases", release_id)
        beta_branch(release["branch"])
        project = publishing.store.get(db, "projects", release["project"])
        if release["state"] != "prepared-beta" or now() - release["observed_at"] > 300 or release["project_revision"] != project_revision(project):
            raise ValueError("Promotion preparation is stale; observe and prepare again")
        identity = digest({"release": release_id, "build": release["build_id"], "branch": release["branch"]})
        old = db.execute("SELECT run FROM dispatches WHERE identity=?", (identity,)).fetchone()
        if old:
            return publishing.store.get(db, "runs", old[0])
        run = {"id": publishing.store.new_id(), "project": project["id"], "project_config": project, "release": release_id,
               "identity": identity, "state": "queued", "created": now(), "job": "", "operation": "promote-beta"}
        db.execute("INSERT INTO dispatches VALUES(?,?)", (identity, run["id"]))
        release["state"] = "promotion-queued"
        publishing.store.put(db, "releases", release)
        return publishing.store.put(db, "runs", run)


def execute_beta(publishing: Publishing, run: dict) -> dict:
    """Called under the provider lease, with durable running intent already saved."""
    with publishing.store.transaction() as db:
        release = publishing.store.get(db, "releases", run["release"])
        project = publishing.store.get(db, "projects", release["project"])
    branch = beta_branch(release["branch"])
    dispatched = False
    def preflight_failure(message: str) -> dict:
        with publishing.store.transaction() as db:
            release.update(state="preflight-failed", finished=now())
            publishing.store.put(db, "releases", release)
        return {"state": "preflight-failed", "message": message, "finished": now()}

    try:
        with publishing.store.transaction() as db:
            uploaded = publishing.store.get(db, "runs", release["run"])
            artifact = publishing.store.get(db, "artifacts", uploaded["artifact"])
            publishing._valid_artifact(db, artifact, public=True)
        observed = observe(publishing, project, branch, release["build_id"])
        if (release["project_revision"] != project_revision(project) or now() - release["observed_at"] > 300
                or observed["previous_build"] != release["previous_build"]):
            return preflight_failure("Branch changed or preparation expired; prepare promotion again")
        with publishing.store.transaction() as db:
            cancelled = publishing.store.get(db, "runs", run["id"]).get("cancel_requested")
        if cancelled:
            return preflight_failure("Cancelled before promotion dispatch")
        dispatched = True
        request(publishing, "SetAppBuildLive", {"appid": project["target"], "buildid": release["build_id"], "betakey": branch}, mutate=True)
        after = branches(request(publishing, "GetAppBetas", {"appid": project["target"]})).get(branch)
        verified = bool(after and not after["default"] and after["build_id"] == release["build_id"])
    except (OSError, RuntimeError, ValueError, http.client.HTTPException):
        if not dispatched:
            return preflight_failure("Cannot validate the beta destination; inspect Steamworks and prepare again")
        verified = False
    with publishing.store.transaction() as db:
        release.update(state="verified" if verified else "unknown", finished=now())
        publishing.store.put(db, "releases", release)
    return {"state": "promoted" if verified else "unknown", "receipt": release["build_id"], "finished": now(),
            "message": "Beta branch promotion verified" if verified else "Promotion outcome needs Steamworks reconciliation"}
