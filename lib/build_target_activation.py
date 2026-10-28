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
from lib.build_admission import DeploymentAdmissionStore
from lib.build_manifest import DEBIAN_PLATFORM, validate_manifest_request
from lib.build_protocol import encode_document, parse_build_request, request_digest
from lib.build_target_policy import authorize_target_deployment
from lib.build_target_routing import load_target_routing, routing_for_config
from lib.cicd_deploy_policy import validate_nginx_path
from lib.deployment import DeploymentOrchestrator
from lib.nginx_config import create_nginx_sites_for_groups
from lib.operation_state import OperationStateStore
from lib.project_manifest import Manifest, parse_manifest, collect_public_udp
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


MAX_REGISTRY_BYTES = 4 * 1024**2
MAX_APPLICATIONS = 256
MAX_EXPECTED_RELEASES = 16
_INITIALIZED = b"basaltwater-target-releases-v1\n"
_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _runtime_collisions(manifests: list[Manifest], occupied: set[int] | None = None) -> None:
    """Reject listener collisions before any release or publication mutation."""
    collect_public_udp([(str(index), manifest) for index, manifest in enumerate(manifests)])
    ports = set(occupied or ())
    for manifest in manifests:
        for component in manifest.components:
            if component.port is not None:
                if component.port in ports:
                    raise ValueError("Target release conflicts with another service port")
                ports.add(component.port)


def parse_expected_releases(value: object) -> list[dict[str, Any]] | None:
    """Detach a bounded, canonical controller completion identity list."""
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_EXPECTED_RELEASES:
        raise ValueError("Expected releases must be a list of 1–16 controller release identities")
    result = []
    applications = set()
    operations = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {
                "application", "operation_id", "generation", "artifact_sha256",
            }
                or not isinstance(item["application"], str) or _SLUG.fullmatch(item["application"]) is None
                or not isinstance(item["operation_id"], str) or _ID.fullmatch(item["operation_id"]) is None
                or type(item["generation"]) is not int or not 1 <= item["generation"] <= 2**63 - 1
                or not isinstance(item["artifact_sha256"], str) or _DIGEST.fullmatch(item["artifact_sha256"]) is None):
            raise ValueError("Invalid expected controller release identity")
        if item["application"] in applications or item["operation_id"] in operations:
            raise ValueError("Expected controller releases contain duplicate applications or operations")
        applications.add(item["application"])
        operations.add(item["operation_id"])
        result.append(dict(item))
    return result


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
    routes = {item["name"]: {key: item[key] for key in ("type", "domain", "path", "runtime") if key in item}
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
        component["env"] = build["env"]
        if build["commands"]:
            component["build"] = build["commands"]
        if component["type"] == "service":
            runtime = component.pop("runtime", None)
            if runtime is None:
                component.update(port="auto", runtime_env={"PORT": "{{port}}", "HOST": "127.0.0.1"})
            else:
                if "exec" in runtime:
                    component.pop("binary")
                component.update(runtime)
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

    def _load(self, *, initialize: bool = True) -> dict[str, Any]:
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
                        "request", "bindings",
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
            checked, _manifest = _bindings_manifest(
                record["bindings"], record["request"], record["bindings"].get("principal")
                if isinstance(record["bindings"], dict) else None,
            )
            if (checked["bindings"]["application"] != app
                    or checked["bindings"]["deployment_root"] != root
                    or request_digest(record["request"]) != record["request_sha256"]):
                raise ValueError("Installed target descriptor differs from its release identity")
        if not initialized:
            if state["applications"]:
                raise ValueError("Target release witness is missing with retained applications")
            if initialize:
                self._save(state)
                write_bytes_atomic(self.witness, _INITIALIZED, mode=0o600)
        return state

    def _save(self, state: dict) -> None:
        write_bytes_atomic(self.registry, encode_document(state, maximum=MAX_REGISTRY_BYTES), mode=0o600)

    def check_routing(self, config) -> None:
        """Refuse local refresh with serving policy not enrolled by root setup."""
        if load_target_routing(self.root) != routing_for_config(config):
            raise ValueError("Artifact serving policy differs; enroll it from the controller before refresh")

    def _publish(self, state: dict, old_groups: dict) -> None:
        """Use the ordinary Nginx, UDP and TLS publishers with retained policy."""
        from deploy.network_steps import configure_public_udp

        routing = load_target_routing(self.root)
        groups = self._groups(state["applications"])
        scope = set(old_groups) | set(groups)
        listeners = self._listeners(state)
        create_nginx_sites_for_groups(groups, managed_domains=scope,
                                     enable_https_redirect=not routing.cloudflare)
        configure_public_udp(listeners)
        if routing.ssl and not routing.cloudflare:
            from web.ssl_steps import install_certbot, setup_ssl_for_deployments

            install_certbot()
            setup_ssl_for_deployments(
                [item for deployments in groups.values() for item in deployments],
                routing.ssl_email, managed_domains=scope,
            )

    @staticmethod
    def _listeners(state: dict) -> list:
        from lib.project_manifest import collect_public_udp

        manifests = [(app, _bindings_manifest(record["bindings"], record["request"],
                                             record["bindings"]["principal"])[1])
                     for app, record in state["applications"].items()]
        return collect_public_udp(manifests)

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

    def _check_expected(self, expected: list[dict[str, Any]], state: dict,
                        admissions: DeploymentAdmissionStore | None) -> None:
        """Check all installed identities, then current intent, before replay."""
        guidance = "Run deployment from the controller again before final setup"
        for identity in expected:
            record = state["applications"].get(identity["application"])
            if record is None or any(record[key] != identity[key] for key in (
                "operation_id", "generation", "artifact_sha256",
            )):
                raise ValueError(f"Expected release is missing or differs from the installed identity. {guidance}")
        if not isinstance(admissions, DeploymentAdmissionStore):
            raise ValueError(f"Expected release replay requires current admission evidence. {guidance}")
        # Do not bootstrap missing admission evidence just to query status.
        if not all(self._private_file(path) for path in (admissions.path, admissions.path + ".lock")):
            raise ValueError(f"Expected release admission evidence is missing. {guidance}")
        if os.stat(admissions.path + ".lock", follow_symlinks=False).st_size == 0:
            raise ValueError(f"Expected release admission initialization witness is missing. {guidance}")
        for identity in expected:
            record = state["applications"][identity["application"]]
            try:
                status = admissions.status(record["operation_id"], owner=record["bindings"]["principal"])
            except (OSError, ValueError, RuntimeError) as exc:
                raise ValueError(f"Expected release admission evidence is unavailable. {guidance}") from exc
            if (any(status[key] != identity[key] for key in identity)
                    or status["state"] != "completed" or status["superseded"] is not False
                    or status["request_sha256"] != record["request_sha256"]
                    or status["bindings_sha256"] != _bindings_manifest(
                        record["bindings"], record["request"], record["bindings"]["principal"],
                    )[0]["bindings_sha256"]):
                raise ValueError(f"Expected release is not the current completed admission. {guidance}")
            # Expiry bounds admission activation, not retention of a completed
            # release. A completed, unsuperseded receipt remains replayable.

    def check_expected_releases(self, *, expected_releases: object,
                                admissions: DeploymentAdmissionStore) -> None:
        """Check current controller completions without writes or artifact I/O.

        The trusted caller must hold exclusive target maintenance throughout
        this check and all subsequent setup mutations. Never call under the
        activation lock: admission status takes the admission lock first.
        """
        expected = parse_expected_releases(expected_releases)
        if expected is None:
            raise ValueError("Expected controller releases are required for guarded setup")
        with _trusted_directory(self.root, private=True), _trusted_directory(self.staging_root, private=True):
            self._check_expected(expected, self._load(initialize=False), admissions)
            if OperationStateStore(os.path.join(self.root, "activation.json")).load() is not None:
                raise ValueError("Unfinished target activation requires operator reconciliation before controller setup")

    def reconcile_installed(self, *, expected_releases: object = None,
                            admissions: DeploymentAdmissionStore | None = None) -> list[dict[str, Any]]:
        """Reapply retained releases for a trusted local setup, without a build.

        This is not a transport action. The root setup caller must exclude
        typed requests and enrollment changes throughout the call. Retained
        bindings describe the installed release; revoking a transport grant
        does not replace that release with new project inputs.

        Expected controller identities additionally require the installed
        admission store. Check it before activation ownership to preserve the
        admission -> activation lock order; the caller's exclusive maintenance
        lease keeps these status reads current through replay and response.
        """
        expected = parse_expected_releases(expected_releases)
        with _trusted_directory(self.root, private=True), _trusted_directory(self.staging_root, private=True):
            load_target_routing(self.root)
            if expected is not None:
                # Refusals must not initialize the registry or create an
                # activation lock/marker, open artifacts, or preflight releases.
                state = self._load(initialize=False)
                self._check_expected(expected, state, admissions)
            operation = OperationStateStore(os.path.join(self.root, "activation.json"))
            with operation.ownership():
                if operation.load() is not None:
                    raise ValueError("Unfinished target activation requires operator reconciliation")
                if expected is None:
                    state = self._load()
                if not state["applications"]:
                    raise ValueError("No retained target releases; run deployment from the controller")
                self._listeners(state)
                prepared = []
                # Check every descriptor and retained bundle before changing an
                # application. Accessors use one shared staging lock, so open
                # them sequentially rather than nesting their contexts.
                for app, record in sorted(state["applications"].items()):
                    binding = record["bindings"]
                    checked, manifest = _bindings_manifest(binding, record["request"], binding["principal"])
                    parent, basename = os.path.split(record["root"])
                    with _trusted_directory(parent, private=False):
                        pass
                    arguments = dict(application=app, owner=binding["principal"],
                                     bindings_sha256=checked["bindings_sha256"],
                                     artifact_sha256=record["artifact_sha256"])
                    with self.open_artifact(record["operation_id"], record["generation"],
                                            record["request"], **arguments):
                        pass
                    prepared.append((app, record, manifest, parent, basename, arguments))
                _runtime_collisions([item[2] for item in prepared])
                for app, _record, manifest, *_rest in prepared:
                    occupied = {route["backend_port"]
                                for other, record in state["applications"].items() if other != app
                                for route in record["deployments"] if route["backend_port"] is not None}
                    _runtime_collisions([manifest], occupied)
                marker = operation.begin("target-release-refresh", self.root, "reconciling", context={
                    "applications": [app for app, *_ in prepared],
                })
                try:
                    old_groups = self._groups(state["applications"])
                    for app, record, manifest, parent, basename, arguments in prepared:
                        manager = DeploymentOrchestrator(base_dir=parent, deploy_user="root", deploy_group="root")
                        with self.open_artifact(record["operation_id"], record["generation"],
                                                record["request"], **arguments) as bundle:
                            deployments = manager.deploy_prebuilt(
                                manifest, bundle, record["request"], record["artifact_sha256"],
                                None, "/" + basename,
                                "https://basaltwater.invalid/" + record["request"]["project"] + ".git",
                                staging_root=self.staging_root, target_platform=DEBIAN_PLATFORM,
                            )
                        record["deployments"] = _deployments(deployments, record["root"])
                    self._groups(state["applications"])
                    encode_document(state, maximum=MAX_REGISTRY_BYTES)
                    operation.transition(marker.operation_id, "publishing")
                    self._publish(state, old_groups)
                    self._save(state)
                    operation.complete(marker.operation_id)
                except BaseException:
                    try:
                        operation.transition(marker.operation_id, "recovery", status="recovery_required")
                    except BaseException:
                        pass
                    raise
        return [{"application": app, "operation_id": record["operation_id"],
                 "generation": record["generation"], "artifact_sha256": record["artifact_sha256"],
                 "state": "reconciled"} for app, record, *_ in prepared]

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
            load_target_routing(self.root)
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
                    _, retained = _bindings_manifest(record["bindings"], record["request"],
                                                     record["bindings"]["principal"])
                    occupied = {item["backend_port"] for item in record["deployments"]
                                if item["backend_port"] is not None}
                    # The retained manifest contains its declared fixed port;
                    # actual assigned auto ports are also reserved here.
                    _runtime_collisions([manifest], occupied)
                    collect_public_udp([(binding["application"], manifest), (application, retained)])
                _runtime_collisions([manifest])
                installed = {
                    "operation_id": operation_id, "generation": generation,
                    "artifact_sha256": artifact_sha256, "request_sha256": request_digest(job),
                    "root": root, "request": job, "bindings": binding, "deployments": [],
                }
                candidate = copy.deepcopy(state)
                candidate["applications"][binding["application"]] = installed
                self._listeners(candidate)
                # Keep room for bounded rendered routes before changing the
                # release. Large retained input documents consume the same
                # registry capacity as other protected evidence.
                encode_document(candidate, maximum=MAX_REGISTRY_BYTES - 1024**2)
                marker = operation.begin("target-release", root, "activating", context={
                    "admission_operation_id": operation_id, "generation": generation,
                    "artifact_sha256": artifact_sha256, "application": binding["application"],
                })
                try:
                    manager = DeploymentOrchestrator(base_dir=parent, deploy_user="root", deploy_group="root")
                    # A literal local path spec preserves the administrator's
                    # root exactly; component routes already contain domains.
                    if manager.get_deployment_path(None, "/" + basename, "https://basaltwater.invalid/source.git") != root:
                        raise ValueError("Deployment path does not match the authorized target root")
                    with self.open_artifact(operation_id, generation, job, owner=principal,
                                            application=binding["application"],
                                            bindings_sha256=checked["bindings_sha256"],
                                            artifact_sha256=artifact_sha256) as bundle:
                        deployments = manager.deploy_prebuilt(
                            manifest, bundle, job, artifact_sha256, None, "/" + basename,
                            "https://basaltwater.invalid/" + job["project"] + ".git",
                            staging_root=self.staging_root, target_platform=DEBIAN_PLATFORM,
                        )
                    installed["deployments"] = _deployments(deployments, root)
                    state["applications"][binding["application"]] = installed
                    self._groups(state["applications"])
                    # Bound the next registry before changing Nginx; the release
                    # operation remains inspectable if either boundary fails.
                    encode_document(state, maximum=MAX_REGISTRY_BYTES)
                    operation.transition(marker.operation_id, "publishing")
                    self._publish(state, old_groups)
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
