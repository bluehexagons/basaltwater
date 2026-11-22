"""Run managed Syncthing with a private API and validate GUI configuration writes."""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
from threading import BoundedSemaphore
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree


GUI_ADDRESS = "127.0.0.1:8384"
GUI_SOCKET = "/run/basaltwater-syncthing/gui.sock"
MAX_CONFIG = 16 * 1024 * 1024
_DATA_ACTIONS = {
    "/rest/db/ignores", "/rest/db/override", "/rest/db/prio", "/rest/db/revert", "/rest/db/scan",
    "/rest/folder/versions", "/rest/noauth/auth/password", "/rest/noauth/auth/logout",
    "/rest/system/error/clear", "/rest/system/pause", "/rest/system/restart",
    "/rest/system/resume",
}
_DISMISS_ACTIONS = {"/rest/cluster/pending/devices", "/rest/cluster/pending/folders"}
_HOP_HEADERS = {
    "connection", "content-length", "expect", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
}


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _storage_path(value: object, root: str) -> str:
    if isinstance(value, str):
        path = value.rstrip("/")
        if (path.startswith("/") and not path.startswith("//") and path == os.path.normpath(path)
                and _within(path, root) and os.path.realpath(path) == path
                and not any(ord(char) < 32 or ord(char) == 127 for char in path)):
            return path
    raise ValueError("Syncthing folder path is outside the configured storage root or uses a symlink")


def _check_storage_mounts(root: str, state: str) -> None:
    """Exclude nested mount trees and aliases of the private state filesystem."""
    mounts = []
    with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as stream:
        for line in stream:
            _mount_id, _parent, device, source, target, *_ = line.split()
            source, target = (re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), item)
                              for item in (source, target))
            if target != root and _within(target, root):
                raise ValueError("Syncthing storage root contains a nested mount")
            mounts.append((target, device, source))

    def backing_paths(path: str) -> set[tuple[str, str]]:
        parents = [mount for mount in mounts if _within(path, mount[0])]
        if not parents:
            raise ValueError("Cannot determine the Syncthing storage mount")
        depth = max(len(target) for target, _, _ in parents)
        # Treat stacked mounts at the same path conservatively.
        return {(device, os.path.normpath(os.path.join(source, os.path.relpath(path, target))))
                for target, device, source in parents if len(target) == depth}

    root_backings = backing_paths(root)
    state_backings = backing_paths(state)
    if any(root_device == state_device and (_within(root_source, state_source) or _within(state_source, root_source))
           for root_device, root_source in root_backings for state_device, state_source in state_backings):
        raise ValueError("Syncthing storage root overlaps the private service state")


def _check_folder_paths(paths: list[object], root: str) -> None:
    declared = {_storage_path(path, root) for path in paths}
    if len(declared) != len(paths) or any(
        str(ancestor) in declared for path in declared for ancestor in Path(path).parents
    ):
        raise ValueError("Syncthing folder roots must be distinct and must not overlap")


def _check_folder(kind: object, marker: object, version: object, version_path: object, version_fs: object) -> None:
    if (kind != "basic" or marker != ".stfolder"
            or version not in ("", "simple", "staggered", "trashcan")
            or version_path != "" or version_fs != "basic"):
        raise ValueError("Syncthing requires basic folders, the default marker and built-in version storage")


def _read_config_file(state: str) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(os.path.join(state, "config.xml"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Syncthing requires a private, regular config.xml")
        raw = stream.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise ValueError("Syncthing configuration exceeds 16 MiB")
    return raw, info


def _parse_config_xml(raw: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in raw.upper():
        raise ValueError("Unsupported Syncthing XML configuration")
    try:
        config = ElementTree.fromstring(raw.decode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid Syncthing XML configuration") from exc
    if config.tag != "configuration":
        raise ValueError("Invalid Syncthing XML configuration")
    return config


def _validate_offline_config(state: str, root: str) -> None:
    _check_storage_mounts(root, state)
    raw, _ = _read_config_file(state)
    config = _parse_config_xml(raw)
    options = config.findall("options")
    nat = config.findall("options/natEnabled")
    if len(options) != 1 or len(nat) != 1 or nat[0].text != "false":
        raise ValueError("Syncthing automatic router mapping must be disabled")
    folders = config.findall("folder")
    _check_folder_paths([folder.get("path") for folder in folders], root)
    for folder in folders + config.findall("defaults/folder"):
        version = folder.find("versioning")
        _check_folder(folder.findtext("filesystemType", "basic"),
                      folder.findtext("markerName", ".stfolder"),
                      version.get("type", "") if version is not None else "",
                      folder.findtext("versioning/fsPath", ""),
                      folder.findtext("versioning/fsType", "basic"))
        if version is not None and any(row.get("key") == "versionsPath" and row.get("val")
                                       for row in version.findall("param")):
            raise ValueError("Syncthing requires built-in version storage")
    if any(node.text not in ("false", "0") for node in config.findall("device/autoAcceptFolders")
           + config.findall("defaults/device/autoAcceptFolders")):
        raise ValueError("Disable Syncthing automatic folder acceptance before starting the managed service")
    gui = config.findall("gui")
    if (len(gui) != 1 or gui[0].get("enabled", "true") != "true"
            or not gui[0].findtext("user") or not gui[0].findtext("password")
            or gui[0].findtext("authMode", "static") != "static"
            or gui[0].get("debugging", "false") != "false"
            or gui[0].findtext("insecureAdminAccess", "false") != "false"
            or gui[0].findtext("insecureSkipHostcheck", "false") != "false"
            or gui[0].findtext("insecureAllowFrameLoading", "false") != "false"):
        raise ValueError("Syncthing requires its managed GUI authentication policy")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    folded = set()
    for key, value in pairs:
        if key.casefold() in folded:
            raise ValueError("Syncthing configuration contains duplicate keys")
        folded.add(key.casefold())
        result[key] = value
    return result


def _fields(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Expected a complete Syncthing configuration object")
    # Go's JSON decoder also matches fields case-insensitively.
    return {key.casefold(): field for key, field in value.items()}


def _validate_config_write(raw: bytes, root: str, state: str) -> bytes:
    config = json.loads(raw, object_pairs_hook=_unique_keys)
    settings = _fields(config)
    folders, devices = settings.get("folders"), settings.get("devices")
    if not isinstance(folders, list) or not isinstance(devices, list):
        raise ValueError("Use the complete Syncthing configuration to change sharing")
    _check_storage_mounts(root, state)
    defaults = _fields(settings.get("defaults"))
    default_folder = _fields(defaults.get("folder"))
    _check_folder_paths([_fields(folder).get("path") for folder in folders], root)
    _storage_path(default_folder.get("path"), root)
    options = _fields(settings.get("options"))
    _storage_path(options.get("defaultfolderpath", root), root)
    if options.get("natenabled") is not False:
        raise ValueError("Syncthing automatic router mapping must be disabled")
    for folder in [*map(_fields, folders), default_folder]:
        version = _fields(folder.get("versioning", {}))
        _check_folder(folder.get("filesystemtype", "basic"), folder.get("markername", ".stfolder"),
                      version.get("type", ""), version.get("fspath", ""), version.get("fstype", "basic"))
        params = version.get("params")
        if isinstance(params, dict) and params.get("versionsPath"):
            raise ValueError("Syncthing requires built-in version storage")
    for device in [*devices, defaults.get("device", {})]:
        if _fields(device).get("autoacceptfolders", False) is not False:
            raise ValueError("Syncthing automatic folder acceptance is disabled")
    gui = _fields(settings.get("gui"))
    if (gui.get("enabled") is not True or gui.get("address") != GUI_ADDRESS
            or gui.get("authmode", "static") != "static"
            or not all(isinstance(gui.get(key), str) and gui[key] for key in ("user", "password", "apikey"))
            or any(gui.get(key, False) is not False for key in
                   ("usetls", "debugging", "insecureadminaccess", "insecureskiphostcheck", "insecureallowframeloading"))):
        raise ValueError("Syncthing GUI address and authentication are managed by setup")
    return json.dumps(config, ensure_ascii=True, allow_nan=False).encode("ascii")


def _request_policy(method: str, target: str) -> bool:
    """Return whether to validate a complete object before proxying."""
    if (not target.startswith("/") or "//" in target.split("?", 1)[0]
            or not re.fullmatch(r"/[A-Za-z0-9_./-]*", target.split("?", 1)[0])):
        raise PermissionError("Invalid Syncthing request path")
    path = urlsplit(target).path
    if any(part in (".", "..") for part in path.split("/")) or _within(path, "/rest/debug"):
        raise PermissionError("Invalid Syncthing request path")
    if (method, path) in {("PUT", "/rest/config"), ("POST", "/rest/system/config")}:
        return True
    if (method in {"GET", "HEAD"} or method == "POST" and path in _DATA_ACTIONS
            or method == "DELETE" and path in _DISMISS_ACTIONS):
        return False
    raise PermissionError("This Syncthing endpoint is read-only. Use PUT /rest/config to change sharing.")


class _UnixConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(GUI_SOCKET)


def _forward_headers(headers) -> list[tuple[str, str]]:
    connection = {token.strip().lower() for value in headers.get_all("Connection", ())
                  for token in value.split(",")}
    return [(name, value) for name, value in headers.items()
            if name.lower() not in _HOP_HEADERS and name.lower() not in connection]


class _ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        # Queries and headers can contain configuration details.
        pass

    def proxy(self) -> None:
        try:
            if self.headers.get_all("Host") not in (
                [GUI_ADDRESS], ["localhost:8384"], ["127.0.0.1"], ["localhost"],
            ):
                raise PermissionError("Connect through the managed Syncthing address")
            target = self.path
            validate = _request_policy(self.command, target)
            url = urlsplit(target)
            if url.path == "/rest/system/browse":
                requested = parse_qs(url.query, max_num_fields=16).get("current", [""])[0]
                path = _storage_path(requested or self.server.share_root, self.server.share_root)
                # Browsing without a trailing slash asks upstream to list
                # matching siblings; list the root's contents in that case.
                if path == self.server.share_root or requested.endswith("/"):
                    path += "/"
                target = url.path + "?" + urlencode({"current": path})
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1
                    or lengths and not re.fullmatch(r"[0-9]{1,8}", lengths[0])):
                raise ValueError("Unsupported Syncthing request framing")
            size = int(lengths[0]) if lengths else 0
            if size > MAX_CONFIG:
                raise ValueError("Syncthing request exceeds the configured limit")
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete Syncthing request")
            if validate:
                body = _validate_config_write(body, self.server.share_root, self.server.state)
        except PermissionError as exc:
            self.send_error(403, str(exc))
            return
        except (OSError, ValueError, RecursionError):
            self.send_error(400, "Syncthing request rejected by the managed storage policy")
            return

        backend = _UnixConnection(GUI_ADDRESS, timeout=75)
        try:
            backend.putrequest(self.command, target, skip_host=True, skip_accept_encoding=True)
            for name, value in _forward_headers(self.headers):
                backend.putheader(name, value)
            backend.putheader("Connection", "close")
            backend.putheader("Content-Length", str(len(body)))
            backend.endheaders(body)
            upstream = backend.getresponse()
        except (OSError, http.client.HTTPException):
            backend.close()
            self.send_error(502, "Syncthing backend is unavailable")
            return
        try:
            self.send_response_only(upstream.status)
            for name, value in _forward_headers(upstream.headers):
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(upstream, self.wfile, length=65536)
        except (OSError, http.client.HTTPException):
            pass  # An interrupted upstream response must close this connection.
        finally:
            backend.close()

    do_GET = do_HEAD = do_PUT = do_POST = do_PATCH = do_DELETE = proxy


class _ProxyServer(ThreadingHTTPServer):
    def __init__(self, root: str, state: str):
        self.share_root = root
        self.state = state
        self._slots = BoundedSemaphore(32)
        host, port = GUI_ADDRESS.split(":")
        super().__init__((host, int(port)), _ProxyHandler)
        self.timeout = 0.5

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(15)
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main(root: str, state: str) -> int:
    if _storage_path(root, root) != root:
        raise ValueError("Syncthing storage root is not canonical")
    _validate_offline_config(state, root)
    if os.path.lexists(GUI_SOCKET):
        if not stat.S_ISSOCK(os.lstat(GUI_SOCKET).st_mode):
            raise ValueError("Cannot replace an unexpected Syncthing GUI socket path")
        os.unlink(GUI_SOCKET)
    with _ProxyServer(root, state) as server, subprocess.Popen(
        ["/usr/bin/syncthing", "serve", f"--home={state}", f"--gui-address=unix://{GUI_SOCKET}",
         "--no-browser", "--no-restart", "--no-upgrade"],
        stdin=subprocess.DEVNULL,
    ) as daemon:
        try:
            while daemon.poll() is None:
                server.handle_request()
        finally:
            if daemon.poll() is None:
                daemon.terminate()
        return 1 if daemon.returncode == 3 else daemon.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main(*sys.argv[1:]))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Syncthing storage guard: {exc}") from None
