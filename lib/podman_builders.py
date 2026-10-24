"""Bounded metadata checks for disposable Buildah working containers."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import NoReturn

from lib.maintenance_defaults import PODMAN_KEEP_LABEL


_IDENTIFIER = re.compile(r"[0-9a-f]{64}\Z")
_INVENTORY_LIMIT = 10_000
_INSPECTION_LIMIT = 32


class BuilderObservationError(Exception):
    """A bounded builder observation was unavailable or malformed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _raise(code: str) -> NoReturn:
    raise BuilderObservationError(code)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise BuilderObservationError("invalid_identifier")
    return value


def _json_output(result: subprocess.CompletedProcess[str], code: str) -> object:
    if not isinstance(result, subprocess.CompletedProcess) or result.returncode != 0:
        _raise(code)
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError, RecursionError):
        _raise(code)


def _run_json(
    callback: Callable[[list[str]], subprocess.CompletedProcess[str]],
    argv: list[str],
    code: str,
) -> object:
    try:
        result = callback(argv)
    except Exception:
        _raise(code)
    return _json_output(result, code)


def _inspect(
    buildah_run: Callable[[list[str]], subprocess.CompletedProcess[str]] | None,
    identifier: str,
) -> bool:
    """Return whether a fresh Buildah inspect marks the builder removable."""
    identifier = _identifier(identifier)
    if buildah_run is None:
        return False
    payload = _run_json(
        buildah_run,
        ["inspect", "--type", "container", identifier],
        "buildah_inspect_failed",
    )
    if isinstance(payload, list):
        if len(payload) != 1:
            _raise("invalid_builder_metadata")
        payload = payload[0]
    if not isinstance(payload, dict):
        _raise("invalid_builder_metadata")

    if payload.get("Type") != "buildah 0.0.1":
        _raise("invalid_builder_metadata")
    if _identifier(payload.get("ContainerID")) != identifier:
        _raise("invalid_builder_metadata")
    mount_point = payload.get("MountPoint")
    if not isinstance(mount_point, str):
        _raise("invalid_builder_metadata")

    labels: list[dict] = []
    try:
        docker_labels = payload["Docker"]["config"].get("Labels")
        oci_labels = payload["OCIv1"]["config"].get("Labels")
    except (KeyError, TypeError, AttributeError):
        _raise("invalid_builder_metadata")
    for value in (docker_labels, oci_labels):
        if value is None:
            continue
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            _raise("invalid_builder_metadata")
        labels.append(value)
    if mount_point != "" or any(PODMAN_KEEP_LABEL in item for item in labels):
        return False
    return True


def builder_is_removable(
    buildah_run: Callable[[list[str]], subprocess.CompletedProcess[str]] | None,
    identifier: str,
) -> bool:
    """Recheck one full builder ID with a fresh, fail-closed inspect."""
    return _inspect(buildah_run, identifier)


def _store_matches(info: object, store: dict) -> bool:
    if not isinstance(info, dict):
        _raise("invalid_buildah_info")
    host, observed = info.get("host"), info.get("store")
    if not isinstance(host, dict) or host.get("rootless") is not True:
        _raise("invalid_buildah_info")
    if not isinstance(observed, dict):
        _raise("invalid_buildah_info")
    expected_keys = ("graphRoot", "runRoot", "graphDriverName")
    buildah_keys = ("GraphRoot", "RunRoot", "GraphDriverName")
    for expected_key, observed_key in zip(expected_keys, buildah_keys, strict=True):
        expected = store.get(expected_key)
        actual = observed.get(observed_key)
        if not isinstance(expected, str) or not expected or not isinstance(actual, str) or not actual:
            _raise("invalid_store_metadata")
        if actual != expected:
            _raise("buildah_store_mismatch")
    return True


def plan_builders(
    podman_run: Callable[[list[str]], subprocess.CompletedProcess[str]],
    buildah_run: Callable[[list[str]], subprocess.CompletedProcess[str]] | None,
    store: dict,
    cutoff: int,
) -> dict:
    """Identify a bounded set of old, unmounted, unpinned Buildah builders.

    Callbacks own their command deadline and environment. This function only
    observes metadata and never removes a container.
    """
    if type(cutoff) is not int or cutoff < 0:
        _raise("invalid_cutoff")
    if not isinstance(store, dict):
        _raise("invalid_store_metadata")
    retained_counts: dict[str, int] = {}

    def retain(reason: str, count: int = 1) -> None:
        retained_counts[reason] = retained_counts.get(reason, 0) + count

    podman_rows = _run_json(
        podman_run,
        ["ps", "--all", "--external", "--format", "json"],
        "podman_inventory_failed",
    )
    if not isinstance(podman_rows, list) or len(podman_rows) > _INVENTORY_LIMIT:
        _raise("invalid_podman_inventory")

    old_storage: list[str] = []
    for row in podman_rows:
        if not isinstance(row, dict) or not isinstance(row.get("State"), str):
            _raise("invalid_podman_inventory")
        if row["State"] != "storage":
            retain("ordinary")
            continue
        created = row.get("Created")
        if type(created) is not int or created < 0:
            _raise("invalid_podman_inventory")
        identifier = _identifier(row.get("Id", row.get("ID")))
        if created >= cutoff:
            retain("young")
        else:
            if identifier not in old_storage:
                old_storage.append(identifier)

    if not old_storage:
        return {"candidate_ids": [], "retained_counts": retained_counts}

    if buildah_run is None:
        retain("tool_unavailable", len(old_storage))
        return {"candidate_ids": [], "retained_counts": retained_counts}

    buildah_rows = _run_json(
        buildah_run,
        ["containers", "--json"],
        "buildah_inventory_failed",
    )
    if not isinstance(buildah_rows, list) or len(buildah_rows) > _INVENTORY_LIMIT:
        _raise("invalid_buildah_inventory")
    builders: set[str] = set()
    for row in buildah_rows:
        if not isinstance(row, dict) or type(row.get("builder")) is not bool:
            _raise("invalid_buildah_inventory")
        identifier = _identifier(row.get("id"))
        if row["builder"]:
            builders.add(identifier)

    # Buildah's default is JSON; --format accepts a Go template, not 'json'.
    info = _run_json(buildah_run, ["info"], "buildah_info_failed")
    _store_matches(info, store)

    candidates: list[str] = []
    inspected = 0
    for identifier in old_storage:
        if identifier not in builders:
            retain("unknown")
            continue
        if inspected >= _INSPECTION_LIMIT:
            retain("inspection_limit")
            continue
        inspected += 1
        if _inspect(buildah_run, identifier):
            candidates.append(identifier)
        else:
            # A valid but mounted or pinned builder is retained.
            retain("protected")

    return {"candidate_ids": candidates, "retained_counts": retained_counts}
