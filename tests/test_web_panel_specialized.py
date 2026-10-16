"""Exercise specialized development adapters through the real HTTP handlers."""

from __future__ import annotations

import base64
from contextlib import ExitStack
from http.cookies import SimpleCookie
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from lib.atomic_io import read_json_file
from scripts.panel_dev import ENTRY_PATHS
from scripts.panel_specialized import create_app


class SpecializedDevelopmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.directory / "home").mkdir(mode=0o700)
        self.enterContext(patch.object(subprocess, "run", side_effect=AssertionError("Unexpected command")))
        self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("Unexpected process")))

    async def serve(self, surface="connect", scenario="full"):
        stack = ExitStack()
        self.addCleanup(stack.close)
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        self.settings = {"surface": surface, "scenario": scenario, "https": surface in {"desktop", "console"},
            "url": f"{'https' if surface in {'desktop', 'console'} else 'http'}://127.0.0.1:{port}",
            "entry": ENTRY_PATHS[surface], "username": "developer", "password": "sample-only-disposable", "refresh": True}
        app = create_app(self.directory, self.settings, stack)
        server = TestServer(app, socket_factory=lambda *args: listener)
        self.client = TestClient(server, headers={"Authorization": "Basic " + base64.b64encode(b"developer:sample-only-disposable").decode()})
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.origin = self.settings["url"]

    async def nonce(self, path="/connect/t3code"):
        response = await self.client.get(path)
        body = await response.text()
        cookie = SimpleCookie(response.headers["Set-Cookie"])
        nonce = cookie["basaltwater_pairing_nonce"].value
        return nonce, {"Cookie": f"basaltwater_pairing_nonce={nonce}"}, body

    async def test_connect_input_validation_completion_unlink_and_nonce_replay(self):
        await self.serve()
        nonce, cookie, body = await self.nonce()
        self.assertIn('id="panel-development"', body)
        fields = {"nonce": nonce, "intent": "start"}
        response = await self.client.post("/connect/t3code", data=fields, headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("Authorization in progress", await response.text())
        self.assertEqual((await self.client.post("/connect/t3code", data=fields, headers=cookie)).status, 403)
        nonce, cookie, _ = await self.nonce()
        response = await self.client.post("/connect/t3code", data={"nonce": nonce, "intent": "input", "input": "bad\ncode"}, headers=cookie)
        self.assertEqual(response.status, 400)
        nonce, cookie, _ = await self.nonce()
        response = await self.client.post("/connect/t3code", data={"nonce": nonce, "intent": "input", "input": "discard-this-sample"}, headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("Authorization completed", await response.text())
        saved = (self.directory / "home/specialized.json").read_text()
        self.assertNotIn("discard-this-sample", saved)
        self.assertTrue(read_json_file(str(self.directory / "home/specialized.json"))["linked"])
        nonce, cookie, _ = await self.nonce()
        response = await self.client.post("/connect/t3code", data={"nonce": nonce, "intent": "toggle"}, headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertFalse(read_json_file(str(self.directory / "home/specialized.json"))["desired"])
        self.assertTrue((self.directory / "home/restart-request").exists())

    async def test_pairing_fragment_destination_and_real_rate_limit(self):
        await self.serve("pairing")
        for index in range(6):
            nonce, cookie, _ = await self.nonce("/devices")
            response = await self.client.post("/pair/t3code", data={"nonce": nonce, "intent": "other"}, headers=cookie)
            self.assertEqual(response.status, 200 if index < 5 else 429)
            if index == 0:
                self.assertIn(self.origin + "/pair#token=synthetic-only", await response.text())
        self.assertIn("Synthetic destination", await (await self.client.get("/pair")).text())

    async def test_approval_origin_decision_replay_and_persistent_state(self):
        await self.serve("approval")
        path = "/requests/" + "a" * 32
        body = await (await self.client.get(path)).text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', body[body.index('<h1>Privilege approvals'):])[1]
        fields = {"csrf": csrf, "digest": "a" * 64, "decision": "approve"}
        self.assertEqual((await self.client.post(path, data=fields, headers={"Origin": "https://wrong.example"})).status, 409)
        response = await self.client.post(path, data=fields, headers={"Origin": self.origin}, allow_redirects=False)
        self.assertEqual(response.status, 303)
        self.assertEqual((await self.client.post(path, data=fields, headers={"Origin": self.origin})).status, 409)
        saved = read_json_file(str(self.directory / "home/specialized.json"))
        self.assertEqual(saved["records"]["a" * 32]["state"], "approved")

    async def test_authentication_host_and_development_csrf(self):
        await self.serve()
        for path in ("/devices", "/__dev__/status", "/__dev__/reload.js"):
            self.assertEqual((await self.client.get(path, headers={"Authorization": ""})).status, 401)
        self.assertEqual((await self.client.get("/devices", headers={"Host": "attacker.example"})).status, 403)
        response = await self.client.get("/devices", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "attacker.example"})
        self.assertNotIn("; Secure", response.headers["Set-Cookie"])
        body = await response.text()
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        fields = {"csrf": "forged", "scenario": "empty", "action": "scenario"}
        self.assertEqual((await self.client.post("/__dev__/control", data=fields)).status, 403)
        self.assertFalse((self.directory / "request.json").exists())
        fields["csrf"] = csrf
        self.assertEqual((await self.client.post("/__dev__/control", data=fields, allow_redirects=False)).status, 303)
        self.assertEqual(read_json_file(str(self.directory / "request.json")), {"scenario": "empty"})
        with self.assertRaisesRegex(RuntimeError, "Unexpected host command"):
            subprocess.run(["systemctl", "restart", "example.service"])
        with self.assertRaisesRegex(AssertionError, "Unexpected process"):
            subprocess.Popen(["synthetic-command-never-executed"])

    async def test_degraded_provider_failures(self):
        await self.serve(scenario="degraded")
        nonce, cookie, _ = await self.nonce()
        response = await self.client.post("/connect/t3code", data={"nonce": nonce, "intent": "start"}, headers=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("Authorization did not complete", await response.text())
        nonce, cookie, _ = await self.nonce("/devices")
        self.assertEqual((await self.client.post("/pair/t3code", data={"nonce": nonce, "intent": "other"}, headers=cookie)).status, 502)

    async def test_empty_approval_has_no_requests(self):
        await self.serve("approval", "empty")
        body = await (await self.client.get("/")).text()
        self.assertIn('id="panel-development"', body)
        self.assertNotIn('href="/requests/', body)

    async def test_desktop_and_console_keep_csrf_and_origin_checks(self):
        await self.serve("desktop")
        for surface in ("desktop", "console"):
            response = await self.client.get(f"/{surface}/")
            self.assertEqual(response.status, 200)
            body = await response.text()
            self.assertIn('id="panel-development"', body)
            csrf = re.search(r'id="csrf" value="([^"]+)"', body)[1]
            fields = {"csrf": csrf, "password": "sample-discarded"}
            if surface == "console":
                fields.update(width=640, height=480, font_size=12)
            path = f"/{surface}/session"
            self.assertEqual((await self.client.post(path, json=fields, headers={"Origin": "https://wrong.example"})).status, 403)
            self.assertEqual((await self.client.post(path, json={**fields, "csrf": "forged"}, headers={"Origin": self.origin})).status, 403)
            response = await self.client.post(path, json=fields, headers={"Origin": self.origin})
            self.assertEqual(response.status, 200)
            self.assertIn("attachment", await response.json())


if __name__ == "__main__":
    unittest.main()
