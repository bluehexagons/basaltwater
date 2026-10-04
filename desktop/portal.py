"""KDE's user-approved RemoteDesktop/ScreenCast session, without restored grants."""

from __future__ import annotations

import os
import secrets
import threading

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"


class Portal:
    def __init__(self, closed):
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
        self.closed = closed
        self.bus.connect("closed", lambda *_: closed())
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
            "NameOwnerChanged", "/org/freedesktop/DBus", BUS_NAME,
            Gio.DBusSignalFlags.NONE, self.owner_changed)

    def owner_changed(self, _bus, _sender, _path, _interface, _signal, parameters, *_):
        _name, old_owner, new_owner = parameters.unpack()
        if old_owner and old_owner != new_owner:
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
        try:
            options = {**options, "handle_token": self.GLib.Variant("s", token)}
            returned = self.call(interface, method, signature, (*values, options)).unpack()[0]
            if returned != path:
                raise RuntimeError("Portal returned an unexpected request handle")
            if not event.wait(120):
                raise RuntimeError("Portal consent timed out; retry start when ready")
            code, fields = result[0]
            if code:
                raise RuntimeError("Portal permission was cancelled or denied; no desktop control granted")
            return fields
        finally:
            self.bus.signal_unsubscribe(subscription)
            self.pending.discard(path)
            if not result:
                try:
                    self.call("org.freedesktop.portal.Request", "Close", "()", (), path=path)
                except Exception:
                    pass

    def start(self):
        v = self.GLib.Variant
        created = self.response(REMOTE, "CreateSession", "(a{sv})", (),
            {"session_handle_token": v("s", "bw" + secrets.token_hex(12))})
        self.session = created["session_handle"]
        self.bus.signal_subscribe(BUS_NAME, "org.freedesktop.portal.Session", "Closed",
            self.session, None, self.Gio.DBusSignalFlags.NONE, lambda *_: self.closed())
        self.response(REMOTE, "SelectDevices", "(oa{sv})", (self.session,),
            {"types": v("u", 3), "persist_mode": v("u", 0)})
        # Monitor only: pointer coordinates must map to the user's selected stream.
        self.response(SCREENCAST, "SelectSources", "(oa{sv})", (self.session,),
            {"types": v("u", 1), "multiple": v("b", False), "cursor_mode": v("u", 1)})
        started = self.response(REMOTE, "Start", "(osa{sv})", (self.session, ""), {})
        if started.get("devices", 0) & 3 != 3 or len(started.get("streams", [])) != 1:
            raise RuntimeError("Select one monitor and grant keyboard and pointer access to use automation")
        node, properties = started["streams"][0]
        size = properties.get("logical_size", properties.get("size"))
        if not isinstance(size, (tuple, list)) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
            raise RuntimeError("Portal did not report the selected monitor's logical size")
        reply, descriptors = self.bus.call_with_unix_fd_list_sync(BUS_NAME, OBJECT, SCREENCAST,
            "OpenPipeWireRemote", v("(oa{sv})", (self.session, {})), None,
            self.Gio.DBusCallFlags.NO_AUTO_START, 3000, None, None)
        self.fd = descriptors.get(reply.unpack()[0])
        return node, list(size)

    def notify(self, method, signature, *values):
        self.call(REMOTE, method, "(oa{sv}" + signature + ")", (self.session, {}, *values))

    def close(self):
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
