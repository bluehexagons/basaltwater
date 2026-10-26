"""Compose admitted prebuilt releases with bounded, scoped route publication.

This is an internal activation callback, not an authentication or admission API.
The caller holds DeploymentAdmissionStore.activate ownership and supplies
current authorized bindings plus a verified-artifact context manager.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
import os
import re
import stat
from typing import Any

from lib.atomic_io import read_json_file, write_bytes_atomic
from lib.build_manifest import DEBIAN_PLATFORM, validate_manifest_request
from lib.build_protocol import encode_document, parse_build_request, request_digest
from lib.build_target_policy import authorize_target_deployment
from lib.cicd_deploy_policy import validate_nginx_path
from lib.deployment import DeploymentOrchestrator
from lib.nginx_config import create_nginx_sites_for_groups
from lib.operation_state import OperationStateStore
from lib.project_manifest import Manifest, parse_manifest
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


MAX_REGISTRY_BYTES = 4 * 1024**2
MAX_APPLICATIONS = 256
_INITIALIZED = b"basaltwater-target-releases-v1\n"
_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def _trusted_directory(path: str, *, private: bool) -> Iterator[int]:
    """Reject links, foreign owners and ancestors permitting path replacement."""
    validate_filesystem_path(path)
    if not isinstance(path, str) or not path.startswith("/") or os.path.normpath(path) != path:
        raise ValueError("Target activation directories must be canonical absolute paths")
    descriptor = os.open("/", _DIRECTORY)
    trusted = {0, os.geteuid()}
    try:
        parent = os.fstat(descriptor)
        for part in path.split("/")[1:]:
            if not part:
                continue
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if info.st_uid not in trusted or parent.st_uid not in trusted:
                    raise ValueError("Target activation directory has an untrusted owner")
                if parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX:
                    raise ValueError("Target activation directory has a writable ancestor")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor, parent = child, info
        if private and (parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700):
            raise ValueError("Target activation private directory must be owned mode 0700")
        if not private and parent.st_mode & 0o022:
            raise ValueError("Target deployment parent must not be writable by other identities")
        yield descriptor
    finally:
        os.close(descriptor)


def _bindings_manifest(bindings: object, request: object, principal: str) -> tuple[dict, Manifest]:
    """Revalidate the trusted projection and retain exact ordered build inputs."""
    if not isinstance(bindings, dict) or set(bindings) != {
        "schema", "principal", "application", "project", "deployment_root",
        "platform", "limits", "components",
    }:
        raise ValueError("Invalid authorized target bindings")
    if bindings["principal"] != principal:
        raise PermissionError("Target bindings belong to another principal")
    components = bindings["components"]
    if not isinstance(components, list):
        raise ValueError("Invalid authorized target components")
    routes = {item["name"]: {key: item[key] for key in ("type", "domain", "path")}
              for item in components if isinstance(item, dict) and "name" in item}
    policy = {"schema": 1, "applications": {bindings["application"]: {
        "project": bindings["project"], "principals": [principal],
        "deployment_root": bindings["deployment_root"], "platform": bindings["platform"],
        "limits": bindings["limits"], "components": routes,
    }}}
    projection = {key: bindings[key] for key in ("schema", "application", "project", "platform", "components")}
    checked = authorize_target_deployment(
        policy, principal=principal, application=bindings["application"],
        request=request, deployment=projection,
    )
    job = parse_build_request(request)
    serving = {item["name"]: item for item in checked["bindings"]["components"]}
    manifest_components = []
    for build in job["components"]:
        component = copy.deepcopy(serving[build["name"]])
        if component.get("health") is None:
            component.pop("health", None)
        component.update(build=build["commands"], env=build["env"])
        if component["type"] == "service":
            # First-slice service convention; the client cannot supply runtime
            # values, arbitrary arguments or secret paths through this projection.
            component.update(port="auto", runtime_env={"PORT": "{{port}}", "HOST": "127.0.0.1"})
        manifest_components.append(component)
    manifest = parse_manifest({"version": 1, "components": manifest_components})
    validate_manifest_request(manifest, job)
    return checked, manifest


def _deployments(value: object, root: str) -> list[dict[str, Any]]:
    """Validate only the supported renderer projection, including saved state."""
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError("Invalid target route inventory")
    result = []
    for item in value:
        base = {"dest_path", "domain", "path", "project_type", "needs_proxy", "serve_path", "backend_port"}
        if not isinstance(item, dict) or type(item.get("needs_proxy")) is not bool:
            raise ValueError("Invalid target route record")
        proxy = item["needs_proxy"]
        expected = base | ({"proxy_port", "preserve_path"} if proxy else {"spa"})
        if set(item) != expected or item["dest_path"] != root:
            raise ValueError("Target route does not match its release root")
        if (not isinstance(item["domain"], str) or not validate_host(item["domain"])
                or item["domain"] != item["domain"].lower().removesuffix(".")):
            raise ValueError("Invalid target route domain")
        validate_nginx_path(item["path"])
        validate_filesystem_path(item["serve_path"])
        serve = item["serve_path"]
        if os.path.normpath(serve) != serve or os.path.commonpath((serve, root)) != root:
            raise ValueError("Target serve path escapes its release")
        if proxy:
            port = item["backend_port"]
            if (item["project_type"] != "service" or type(port) is not int or not 1024 <= port <= 65535
                    or item["proxy_port"] != port or item["preserve_path"] is not True or serve != root):
                raise ValueError("Invalid target proxy route")
        elif (item["project_type"] != "static" or item["backend_port"] is not None
              or type(item["spa"]) is not bool):
            raise ValueError("Invalid target static route")
        result.append(copy.deepcopy(item))
    return result


class TargetReleaseActivator:
    """Activate under admission ownership, then publish all locally owned routes.

    ``open_artifact`` is a trusted context-manager callback accepting operation,
    generation, parsed request, owner, bindings digest and artifact digest. It
    must pin an immutable verified bundle and must not reenter the admission
    store. This class does not authenticate or acquire deployment admission.
    """

    def __init__(self, state_root: str, staging_root: str, *, open_artifact: Callable):
        if not callable(open_artifact):
            raise ValueError("A verified artifact accessor is required")
        for path in (state_root, staging_root):
            with _trusted_directory(path, private=True):
                pass
        if os.path.commonpath((state_root, staging_root)) in {state_root, staging_root}:
            raise ValueError("Target state and verification roots must not overlap")
        self.root, self.staging_root, self.open_artifact = state_root, staging_root, open_artifact
        self.registry = os.path.join(state_root, "releases.json")
        self.witness = os.path.join(state_root, ".initialized")

    def _private_file(self, path: str) -> bool:
        try:
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Unsafe target release state file")
        return True

    def _load(self) -> dict[str, Any]:
        initialized = self._private_file(self.witness)
        present = self._private_file(self.registry)
        if initialized:
            descriptor = os.open(self.witness, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if os.read(descriptor, len(_INITIALIZED) + 1) != _INITIALIZED:
                    raise ValueError("Invalid target release initialization witness")
            finally:
                os.close(descriptor)
            if not present:
                raise ValueError("Initialized target release registry is missing")
        state = read_json_file(self.registry, max_bytes=MAX_REGISTRY_BYTES) if present else {"schema": 1, "applications": {}}
        if (not isinstance(state, dict) or set(state) != {"schema", "applications"}
                or type(state["schema"]) is not int or state["schema"] != 1
                or not isinstance(state["applications"], dict)
                or len(state["applications"]) > MAX_APPLICATIONS):
            raise ValueError("Invalid target release registry")
        for app, record in state["applications"].items():
            if (not isinstance(app, str) or _SLUG.fullmatch(app) is None
                    or not isinstance(record, dict) or set(record) != {
                        "operation_id", "generation", "artifact_sha256", "request_sha256", "root", "deployments",
                    }):
                raise ValueError("Invalid target release registry record")
            if (not isinstance(record["operation_id"], str) or _ID.fullmatch(record["operation_id"]) is None
                    or type(record["generation"]) is not int or record["generation"] < 1
                    or any(not isinstance(record[key], str) or _DIGEST.fullmatch(record[key]) is None
                           for key in ("artifact_sha256", "request_sha256"))):
                raise ValueError("Invalid target release registry identity")
            root = record["root"]
            if (not isinstance(root, str) or re.fullmatch(r"/[A-Za-z0-9_./-]+", root) is None
                    or os.path.normpath(root) != root or root == "/"):
                raise ValueError("Invalid target release registry root")
            _deployments(record["deployments"], root)
        if not initialized:
            if state["applications"]:
                raise ValueError("Target release witness is missing with retained applications")
            self._save(state)
            write_bytes_atomic(self.witness, _INITIALIZED, mode=0o600)
        return state

    def _save(self, state: dict) -> None:
        write_bytes_atomic(self.registry, encode_document(state, maximum=MAX_REGISTRY_BYTES), mode=0o600)

    @staticmethod
    def _groups(applications: dict) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        seen = set()
        for record in applications.values():
            for deployment in record["deployments"]:
                identity = (deployment["domain"], deployment["path"].rstrip("/") or "/")
                if identity in seen:
                    raise ValueError("Target release registry contains conflicting routes")
                seen.add(identity)
                groups.setdefault(deployment["domain"], []).append(deployment)
        return groups

    def __call__(self, *, bindings: object, request: object, operation_id: str,
                 generation: int, artifact_sha256: str, principal: str) -> dict[str, Any]:
        """Run only as the trusted callback of a current admission activation."""
        if (not isinstance(operation_id, str) or _ID.fullmatch(operation_id) is None
                or type(generation) is not int or generation < 1
                or not isinstance(artifact_sha256, str) or _DIGEST.fullmatch(artifact_sha256) is None):
            raise ValueError("Invalid target activation identity")
        checked, manifest = _bindings_manifest(bindings, request, principal)
        binding = checked["bindings"]
        job = parse_build_request(request)
        root = binding["deployment_root"]
        parent, basename = os.path.split(root)
        if any(os.path.commonpath((root, private)) in {root, private}
               for private in (self.root, self.staging_root)):
            raise ValueError("Target release overlaps private target evidence")
        with _trusted_directory(parent, private=False), _trusted_directory(self.root, private=True), _trusted_directory(self.staging_root, private=True):
            operation = OperationStateStore(os.path.join(self.root, "activation.json"))
            with operation.ownership():
                state = self._load()
                old_groups = self._groups(state["applications"])
                previous = state["applications"].get(binding["application"])
                if previous is not None and (previous["operation_id"] == operation_id or previous["generation"] >= generation):
                    raise ValueError("Target release has already applied this or a newer generation")
                if previous is not None and previous["root"] != root:
                    raise ValueError("Changing an active target release root requires operator reconciliation")
                if binding["application"] not in state["applications"] and len(state["applications"]) >= MAX_APPLICATIONS:
                    raise ValueError("Target release registry is full")
                for application, record in state["applications"].items():
                    if application == binding["application"]:
                        continue
                    other = record["root"]
                    sanitize = lambda value: re.sub(r"[^a-z0-9_-]", "-", os.path.basename(value).lower()).strip("-_") or "x"
                    if (os.path.commonpath((root, other)) in {root, other}
                            or sanitize(root) == sanitize(other)):
                        raise ValueError("Target release conflicts with another active application's root or identity")
                    existing_routes = {(item["domain"], item["path"].rstrip("/") or "/")
                                       for item in record["deployments"]}
                    if any((item.domain, item.path.rstrip("/") or "/") in existing_routes
                           for item in manifest.components):
                        raise ValueError("Target release conflicts with another active application's route")
                marker = operation.begin("target-release", root, "activating", context={
                    "admission_operation_id": operation_id, "generation": generation,
                    "artifact_sha256": artifact_sha256, "application": binding["application"],
                })
                try:
                    manager = DeploymentOrchestrator(base_dir=parent)
                    # A literal local path spec preserves the administrator's
                    # root exactly; component routes already contain domains.
                    if manager.get_deployment_path(None, "/" + basename, "https://basaltwater.invalid/source.git") != root:
                        raise ValueError("Deployment path does not match the authorized target root")
                    with self.open_artifact(operation_id, generation, job, owner=principal,
                                            bindings_sha256=checked["bindings_sha256"],
                                            artifact_sha256=artifact_sha256) as bundle:
                        deployments = manager.deploy_prebuilt(
                            manifest, bundle, job, artifact_sha256, None, "/" + basename,
                            "https://basaltwater.invalid/" + job["project"] + ".git",
                            staging_root=self.staging_root, target_platform=DEBIAN_PLATFORM,
                        )
                    state["applications"][binding["application"]] = {
                        "operation_id": operation_id, "generation": generation,
                        "artifact_sha256": artifact_sha256, "request_sha256": request_digest(job),
                        "root": root, "deployments": _deployments(deployments, root),
                    }
                    groups = self._groups(state["applications"])
                    # Bound the next registry before changing Nginx; the release
                    # operation remains inspectable if either boundary fails.
                    encode_document(state, maximum=MAX_REGISTRY_BYTES)
                    operation.transition(marker.operation_id, "publishing")
                    create_nginx_sites_for_groups(groups, managed_domains=set(old_groups) | set(groups))
                    self._save(state)
                    operation.complete(marker.operation_id)
                except BaseException:
                    try:
                        operation.transition(marker.operation_id, "recovery", status="recovery_required")
                    except BaseException:
                        pass
                    raise
        return {"application": binding["application"], "operation_id": operation_id,
                "generation": generation, "artifact_sha256": artifact_sha256, "state": "completed"}
