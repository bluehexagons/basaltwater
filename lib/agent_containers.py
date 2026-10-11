"""Opt-in local container storage diagnostics; never prune or start workloads."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import tempfile

from lib.agent_probe import run_probe
from lib.validation import validate_filesystem_path


ENGINES = ("podman", "docker")
_ISSUES = frozenset(("engine_missing", "local_socket_missing", "remote_selection_unsupported",
                     "probe_unavailable", "probe_failed", "probe_timeout", "probe_output_limit",
                     "invalid_engine_data", "filesystem_unavailable", "disk_usage_unavailable"))
_COUNTS = ("image_count", "referenced_image_count", "container_count", "running_container_count",
           "image_bytes", "container_bytes", "volume_bytes", "build_cache_bytes", "reclaimable_estimate_bytes")
_FILESYSTEM = ("total_bytes", "used_bytes", "free_bytes", "usage_percent")
_PODMAN_INFO = ('{"rootless":{{.Host.Security.Rootless}},"remote":{{.Host.ServiceIsRemote}},'
                '"graph_root":{{json .Store.GraphRoot}},"containers":{{.Store.ContainerStore.Number}},'
                '"running":{{.Store.ContainerStore.Running}}}')
_DOCKER_INFO = ('{"graph_root":{{json .DockerRootDir}},"security_options":{{json .SecurityOptions}},'
                '"containers":{{.Containers}},"running":{{.ContainersRunning}}}')


def _integer(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _size(value: object) -> int | None:
    if (number := _integer(value)) is not None:
        return number
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)(?: \([0-9.]+%\))?", value)
    if not match:
        return None
    unit = match[2]
    exponent = 0 if unit == "B" else "KMGT".index(unit[0].upper()) + 1
    size = float(match[1]) * (1024 if "iB" in unit else 1000) ** exponent
    return int(size) if 0 <= size < 2**63 else None


def _filesystem(graph_root: str, home: str) -> dict:
    validate_filesystem_path(graph_root, must_exist=True)
    if not os.path.isabs(graph_root) or not os.path.isdir(graph_root):
        raise ValueError("Container graph root is unavailable")
    disk = shutil.disk_usage(graph_root)
    if disk.total <= 0:
        raise ValueError("Container filesystem capacity is unavailable")
    device = os.stat(graph_root).st_dev
    def same_device(path: str) -> bool | None:
        try:
            return device == os.stat(path).st_dev
        except OSError:
            return None
    return {"available": True, "total_bytes": disk.total, "used_bytes": disk.used,
            "free_bytes": disk.free, "usage_percent": disk.used * 100 // disk.total if disk.total else None,
            "same_filesystem_as_home": same_device(home), "same_filesystem_as_root": same_device("/")}


def inspect_container_engine(engine: str, *, home: str | None = None) -> dict:
    """Inspect only the local account's Podman store or a known local Docker socket."""
    if engine not in ENGINES:
        raise ValueError("Choose podman or docker")
    result = {"engine": engine, "available": False, "rootless": None, "graph_root": None,
              "filesystem": {"available": False}, "issues": [], **dict.fromkeys(_COUNTS),
              "reclaimable_is_estimate": True, "shared_layers_may_overestimate_reclaimable": True}
    executable = shutil.which(engine, path="/usr/local/bin:/usr/bin:/bin")
    if executable is None:
        result["issues"].append("engine_missing")
        return result
    # Never follow a saved remote context or an inherited remote endpoint.
    if any(os.environ.get(key) for key in (
        ("CONTAINER_HOST", "CONTAINER_CONNECTION") if engine == "podman" else ("DOCKER_HOST", "DOCKER_CONTEXT")
    )):
        result["issues"].append("remote_selection_unsupported")
        return result
    account_home = home or os.path.expanduser("~")
    environment = dict(os.environ, LC_ALL="C")
    try:
        configuration = tempfile.TemporaryDirectory(prefix="basaltwater-container-probe-")
    except OSError:
        result["issues"].append("probe_unavailable")
        return result
    with configuration as config:
        if engine == "podman":
            prefix = [executable, "--remote=false"]
        else:
            socket = f"/run/user/{os.geteuid()}/docker.sock"
            if not os.path.exists(socket):
                socket = "/var/run/docker.sock"
            try:
                if not stat.S_ISSOCK(os.stat(socket).st_mode):
                    raise ValueError("Not a socket")
            except (OSError, ValueError):
                result["issues"].append("local_socket_missing")
                return result
            # Empty config excludes credential helpers and the user's saved contexts.
            prefix = [executable, "--config", config, "--host", "unix://" + socket]
        info = run_probe([*prefix, "info", "--format", _PODMAN_INFO if engine == "podman" else _DOCKER_INFO])
        if info.issue:
            result["issues"].append(info.issue)
            return result
        try:
            value = json.loads(info.output)
            if not isinstance(value, dict) or not isinstance(value.get("graph_root"), str):
                raise ValueError("Missing graph root")
            if value.get("remote") is True:
                result["issues"].append("remote_selection_unsupported")
                return result
            result["available"] = True
            result["graph_root"] = value["graph_root"]
            if engine == "podman":
                result["rootless"] = value.get("rootless") if type(value.get("rootless")) is bool else None
            elif isinstance(value.get("security_options"), list):
                result["rootless"] = "name=rootless" in value["security_options"]
            result["container_count"] = _integer(value.get("containers"))
            result["running_container_count"] = _integer(value.get("running"))
        except (ValueError, RecursionError):
            result["issues"].append("invalid_engine_data")
            return result
        try:
            result["filesystem"] = _filesystem(value["graph_root"], account_home)
        except (OSError, ValueError):
            result["issues"].append("filesystem_unavailable")
        usage = run_probe([*prefix, "system", "df", "--format", "json" if engine == "podman" else "{{json .}}"])
        if usage.issue:
            result["issues"].append(usage.issue)
            return result
        try:
            rows = json.loads(usage.output) if engine == "podman" else [json.loads(line) for line in usage.output.splitlines() if line.strip()]
            if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
                raise ValueError("Invalid disk usage")
            totals = {}
            reclaimable = []
            for row in rows:
                kind = {"Images": "image_bytes", "Containers": "container_bytes",
                        "Local Volumes": "volume_bytes", "Build Cache": "build_cache_bytes"}.get(row.get("Type"))
                if kind is None:
                    continue
                if kind in totals:
                    raise ValueError("Duplicate disk usage category")
                totals[kind] = _size(row.get("RawSize") if engine == "podman" else row.get("Size"))
                reclaimable.append(_size(row.get("RawReclaimable") if engine == "podman" else row.get("Reclaimable")))
                if kind == "image_bytes":
                    for field, key in (("image_count", "TotalCount"), ("referenced_image_count", "Active")):
                        raw = row.get(key, row.get("Total")) if key == "TotalCount" else row.get(key)
                        result[field] = _integer(int(raw)) if isinstance(raw, str) and raw.isdecimal() and len(raw) < 20 else _integer(raw)
            result.update(totals)
            if not totals or any(size is None for size in totals.values()):
                result["issues"].append("disk_usage_unavailable")
            if reclaimable and all(size is not None for size in reclaimable):
                result["reclaimable_estimate_bytes"] = _integer(sum(reclaimable))
        except (ValueError, TypeError, RecursionError, OverflowError):
            result["issues"].append("invalid_engine_data")
    return result


def inspect_container_storage(*, home: str | None = None, engines: list[str] | None = None) -> dict:
    return {"requested": True, "engines": [inspect_container_engine(engine, home=home)
                                            for engine in dict.fromkeys(engines or ENGINES)]}


def sanitize_container_storage(value: object) -> dict:
    """Allow only aggregate metrics and fixed issue codes into shareable evidence."""
    storage = value if isinstance(value, dict) else {}
    records = []
    for record in storage.get("engines", []) if isinstance(storage.get("engines"), list) else []:
        if not isinstance(record, dict) or record.get("engine") not in ENGINES:
            continue
        filesystem = record.get("filesystem") if isinstance(record.get("filesystem"), dict) else {}
        records.append({"engine": record["engine"], "available": record.get("available") is True,
                        "rootless": record.get("rootless") if type(record.get("rootless")) is bool else None,
                        **{key: _integer(record.get(key)) for key in _COUNTS},
                        "filesystem": {"available": filesystem.get("available") is True,
                                       **{key: _integer(filesystem.get(key)) for key in _FILESYSTEM},
                                       **{key: filesystem.get(key) if type(filesystem.get(key)) is bool else None
                                          for key in ("same_filesystem_as_home", "same_filesystem_as_root")}},
                        "reclaimable_is_estimate": True, "shared_layers_may_overestimate_reclaimable": True,
                        "issues": [issue for issue in record.get("issues", []) if isinstance(issue, str) and issue in _ISSUES]
                        if isinstance(record.get("issues"), list) else []})
    return {"requested": storage.get("requested") is True, "engines": records}


def container_preflight(record: dict, *, image_bytes: int, additional_copies: int = 1,
                        reserve_bytes: int = 1024**3) -> dict:
    """Compare an explicit uncompressed image estimate with graph-root headroom."""
    if type(image_bytes) is not int or not 0 < image_bytes <= 2**60:
        raise ValueError("image-bytes must be a positive integer at most 2**60")
    if type(additional_copies) is not int or not 0 <= additional_copies <= 8:
        raise ValueError("additional-copies must be between 0 and 8")
    if type(reserve_bytes) is not int or not 0 <= reserve_bytes <= 2**60:
        raise ValueError("reserve-bytes must be between 0 and 2**60")
    filesystem = record.get("filesystem", {})
    free = _integer(filesystem.get("free_bytes")) if filesystem.get("available") is True else None
    required = image_bytes * (1 + additional_copies) + reserve_bytes
    return {"status": "unknown" if free is None else "sufficient_estimate" if free >= required else "insufficient",
            "image_bytes": image_bytes, "additional_copies": additional_copies, "reserve_bytes": reserve_bytes,
            "required_estimate_bytes": required, "graph_root_free_bytes": free,
            "guaranteed": False, "uncertainties": ["uncompressed_size_is_user_estimate", "shared_layers",
                "rootless_id_mapped_layer_copies", "temporary_build_outputs", "concurrent_workloads"],
            "reclaimable_counted_as_free": False}


def add_containers_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("containers", help="Read-only local container storage and SDK headroom")
    actions = parser.add_subparsers(dest="agent_containers_command", required=True)
    inspect = actions.add_parser("inspect")
    inspect.add_argument("--engine", choices=ENGINES, action="append")
    inspect.add_argument("--json", action="store_true")
    preflight = actions.add_parser("preflight")
    preflight.add_argument("--engine", choices=ENGINES, required=True)
    preflight.add_argument("--image-bytes", type=int, required=True, help="Estimated uncompressed new image size")
    preflight.add_argument("--additional-copies", type=int, default=1)
    preflight.add_argument("--reserve-bytes", type=int, default=1024**3)
    preflight.add_argument("--json", action="store_true")


def run_containers_command(args: argparse.Namespace) -> int:
    try:
        if args.agent_containers_command == "preflight":
            # Validate assumptions before invoking either engine.
            container_preflight({}, image_bytes=args.image_bytes, additional_copies=args.additional_copies,
                                reserve_bytes=args.reserve_bytes)
            record = inspect_container_engine(args.engine)
            estimate = container_preflight(record, image_bytes=args.image_bytes,
                                            additional_copies=args.additional_copies, reserve_bytes=args.reserve_bytes)
            result = {"storage": record, "preflight": estimate}
            success = estimate["status"] == "sufficient_estimate"
        else:
            result = inspect_container_storage(engines=args.engine)
            success = all(record["available"] and not record["issues"] for record in result["engines"])
        print(json.dumps(result, indent=2))
        return 0 if success else 1
    except OSError:
        print(json.dumps({"ok": False, "issue": "probe_unavailable",
                          "remediation": "Check local temporary-directory capacity and permissions."}))
        return 1
    except ValueError as exc:
        print(json.dumps({"ok": False, "issue": "invalid_capacity_input", "error": str(exc),
                          "remediation": "Provide uncompressed image bytes, 0-8 additional copies, and reserve bytes."}))
        return 1
