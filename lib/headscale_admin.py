"""Root-only native Unix-socket administration; no generic command execution."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import os
import pwd
import re
import stat
import uuid

from lib.atomic_io import parse_json, remove_file_durable, write_json_atomic
from lib.concurrency import resource_lock
from lib.headscale_client import native
from lib.headscale_dns_binding import dns_binding_lease
from lib.headscale_server import ServerPaths, load_manifest, regular_bytes
from lib.setup_directories import check_directory_path, directory_fd
from lib.validation import validate_network_ip
from lib.validators import validate_username

LIMIT = 1024 * 1024
VERSION = "0.29.7"  # Must change with the reviewed native schema subset.
SCHEMAS = {"users_list": (), "users_create": ("name",), "users_delete": ("id",),
           "nodes_list": (), "register": ("user", "auth_id"),
           "node_expire": ("id",), "node_delete": ("id",)}


class AdminError(RuntimeError):
    """Safe fixed diagnostic; native details and secret values never appear."""


def identifier(value: object) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
            or int(value) > 2**63 - 1):
        raise AdminError("Invalid native identity")
    return value


def validate_action(action: str, parameters: dict) -> dict:
    if (not isinstance(action, str) or action not in SCHEMAS or type(parameters) is not dict
            or set(parameters) != set(SCHEMAS[action])):
        raise AdminError("Invalid administrative request schema")
    for key, value in parameters.items():
        if key == "id":
            identifier(value)
        elif key == "user" and isinstance(value, str) and value.isascii() and value.isdigit():
            identifier(value)
        elif key in {"name", "user"}:
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
                raise AdminError("Invalid native user name")
            if not validate_username(value):
                raise AdminError("Invalid native user name")
        elif not isinstance(value, str) or not re.fullmatch(r"hskey-authreq-[A-Za-z0-9_-]{24}", value):
            raise AdminError("Invalid private Auth ID")
    return dict(parameters)


def approved_tags(value: object) -> list[str]:
    if (type(value) is not list or not 1 <= len(value) <= 16
            or any(not isinstance(t, str) or not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]{0,62}", t)
                   for t in value) or len(set(value)) != len(value)):
        raise AdminError("Explicit approved service tags required")
    return sorted(value)


def user_projection(user) -> dict:
    if not user.name or len(user.name) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in user.name):
        raise AdminError("Invalid native user schema")
    return {"id": identifier(str(user.id)), "name": user.name}


def node_projection(node) -> dict:
    ident = identifier(str(node.id))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", node.name):
        raise AdminError("Invalid native node schema")
    addresses = [validate_network_ip(a) for a in node.ip_addresses]
    if not 1 <= len(addresses) <= 8 or len(set(addresses)) != len(addresses):
        raise AdminError("Invalid native node addresses")
    for text in addresses:
        address = ipaddress.ip_address(text)
        if address not in ipaddress.ip_network("100.64.0.0/10" if address.version == 4 else "fd7a:115c:a1e0::/48"):
            raise AdminError("Native node address outside managed overlay")
    tags = list(node.tags)
    if tags:
        approved_tags(tags)
    return {"id": ident, "node_id": ident, "hostname": node.name, "addresses": addresses,
            "tags": tags, "user_id": (identifier(str(node.user.id))
                                        if node.HasField("user") and node.user.id and not tags else None),
            "online": node.online,
            "expiry": node.expiry.ToJsonString() if node.HasField("expiry") else None}


class NativeAdmin:
    """Trusted construction only: paths are never accepted by action payloads."""

    def __init__(self, paths: ServerPaths = ServerPaths()):
        self.paths = paths
        self.endpoint = ""
        self.config = {}

    def verify(self) -> None:
        if os.geteuid() != 0:
            raise AdminError("Headscale administration requires root")
        manifest = load_manifest(self.paths)
        self.config = parse_json(regular_bytes(self.paths.config).decode())
        if (manifest is None or manifest.version != VERSION or type(self.config) is not dict
                or self.config.get("server_url") != manifest.endpoint
                or self.config.get("unix_socket") != str(self.paths.socket)
                or self.config.get("unix_socket_permission") != "0600"
                or self.config.get("log", {}).get("level") != "warn"
                or self.config.get("dns", {}).get("base_domain") != manifest.base_domain):
            raise AdminError("Managed native endpoint/version binding mismatch")
        check_directory_path(str(self.paths.socket.parent))
        info = self.paths.socket.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_mode & 0o077
                or info.st_uid != pwd.getpwnam("headscale").pw_uid):
            raise AdminError("Unsafe native administrative socket")
        binary = os.stat("/usr/bin/headscale", follow_symlinks=False)
        if not stat.S_ISREG(binary.st_mode) or binary.st_uid != 0 or binary.st_mode & 0o022:
            raise AdminError("Unsafe native server binary")
        if not re.fullmatch(r"headscale version v?" + re.escape(VERSION),
                            native(["/usr/bin/headscale", "version"], limit=4096).splitlines()[0].strip()):
            raise AdminError("Native binary differs from schema pin")
        state = dict(line.split("=", 1) for line in native([
            "/usr/bin/systemctl", "show", "headscale",
            "--property=ActiveState,User,ExecStart,Environment,EnvironmentFiles,MainPID"],
            limit=16384).splitlines() if "=" in line)
        command = state.get("ExecStart", "")
        if (state.get("ActiveState") != "active" or state.get("User") != "headscale"
                or "path=/usr/bin/headscale ;" not in command
                or "argv[]=/usr/bin/headscale serve ;" not in command
                or state.get("Environment") != "" or state.get("EnvironmentFiles") != ""
                or not re.fullmatch(r"[1-9][0-9]{0,9}", state.get("MainPID", ""))):
            raise AdminError("Native service ownership unavailable")
        running = os.stat("/proc/" + state["MainPID"] + "/exe")
        if (running.st_dev, running.st_ino) != (binary.st_dev, binary.st_ino):
            raise AdminError("Running server differs from pinned binary; reconcile restart")
        self.endpoint = manifest.endpoint

    def rpc(self, method: str, **fields):
        from lib.headscale_admin_schema import messages
        try:
            import grpc
            types = messages()
            request = types[method + "Request"](**fields)
            with grpc.insecure_channel("unix:" + str(self.paths.socket), options=(
                    ("grpc.max_receive_message_length", LIMIT),
                    ("grpc.max_send_message_length", 8192),
                    ("grpc.enable_retries", 0))) as channel:
                call = channel.unary_unary("/headscale.v1.HeadscaleService/" + method,
                                           request_serializer=lambda msg: msg.SerializeToString(),
                                           response_deserializer=types[method + "Response"].FromString)
                return call(request, timeout=10, wait_for_ready=False)
        except Exception:
            raise AdminError("Native operation unconfirmed; reconcile before retry") from None

    @contextmanager
    def dns_guard(self, node: dict):
        """Management-side publication reservation, keyed by endpoint/StableID.

        Publisher must hold this same lease through native DNS publication.
        Native nameservers additionally prevent removal of unrecorded resolvers.
        """
        with dns_binding_lease(str(self.paths.private / "dns-publication.json")) as lease:
            binding = lease.binding
            if binding and binding.network_url != self.endpoint:
                raise AdminError("DNS publication endpoint mismatch")
            if binding and binding.node_id == node["node_id"] and binding.advertised:
                raise AdminError("Withdraw or replace advertised DNS resolver first")
            config = parse_json(regular_bytes(self.paths.config).decode())
            nameservers = config["dns"]["nameservers"]
            addresses = nameservers.get("global", []) + [a for group in nameservers.get("split", {}).values() for a in group]
            if set(node["addresses"]) & set(addresses):
                raise AdminError("Withdraw or replace advertised DNS resolver first")
            yield

    def users(self) -> list[dict]:
        return [user_projection(u) for u in self.rpc("ListUsers").users]

    def nodes(self) -> list[dict]:
        return [node_projection(n) for n in self.rpc("ListNodes").nodes]

    def execute(self, action: str, parameters: dict) -> dict:
        p = validate_action(action, parameters)
        self.verify()
        if action == "users_list":
            return {"users": self.users()}
        if action == "nodes_list":
            return {"nodes": self.nodes()}
        if action == "users_create":
            user = user_projection(self.rpc("CreateUser", name=p["name"]).user)
            if user["name"] != p["name"] or user not in self.users():
                raise AdminError("User creation not verified")
            return {"user": user}
        if action == "users_delete":
            if any(n["user_id"] == p["id"] for n in self.nodes()):
                raise AdminError("Remove owned nodes before deleting user")
            if not any(u["id"] == p["id"] for u in self.users()):
                raise AdminError("User identity unavailable")
            self.rpc("DeleteUser", id=int(p["id"]))
            if any(u["id"] == p["id"] for u in self.users()):
                raise AdminError("User deletion not verified")
            return {"deleted": p["id"]}
        if action == "register":
            # Portal reviews bind the immutable numeric user ID. The native
            # registration RPC takes a user name; resolve it at execution time.
            field = "id" if p["user"].isdigit() else "name"
            users = [u for u in self.users() if u[field] == p["user"]]
            if len(users) != 1:
                raise AdminError("Exact native user unavailable")
            node = node_projection(self.rpc("AuthRegister", user=users[0]["name"], auth_id=p["auth_id"]).node)
            if node["user_id"] != users[0]["id"] or node not in self.nodes():
                raise AdminError("Registered node ownership not verified")
            return {"node": node}
        nodes = [n for n in self.nodes() if n["id"] == p["id"]]
        if len(nodes) != 1:
            raise AdminError("Exact native node unavailable")
        with self.dns_guard(nodes[0]):
            if action == "node_delete":
                self.rpc("DeleteNode", node_id=int(p["id"]))
                if any(n["id"] == p["id"] for n in self.nodes()):
                    raise AdminError("Node deletion not verified")
                return {"deleted": p["id"]}
            self.rpc("ExpireNode", node_id=int(p["id"]))
            observed = [n for n in self.nodes() if n["id"] == p["id"]]
            if (len(observed) != 1 or not observed[0]["expiry"]
                    or datetime.fromisoformat(observed[0]["expiry"].replace("Z", "+00:00")) > datetime.now(timezone.utc)):
                raise AdminError("Node expiry not verified")
            return {"node": observed[0]}


def execute_admin_action(action: str, parameters: dict) -> dict:
    """Portal contract: authorize outside this module; never persist Auth IDs."""
    try:
        validate_action(action, parameters)
        adapter = NativeAdmin()
        if action in {"users_list", "nodes_list"}:
            return adapter.execute(action, parameters)
        # Serialise exact approval consumption across portal and SSH callers.
        # Never use an Auth ID as a lock name, filesystem key or stored value.
        if os.geteuid() != 0:
            raise AdminError("Headscale administration requires root")
        with resource_lock("headscale-admin", str(adapter.paths.private)):
            return adapter.execute(action, parameters)
    except AdminError:
        raise
    except Exception:
        raise AdminError("Native administration unavailable; inspect private state") from None


def issue_machine_key(approved_service_tags: list[str]) -> dict:
    """Explicit root authorization for exactly these tags; return metadata only.

    Never replay this call. The persistent intent survives success, cancellation,
    and lost acknowledgement until explicit native key/node reconciliation.
    """
    tags = approved_tags(approved_service_tags)
    admin = NativeAdmin()
    admin.verify()
    with directory_fd(str(admin.paths.private), mode=0o700) as directory:
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise AdminError("Unsafe private issuance directory")
        with resource_lock("headscale-key-issue", str(admin.paths.private)):
            intent = admin.paths.private / "key-intent.json"
            if intent.exists() or intent.is_symlink():
                raise AdminError("Reconcile previous key and nodes before issuing again")
            expires = int(datetime.now(timezone.utc).timestamp()) + 3600
            before = [identifier(str(k.id)) for k in admin.rpc("ListPreAuthKeys").pre_auth_keys]
            name = uuid.uuid4().hex
            write_json_atomic(str(intent), {"schema": 1, "endpoint": admin.endpoint,
                                           "tags": tags, "expiry": expires, "before": before,
                                           "id": "", "delivery": name})
            key = admin.rpc("CreatePreAuthKey", acl_tags=tags,
                            expiration={"seconds": expires}).pre_auth_key
            if (key.reusable or key.ephemeral or key.used or key.HasField("user")
                    or sorted(key.acl_tags) != tags or key.expiration.seconds != expires
                    or key.expiration.nanos or not re.fullmatch(r"[A-Za-z0-9_-]{32,4096}", key.key)):
                raise AdminError("Issued key attributes differ; reconcile native key state")
            ident = identifier(str(key.id))
            if ident in before:
                raise AdminError("Issued key identity reused")
            transient = admin.paths.private / "keys"
            with directory_fd(str(transient), create=True, mode=0o700) as fd:
                info = os.fstat(fd)
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                    raise AdminError("Unsafe transient key directory")
                secret = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
                with os.fdopen(secret, "w") as stream:
                    stream.write(key.key)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
            write_json_atomic(str(intent), {"schema": 1, "endpoint": admin.endpoint,
                                           "tags": tags, "expiry": expires, "before": before,
                                           "id": ident, "delivery": name})
            return {"id": ident, "expiry": key.expiration.ToJsonString(), "tags": tags,
                    "reusable": False, "ephemeral": False}


def manage_machine_key(action: str, key_id: str) -> dict:
    """Expire/revoke future enrollment or delete by ID, never by key value."""
    ident = identifier(key_id)
    if action not in {"expire", "revoke", "delete"}:
        raise AdminError("Invalid key lifecycle action")
    admin = NativeAdmin()
    admin.verify()
    keys = [k for k in admin.rpc("ListPreAuthKeys").pre_auth_keys if str(k.id) == ident]
    if len(keys) != 1:
        raise AdminError("Exact native key unavailable")
    admin.rpc("DeletePreAuthKey" if action == "delete" else "ExpirePreAuthKey", id=int(ident))
    keys = [k for k in admin.rpc("ListPreAuthKeys").pre_auth_keys if str(k.id) == ident]
    if (action == "delete" and keys) or (action != "delete" and
            (len(keys) != 1 or not keys[0].HasField("expiration") or
             keys[0].expiration.seconds > datetime.now(timezone.utc).timestamp())):
        raise AdminError("Key lifecycle result not verified")
    return {"id": ident, "state": "deleted" if action == "delete" else "expired"}


def inspect_machine_keys() -> dict:
    """Private root reconciliation view with no native key/prefix/hash values."""
    admin = NativeAdmin()
    admin.verify()
    keys = []
    for key in admin.rpc("ListPreAuthKeys").pre_auth_keys:
        keys.append({"id": identifier(str(key.id)), "tags": list(key.acl_tags),
                     "reusable": key.reusable, "ephemeral": key.ephemeral, "used": key.used,
                     "expiry": key.expiration.ToJsonString() if key.HasField("expiration") else None})
    return {"keys": keys, "nodes": admin.nodes(),
            "reconciliation_required": (admin.paths.private / "key-intent.json").exists()}


def reconcile_machine_key(expected_key_ids: list[str], expected_node_ids: list[str]) -> dict:
    """Reviewed native snapshot, revoke outstanding issued keys and discard delivery.

    Lists must exactly match all keys created since the intent and all current
    nodes. Existing enrolled nodes remain; their lifecycle requires separate
    explicit expiry/removal. An unknown outcome never triggers another issue.
    """
    for values in (expected_key_ids, expected_node_ids):
        if type(values) is not list or len(values) > 10000:
            raise AdminError("Exact reviewed native identities required")
        for value in values:
            identifier(value)
        if len(set(values)) != len(values):
            raise AdminError("Duplicate reviewed native identity")
    admin = NativeAdmin()
    admin.verify()
    with resource_lock("headscale-key-issue", str(admin.paths.private)):
        intent_path = admin.paths.private / "key-intent.json"
        info = intent_path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise AdminError("Unsafe private issuance intent")
        intent = parse_json(regular_bytes(intent_path))
        fields = {"schema", "endpoint", "tags", "expiry", "before"}
        if (type(intent) is not dict or set(intent) not in (fields, fields | {"id", "delivery"})
                or intent["schema"] != 1 or intent["endpoint"] != admin.endpoint
                or type(intent["before"]) is not list):
            raise AdminError("Invalid issuance intent; retain for private recovery")
        approved_tags(intent["tags"])
        for ident in intent["before"]:
            identifier(ident)
        keys = admin.rpc("ListPreAuthKeys").pre_auth_keys
        outstanding = {str(k.id) for k in keys} - set(intent["before"])
        if (outstanding != set(expected_key_ids)
                or {n["id"] for n in admin.nodes()} != set(expected_node_ids)):
            raise AdminError("Native keys/nodes changed; review reconciliation again")
        for ident in sorted(outstanding):
            admin.rpc("ExpirePreAuthKey", id=int(identifier(ident)))
        remaining = [k for k in admin.rpc("ListPreAuthKeys").pre_auth_keys if str(k.id) in outstanding]
        if any(not k.HasField("expiration") or k.expiration.seconds > datetime.now(timezone.utc).timestamp()
               for k in remaining):
            raise AdminError("Key revocation not verified; retain issuance intent")
        if "delivery" in intent:
            name = intent["delivery"]
            if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{32}", name):
                raise AdminError("Invalid private delivery identity")
            remove_file_durable(str(admin.paths.private / "keys" / name))
            # The separate private SSH delivery claim never permits key replay.
            # Reconciliation clears it only for this exact issuance intent.
            from lib.headscale_private import read_private

            claim_path = str(admin.paths.private / "key-delivery.json")
            claim = read_private(claim_path)
            if claim is not None:
                if claim != {"schema": 1, "id": intent["id"], "delivery": name}:
                    raise AdminError("Private delivery claim differs; retain issuance intent")
                remove_file_durable(claim_path)
        remove_file_durable(str(intent_path))
        return {"reconciled": sorted(outstanding), "nodes": sorted(expected_node_ids)}
