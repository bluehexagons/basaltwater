"""Exercise the managed Syncthing API storage boundary without host changes."""

from __future__ import annotations

from email.message import Message
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sync.service_tools import syncthing_guard as guard


class TestSyncthingGuard(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory()))
        self.root = base / "shares"
        self.state = base / "state"
        self.outside = base / "outside"
        for directory in (self.root, self.state, self.outside):
            directory.mkdir()
        self.config = {
            "gui": {"address": guard.GUI_ADDRESS, "enabled": True,
                    "user": "admin", "password": "test-hash", "apiKey": "test-key"},
            "folders": [{"id": "docs", "path": str(self.root / "docs")}],
            "devices": [],
            "options": {"natEnabled": False},
            "defaults": {"folder": {"path": str(self.root)}, "device": {}},
        }

    def handler(self, method, target):
        handler = object.__new__(guard._ProxyHandler)
        body = json.dumps(self.config).encode()
        handler.headers = Message()
        handler.headers["Host"] = guard.GUI_ADDRESS
        handler.headers["Cookie"] = "session=fixture"
        handler.headers["Content-Length"] = str(len(body))
        handler.command = method
        handler.path = target
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.server = SimpleNamespace(share_root=str(self.root), state=str(self.state))
        handler.send_error = MagicMock()
        handler.send_response_only = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_guard_forwards_valid_gui_write_with_authentication_unchanged(self):
        handler = self.handler("PUT", "/rest/config")
        response = io.BytesIO(b"accepted")
        response.status = 200
        response.headers = Message()
        with patch.object(guard, "_UnixConnection") as connection:
            backend = connection.return_value
            backend.getresponse.return_value = response
            handler.do_PUT()
            backend.putrequest.assert_called_once_with("PUT", "/rest/config", skip_host=True, skip_accept_encoding=True)
            backend.putheader.assert_any_call("Cookie", "session=fixture")
            self.assertEqual(json.loads(backend.endheaders.call_args.args[0]), self.config)
        handler.send_error.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), b"accepted")

    def test_gui_and_legacy_full_config_reject_readable_external_folders(self):
        (self.outside / "sample.txt").write_text("fictional external contents", encoding="utf-8")
        (self.root / "linked").symlink_to(self.outside, target_is_directory=True)
        for folder in (self.outside, self.state, self.root / "linked"):
            self.config["folders"][0]["path"] = str(folder)
            for method, target in (("PUT", "/rest/config"), ("POST", "/rest/system/config")):
                with self.subTest(folder=folder.name, target=target):
                    handler = self.handler(method, target)
                    with patch.object(guard, "_UnixConnection") as connection:
                        handler.proxy()
                        connection.assert_not_called()
                    self.assertEqual(handler.send_error.call_args.args[0], 400)

    def test_partial_configuration_mutation_does_not_reach_backend(self):
        for method, target in (("PATCH", "/rest/config"), ("PUT", "/rest/config/folders/docs"),
                               ("POST", "/rest/config/folders"), ("PUT", "/rest/config/gui")):
            with self.subTest(method=method, target=target):
                handler = self.handler(method, target)
                with patch.object(guard, "_UnixConnection") as connection:
                    handler.proxy()
                    connection.assert_not_called()
                self.assertEqual(handler.send_error.call_args.args[0], 403)

    def test_runtime_policy_refuses_overlapping_folders_and_alternate_storage_settings(self):
        folder = self.config["folders"][0]
        original = folder.copy()
        for added, message in (
            ({"path": str(self.root)}, "overlap"),
            ({"versioning": {"type": "external"}}, "version storage"),
            ({"versioning": {"type": "trashcan", "fsPath": str(self.state)}}, "version storage"),
        ):
            with self.subTest(added=added):
                folder.clear()
                folder.update(original)
                self.config["folders"] = [folder, {**folder, "id": "archive", **added}] if "path" in added else [folder | added]
                with self.assertRaisesRegex(ValueError, message):
                    guard._validate_config_write(json.dumps(self.config).encode(), str(self.root), str(self.state))
        self.config["folders"] = [original]
        self.config["devices"] = [{"autoAcceptFolders": True}]
        with self.assertRaisesRegex(ValueError, "automatic folder acceptance"):
            guard._validate_config_write(json.dumps(self.config).encode(), str(self.root), str(self.state))

    def test_offline_config_checked_before_starting_syncthing(self):
        config = self.state / "config.xml"
        config.write_text(
            f'<configuration><folder path="{self.state}"/>'
            '<options><natEnabled>false</natEnabled></options>'
            '<gui><user>admin</user><password>test-hash</password></gui></configuration>',
            encoding="utf-8",
        )
        with patch.object(guard.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "outside the configured"):
                guard.main(str(self.root), str(self.state))
            process.assert_not_called()
        config.write_text(config.read_text().replace(str(self.state), str(self.root)), encoding="utf-8")
        guard._validate_offline_config(str(self.state), str(self.root))

    def test_router_mapping_is_refused_before_startup_and_on_config_writes(self):
        (self.state / "config.xml").write_text(
            '<configuration><options><natEnabled>true</natEnabled></options></configuration>',
            encoding="utf-8",
        )
        with patch.object(guard.subprocess, "Popen") as process:
            with self.assertRaisesRegex(ValueError, "automatic router mapping"):
                guard.main(str(self.root), str(self.state))
            process.assert_not_called()
        for options in ({}, {"natEnabled": True}):
            self.config["options"] = options
            handler = self.handler("PUT", "/rest/config")
            with patch.object(guard, "_UnixConnection") as connection:
                handler.proxy()
                connection.assert_not_called()
            self.assertEqual(handler.send_error.call_args.args[0], 400)

    def test_mount_aliases_and_nested_mounts_are_refused(self):
        prefix = "1 0 0:1 / / rw - ext4 fixture rw\n"
        for lines, error in (
            (f"2 1 0:1 {self.state} {self.root} rw - ext4 fixture rw\n", "overlaps"),
            (f"2 1 0:1 {self.state} {self.root}/bind rw - ext4 fixture rw\n", "nested mount"),
            (f"2 1 0:2 / {self.root}/disk rw - ext4 fixture rw\n", "nested mount"),
        ):
            with self.subTest(error=error), patch("builtins.open", return_value=io.StringIO(prefix + lines)):
                with self.assertRaisesRegex(ValueError, error):
                    guard._check_storage_mounts(str(self.root), str(self.state))
