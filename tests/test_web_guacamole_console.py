"""Console login boundaries, independent sessions and fixed SSH parameters."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from common.service_tools import guacamole_console as console, guacamole_service as service
from common.service_tools.guacamole_protocol import Parser, filter_instruction, handshake, instruction
from common.service_tools.web_panel_features import PanelFeatures
from desktop import guacamole_steps as setup
from lib.guacamole_config import SSH_SERVICE

PIN = "[127.0.0.1]:2222 ssh-ed25519 " + "A" * 68


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.desktop = self.stack.enter_context(patch.object(service, "desktop_call", new=AsyncMock(side_effect=AssertionError("Console must not touch desktop"))))
        self.stack.enter_context(patch.object(service.pwd, "getpwnam", return_value=MagicMock(pw_dir="/home/agent")))
        self.discovery = self.stack.enter_context(patch.object(service, "discover_features", return_value=PanelFeatures()))
        self.app = service.create_app(dict(username="agent", origins=["https://vm"],
                                          certificate="a" * 64, ssh_host_key=PIN))
        self.gateway = self.app[console.CONSOLE_KEY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Host": "vm", "X-Forwarded-Proto": "https", "X-Guacamole-User": "agent", "Origin": "https://vm"}

    async def asyncTearDown(self):
        await self.client.close()
        self.desktop.assert_not_called()
        self.stack.close()

    async def post(self, endpoint, **fields):
        return await self.client.post("/console/" + endpoint, headers=self.headers,
                                      json={"csrf": self.gateway.csrf, **fields})

    async def connect(self, **overrides):
        return await self.post("session", **(dict(password="private-password", width=800, height=420, font_size=12) | overrides))

    async def test_page_is_read_only_local_scripts_and_desktop_choice(self):
        result = await self.client.get("/console/", headers=self.headers)
        self.assertEqual(result.status, 200)
        page = await result.text()
        self.assertIn("Connect to console", page)
        self.assertIn('href="/desktop/"', page)
        self.assertIn('/console/assets/console.js', page)
        self.assertIn('id="console-keyboard"', page)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", result.headers["Content-Security-Policy"])
        self.assertIsNone(self.gateway.session)

    async def test_identity_origin_csrf_and_no_client_selected_destinations(self):
        for changed in ({"Origin": "https://evil"}, {"Host": "evil"}, {"X-Forwarded-Proto": "http"}, {"X-Guacamole-User": "root"}):
            result = await self.client.post("/console/session", headers=self.headers | changed, json={})
            self.assertEqual(result.status, 403)
        self.assertEqual((await self.connect(hostname="evil")).status, 403)
        result = await self.client.post("/console/session", headers=self.headers, json={"csrf": self.app[service.GATEWAY_KEY].csrf})
        self.assertEqual(result.status, 403)
        self.assertEqual((await self.client.get("/console/?host=evil", headers=self.headers)).status, 400)
        self.assertEqual((await self.client.get("/console/assets/unknown.js", headers=self.headers)).status, 404)
        self.assertIsNone(self.gateway.session)

    async def test_both_access_pages_keep_installed_navigation_and_hide_unavailable_sections(self):
        self.gateway.config["admin"] = True
        for features in (PanelFeatures(), PanelFeatures(tools=frozenset({"codex", "git", "butler"}),
                                                        maintenance=True, admin=True)):
            self.discovery.return_value = features
            for path in ("/desktop/", "/console/"):
                with self.subTest(path=path, tools=features.tools):
                    response = await self.client.get(path, headers=self.headers)
                    self.assertEqual(response.status, 200)
                    page = await response.text()
                    for href in ("/desktop/", "/console/"):
                        self.assertIn(f'href="{href}"', page)
                    for href in ("/agents", "/credentials", "/publishing", "/admin", "/maintenance-agent"):
                        self.assertEqual(f'href="{href}"' in page, bool(features.tools))
            self.discovery.assert_called_with("/home/agent", admin=True)

    async def test_dimensions_and_font_bounds_reject_before_creating_session(self):
        for values in (dict(width=True), dict(width=239), dict(width=3841), dict(height=[]),
                       dict(height=0), dict(height=2161), dict(font_size=True), dict(font_size=99),
                       dict(password=[]), dict(password=""), dict(password="a\0b")):
            with self.subTest(values=values):
                self.assertEqual((await self.connect(**values)).status, 400)
                self.assertIsNone(self.gateway.session)

    async def test_pending_password_one_slot_replay_stale_disconnect_and_expiry(self):
        response = await self.connect()
        attachment = (await response.json())["attachment"]
        self.assertNotIn("private-password", await response.text())
        old = self.gateway.session
        self.assertNotIn("private-password", repr(old))
        self.assertEqual((await self.connect()).status, 409)
        self.assertIsNone(self.app[service.GATEWAY_KEY].session)
        old.created -= service.ATTACH_TIMEOUT + 1
        self.assertEqual((await self.connect()).status, 200)
        self.assertTrue(old.closed.is_set())
        self.assertEqual(old.password, "")
        self.assertEqual((await self.post("disconnect", session=attachment)).status, 403)
        current = self.gateway.session
        activity = current.activity
        self.assertEqual((await self.post("status", session=current.identifier)).status, 200)
        self.assertEqual(current.activity, activity)
        current.activity -= service.IDLE_TIMEOUT + 1
        self.assertEqual((await self.post("status", session=current.identifier)).status, 409)
        self.assertTrue(current.closed.is_set())

    async def test_native_tunnel_input_resize_forbidden_transfers_and_disconnect(self):
        attachment = (await (await self.connect()).json())["attachment"]
        reader = asyncio.StreamReader()
        writer = MagicMock(drain=AsyncMock(), is_closing=MagicMock(return_value=False))
        self.stack.enter_context(patch.object(console.asyncio, "open_connection", new=AsyncMock(return_value=(reader, writer))))
        native = self.stack.enter_context(patch.object(console, "handshake", new=AsyncMock(return_value="$console")))
        ws = await self.client.ws_connect("/console/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        await ws.receive()
        session = self.gateway.session
        self.assertEqual(session.password, "")
        self.assertFalse(session.controlled)
        native.assert_awaited_once()
        self.assertEqual(native.call_args.kwargs, dict(ssh_host_key=PIN, font_size="12"))
        with self.assertRaises(Exception) as replay:
            await self.client.ws_connect("/console/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        self.assertEqual(replay.exception.status, 403)
        reader.feed_data(instruction("sync", "1").encode())
        await ws.receive()
        self.assertTrue(session.controlled)
        await ws.send_str(instruction("key", "65", "1") + instruction("size", "390", "420"))
        await asyncio.sleep(.03)
        writes = [call.args[0] for call in writer.write.call_args_list]
        self.assertIn(instruction("key", "65", "1").encode(), writes)
        self.assertIn(instruction("size", "390", "420").encode(), writes)
        await ws.send_str(instruction("clipboard", "0", "text/plain"))
        self.assertIn((await ws.receive(timeout=2)).type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
        await ws.close()
        self.assertTrue(session.closed.is_set())
        writer.close.assert_called()

    async def test_login_failure_never_reflects_password_and_allows_reconnect(self):
        attachment = (await (await self.connect()).json())["attachment"]
        self.stack.enter_context(patch.object(self.gateway, "login", new=AsyncMock(side_effect=ValueError("private-password"))))
        with self.assertRaises(Exception) as failure:
            await self.client.ws_connect("/console/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        self.assertEqual(failure.exception.status, 502)
        self.assertIsNone(self.gateway.session)
        self.assertEqual((await self.connect()).status, 200)

    async def test_shutdown_and_pending_timeout_discard_password(self):
        await self.connect()
        current = self.gateway.session
        current.created -= service.ATTACH_TIMEOUT + 1
        worker = asyncio.create_task(self.gateway.expire_pending())
        try:
            await asyncio.sleep(1.1)
            self.assertIsNone(self.gateway.session)
            self.assertEqual(current.password, "")
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await self.connect()
        current = self.gateway.session
        await self.app.shutdown()
        self.assertTrue(current.closed.is_set())
        self.assertEqual(current.password, "")

    async def test_handshake_pins_ssh_endpoint_and_disables_files_recording_and_commands(self):
        names = ["hostname", "port", "username", "password", "host-key", "enable-sftp", "disable-copy",
                 "disable-paste", "command", "private-key", "typescript-path", "recording-path", "font-size"]
        reader = asyncio.StreamReader()
        reader.feed_data((instruction("args", "VERSION_1_5_0", *names) + instruction("ready", "$shell")).encode())
        writer = MagicMock(drain=AsyncMock())
        await handshake(reader, writer, "agent", "test", "", [390, 420], ssh_host_key=PIN, font_size="16")
        messages = Parser().feed(b"".join(call.args[0] for call in writer.write.call_args_list).decode())
        self.assertEqual(messages[0], ["select", "ssh"])
        values = dict(zip(names, next(row for row in messages if row[0] == "connect")[2:]))
        self.assertEqual(values, dict(hostname="127.0.0.1", port="2222", username="agent", password="test",
                                    **{"host-key": PIN, "enable-sftp": "false", "disable-copy": "true", "disable-paste": "true",
                                       "command": "", "private-key": "", "typescript-path": "", "recording-path": "", "font-size": "16"}))
        for values in (["size", "1", "420"], ["size", "800", "99999"], ["select", "rdp"]):
            with self.assertRaises(ValueError):
                filter_instruction(values, True, allow_resize=True)


class ConsoleInstallationTests(unittest.TestCase):
    def test_private_login_does_not_read_public_sshd_configuration_or_allow_other_accounts(self):
        value = setup.ssh_configuration("agent")
        for expected in ("AllowUsers agent", "PermitRootLogin no", "PasswordAuthentication yes", "UsePAM yes",
                         "AllowTcpForwarding no", "AllowStreamLocalForwarding no", "AuthorizedKeysFile none"):
            self.assertIn(expected, value)
        self.assertNotIn("Include", value)
        self.assertNotIn("ListenAddress", value)
        for username in ("root", "agent\nAllowUsers root", "../agent"):
            with self.assertRaises(ValueError):
                setup.ssh_configuration(username)
        units = setup.render_units("agent", 1000, 1000)
        login = units[f"{SSH_SERVICE}@.service"]
        self.assertIn("PartOf=" + SSH_SERVICE + ".socket", login)
        self.assertIn("CollectMode=inactive-or-failed\n[Service]", login)
        for name, unit in units.items():
            if name != f"{SSH_SERVICE}@.service":
                self.assertNotIn("CollectMode=", unit)
        self.assertNotIn("SuccessExitStatus=", login)
        self.assertIn("RuntimeMaxSec=3600", login)
        self.assertIn("RuntimeDirectory=sshd", login)
        self.assertIn("RuntimeDirectoryPreserve=yes", login)
        self.assertNotIn("NoNewPrivileges", login)  # Normal Unix shell and sudo permissions.

    def test_host_key_refuses_unsafe_links_before_running_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "private"
            target.write_text("do not read")
            link = Path(directory) / "key"
            link.symlink_to(target)
            with patch.object(setup, "SSH_HOST_KEY", str(link)), patch.object(setup, "_safe_directory"), patch.object(setup, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    setup.prepare_ssh_host_key()
                run.assert_not_called()
