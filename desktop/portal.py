"""KDE RemoteDesktop/ScreenCast with explicit opt-in grant restoration."""

from __future__ import annotations

import os
import secrets
import threading

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
RESPONSE_SECONDS = 120


class Portal:
    def __init__(self, closed, *, progress=None):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.DBusConnection.new_for_address_sync(
            f"unix:path=/run/user/{os.getuid()}/bus",
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.session = None
        self.fd = None
        self.pending = set()
        self.waiters = set()
        self.cancelled = threading.Event()
        self.progress = progress or (lambda *_: None)
        self.closed = closed
        self.restore_token = None
        self.close_lock = threading.RLock()
        self.bus.connect("closed", lambda *_: self.disconnected())
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
            "NameOwnerChanged", "/org/freedesktop/DBus", BUS_NAME,
            Gio.DBusSignalFlags.NONE, self.owner_changed)

    def owner_changed(self, _bus, _sender, _path, _interface, _signal, parameters, *_):
        _name, old_owner, new_owner = parameters.unpack()
        if old_owner and old_owner != new_owner:
            self.disconnected()

    def disconnected(self):
        self.cancelled.set()
        for event in tuple(self.waiters):
            event.set()
        self.closed()

    def call(self, interface, method, signature, values, *, path=OBJECT):
        return self.bus.call_sync(BUS_NAME, path, interface, method,
            self.GLib.Variant(signature, values), None,
            self.Gio.DBusCallFlags.NO_AUTO_START, 3000, None)

    def response(self, interface, method, signature, values, options):
        token = "bw" + secrets.token_hex(12)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        event = threading.Event()
        result = []

        def receive(_bus, _sender, _path, _interface, _signal, parameters, *_):
            result.append(parameters.unpack())
            event.set()

        subscription = self.bus.signal_subscribe(BUS_NAME, "org.freedesktop.portal.Request",
            "Response", path, None, self.Gio.DBusSignalFlags.NONE, receive)
        self.pending.add(path)
        self.waiters.add(event)
        try:
            self.progress(method + ".request", False)
            options = {**options, "handle_token": self.GLib.Variant("s", token)}
            returned = self.call(interface, method, signature, (*values, options)).unpack()[0]
            if returned != path:
                raise RuntimeError("Portal returned an unexpected request handle")
            self.progress(method + ".response", method == "Start")
            if not event.wait(RESPONSE_SECONDS):
                raise RuntimeError(f"Portal {method} response timed out; inspect status before retrying")
            if self.cancelled.is_set():
                raise RuntimeError("Portal session closed while waiting for " + method)
            code, fields = result[0]
            if code:
                raise RuntimeError("Portal permission was cancelled or denied; no desktop control granted")
            self.progress(method + ".complete", False)
            return fields
        finally:
            self.bus.signal_unsubscribe(subscription)
            self.pending.discard(path)
            self.waiters.discard(event)
            if not result:
                try:
                    self.call("org.freedesktop.portal.Request", "Close", "()", (), path=path)
                except Exception:
                    pass

    def start(self, *, remember=False, restore_token=None, save_token=None):
        v = self.GLib.Variant
        if remember:
            self.progress("RemoteDesktop.version", False)
            version = self.call("org.freedesktop.DBus.Properties", "Get", "(ss)", (REMOTE, "version")).unpack()[0]
            if version < 2:
                raise RuntimeError("This portal does not support persistent RemoteDesktop grants; use revoke to remove the opt-in, then start without --remember")
        created = self.response(REMOTE, "CreateSession", "(a{sv})", (),
            {"session_handle_token": v("s", "bw" + secrets.token_hex(12))})
        self.session = created["session_handle"]
        self.bus.signal_subscribe(BUS_NAME, "org.freedesktop.portal.Session", "Closed",
            self.session, None, self.Gio.DBusSignalFlags.NONE, lambda *_: self.disconnected())
        options = {"types": v("u", 3), "persist_mode": v("u", 2 if remember else 0)}
        if restore_token is not None:
            if not remember:
                raise ValueError("Restoring a grant requires explicit persistent mode")
            from desktop.native_grants import validate_token
            validate_token(restore_token)
            options["restore_token"] = v("s", restore_token)
        self.restore_token = restore_token
        self.response(REMOTE, "SelectDevices", "(oa{sv})", (self.session,), options)
        # Monitor only: pointer coordinates must map to the user's selected stream.
        self.response(SCREENCAST, "SelectSources", "(oa{sv})", (self.session,),
            {"types": v("u", 1), "multiple": v("b", False), "cursor_mode": v("u", 1)})
        started = self.response(REMOTE, "Start", "(osa{sv})", (self.session, ""), {})
        self.restore_token = started.get("restore_token") if remember else None
        if started.get("devices", 0) & 3 != 3 or len(started.get("streams", [])) != 1:
            self.revoke_token(self.restore_token)
            raise RuntimeError("Select one monitor and grant keyboard and pointer access to use automation")
        node, properties = started["streams"][0]
        size = properties.get("logical_size", properties.get("size"))
        if not isinstance(size, (tuple, list)) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
            self.revoke_token(self.restore_token)
            raise RuntimeError("Portal did not report the selected monitor's logical size")
        if self.restore_token and save_token:
            save_token(self.restore_token)
        self.progress("OpenPipeWireRemote", False)
        reply, descriptors = self.bus.call_with_unix_fd_list_sync(BUS_NAME, OBJECT, SCREENCAST,
            "OpenPipeWireRemote", v("(oa{sv})", (self.session, {})), None,
            self.Gio.DBusCallFlags.NO_AUTO_START, 3000, None, None)
        self.fd = descriptors.get(reply.unpack()[0])
        return node, list(size)

    def notify(self, method, signature, *values):
        self.call(REMOTE, method, "(oa{sv}" + signature + ")", (self.session, {}, *values))

    def revoke_token(self, token):
        """Delete only this grant's PermissionStore entry, never other applications."""
        if not token:
            return
        from desktop.native_grants import validate_token
        validate_token(token)
        try:
            self.bus.call_sync("org.freedesktop.impl.portal.PermissionStore",
                "/org/freedesktop/impl/portal/PermissionStore", "org.freedesktop.impl.portal.PermissionStore",
                "Delete", self.GLib.Variant("(ss)", ("remote-desktop", token)), None,
                self.Gio.DBusCallFlags.NONE, 3000, None)
        except self.GLib.Error as exc:
            # Already consumed/revoked tokens are a successful revocation.
            if self.Gio.DBusError.get_remote_error(exc) != "org.freedesktop.portal.Error.NotFound":
                raise RuntimeError("Portal grant revocation failed; saved state retained for retry") from None

    def close(self):
        with self.close_lock:
            self.cancelled.set()
            for event in tuple(self.waiters):
                event.set()
            for path in tuple(self.pending):
                try:
                    self.call("org.freedesktop.portal.Request", "Close", "()", (), path=path)
                except Exception:
                    pass
            if self.session:
                try:
                    self.call("org.freedesktop.portal.Session", "Close", "()", (), path=self.session)
                except Exception:
                    pass
                self.session = None
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
