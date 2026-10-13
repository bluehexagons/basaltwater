"""Guacamole configuration, installation safety, protocol and authorization tests."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from common.service_tools import guacamole_service as service
from common.service_tools.guacamole_protocol import Parser, filter_instruction, handshake, instruction, read_instruction
from common.web_panel_steps import build_web_panel_manifest, render_web_panel_nginx
from common import web_panel_steps as panel
from desktop import guacamole_steps as setup
from desktop import session_runtime as desktop
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.guacamole_config import validate_guacamole_settings
from plugins.server import build_server_steps


def config(**overrides):
    values = dict(host="desktop-vm", username="agent", system_type="agent_vm", web_panel_port=443, enable_ssl=True, guacamole=True)
    return SetupConfig(**(values | overrides))


class ConfigurationTests(unittest.TestCase):
    def test_flag_and_round_trips_do_not_enable_rdp_ingress(self):
        parser = create_setup_argument_parser("test")
        selected = SetupConfig.from_args(parser.parse_args(["desktop-vm", "agent", "--guacamole", "--web-panel", "--ssl"]), "agent_vm")
        self.assertTrue(selected.guacamole)
        self.assertTrue(selected.include_desktop)
        self.assertFalse(selected.enable_rdp)
        self.assertIn("--guacamole", selected.to_remote_args())
        self.assertIn("--guacamole", selected.to_setup_command())
        saved = SetupConfig.from_dict(selected.host, selected.system_type, selected.to_dict())
        self.assertTrue(saved.guacamole)
        names = [name for name, _ in build_server_steps(selected)]
        self.assertTrue(any("XRDP" in name for name in names))
        self.assertTrue(any("web panel" in name for name in names))

    def test_partial_patch_preserves_custom_panel_and_enablement(self):
        parser = create_setup_argument_parser("test")
        original = config(web_panel_port=9443)
        partial = SetupConfig.from_args(parser.parse_args(["desktop-vm", "agent", "--guacamole"]), "agent_vm")
        merged = merge_setup_configs(original, partial)
        self.assertEqual(merged.web_panel_port, 9443)
        self.assertTrue(merged.enable_ssl)
        self.assertTrue(merged.guacamole)
        validate_guacamole_settings(merged)
        omitted = SetupConfig.from_args(parser.parse_args(["desktop-vm", "agent"]), "agent_vm")
        self.assertTrue(merge_setup_configs(original, omitted).guacamole)
        disabled = SetupConfig.from_args(parser.parse_args(["desktop-vm", "agent", "--no-guacamole"]), "agent_vm")
        self.assertFalse(merge_setup_configs(original, disabled).guacamole)
        self.assertIn("--no-guacamole", disabled.to_remote_args())
        self.assertFalse(config(disable_web_panel=True).guacamole)

    def test_unsupported_plans_fail_before_steps_run(self):
        for overrides in (dict(username="root"), dict(system_type="agent_cachyos"), dict(system_type="server_proxmox"), dict(harden_user=True), dict(guacamole="true"), dict(guacamole=0)):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                config(**overrides)
        for overrides in (dict(enable_ssl=False), dict(web_panel_port=None), dict(machine_type="oci"), dict(enable_rdp=True, rdp_bind_address="192.0.2.1")):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                build_server_steps(config(**overrides))

    def test_only_enabled_panel_publishes_authenticated_route(self):
        nginx = render_web_panel_nginx(["desktop-vm"], 9443, cert_path="/cert.pem", key_path="/key.pem", guacamole=True)
        self.assertIn("auth_basic_user_file", nginx)
        self.assertIn("X-Guacamole-User $remote_user", nginx)
        self.assertIn('proxy_set_header Authorization ""', nginx)
        self.assertIn("if=$basaltwater_web_panel_auth_failure", nginx)
        self.assertNotIn("access_log off", nginx)
        self.assertIn("/run/basaltwater-guacamole/http.sock", nginx)
        self.assertNotIn("auth_basic off", nginx)
        self.assertNotIn("/desktop/", render_web_panel_nginx(["desktop-vm"], 80))
        with self.assertRaises(ValueError):
            render_web_panel_nginx(["desktop-vm"], 80, guacamole=True)
        manifest = build_web_panel_manifest(config(), ["desktop-vm"])
        self.assertTrue(manifest["features"]["guacamole"])
        self.assertIn("https://desktop-vm/desktop/", [entry["url"] for entry in manifest["services"]])


class InstallerTests(unittest.TestCase):
    def test_pinned_architectures_are_available_on_debian_trixie(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch), \
                    patch.object(setup, "read_os_release", return_value=dict(ID="debian", VERSION_CODENAME="trixie")), \
                    patch.object(setup, "run", return_value=subprocess.CompletedProcess([], 0, arch)):
                self.assertEqual(setup.binary_architecture(), arch)

    def test_unsupported_platforms_fail_before_package_mutation(self):
        for distribution, arch in (
            (dict(ID="ubuntu", VERSION_CODENAME="noble"), "amd64"),
            (dict(ID="debian", VERSION_CODENAME="bookworm"), "amd64"),
            (dict(ID="debian", VERSION_CODENAME="trixie"), "riscv64"),
        ):
            with self.subTest(distribution=distribution, arch=arch), \
                    patch.object(setup, "read_os_release", return_value=distribution), \
                    patch.object(setup, "run", return_value=subprocess.CompletedProcess([], 0, arch)) as run:
                with self.assertRaises(RuntimeError):
                    setup.binary_architecture()
                self.assertFalse(any("apt-get" in call.args[0] for call in run.call_args_list))

    def test_download_verifies_reviewed_hash_not_remote_checksum(self):
        payload = b"approved binary"
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(payload)) as request, \
                patch.dict(setup.BINARY_HASHES, amd64=hashlib.sha256(payload).hexdigest()):
            self.assertEqual(setup.download_package("amd64", Path(temporary)).read_bytes(), payload)
            self.assertIn(f"/releases/download/v{setup.BUILD_VERSION}/", request.call_args.args[0])
            self.assertNotIn("latest", request.call_args.args[0])
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(b"tampered")), \
                patch.dict(setup.BINARY_HASHES, amd64=hashlib.sha256(payload).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                setup.download_package("amd64", Path(temporary))

    def test_verified_package_must_match_selected_name_version_and_architecture(self):
        selected = [setup.PACKAGE_NAME, setup.PACKAGE_VERSION, "amd64"]
        for fields in (selected, ["other", *selected[1:]],
                       [selected[0], "0.1", selected[2]], [*selected[:2], "arm64"]):
            with self.subTest(fields=fields), \
                    patch.object(setup, "run", return_value=subprocess.CompletedProcess([], 0, "\n".join(fields))):
                if fields == selected:
                    setup.validate_package(Path("/tmp/verified.deb"), "amd64")
                else:
                    with self.assertRaisesRegex(RuntimeError, "metadata"):
                        setup.validate_package(Path("/tmp/verified.deb"), "amd64")

    def test_unchanged_installed_package_reuses_release_without_download_or_apt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / setup.BUILD_VERSION
            release.mkdir()
            selected = subprocess.CompletedProcess([], 0, f"installed\n{setup.PACKAGE_VERSION}\namd64\n")
            with patch.object(setup, "RELEASE_ROOT", str(root)), patch.object(setup, "RELEASE", str(release)), \
                    patch.object(setup, "_safe_directory"), patch.object(setup, "binary_architecture", return_value="amd64"), \
                    patch.object(setup, "run", return_value=selected) as run, \
                    patch.object(setup, "validate_installed_release") as validate, \
                    patch.object(setup, "download_package") as download:
                setup.install_release()
                validate.assert_called_once_with(release, "amd64")
                download.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][0], "dpkg-query")
                run.return_value = subprocess.CompletedProcess([], 1, "")
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    setup.install_release()
                download.assert_not_called()

    def test_new_release_installs_local_verified_package_and_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / setup.BUILD_VERSION
            def downloaded(arch, directory):
                package = directory / "verified.deb"
                package.write_bytes(b"verified")
                return package
            with patch.object(setup, "RELEASE_ROOT", str(root)), patch.object(setup, "RELEASE", str(release)), \
                    patch.object(setup, "_safe_directory"), patch.object(setup, "binary_architecture", return_value="amd64"), \
                    patch.object(setup, "run", return_value=subprocess.CompletedProcess([], 1, "")) as run, \
                    patch.object(setup, "download_package", side_effect=downloaded), \
                    patch.object(setup, "validate_package") as validate, \
                    patch.object(setup, "validate_installed_release") as installed:
                setup.install_release()
                validate.assert_called_once()
                command = run.call_args.args[0]
                self.assertEqual(command[:2], ["apt-get", "install"])
                self.assertIn("--no-install-recommends", command)
                self.assertTrue(command[-1].endswith("/verified.deb"))
                installed.assert_called_once_with(release, "amd64")
                self.assertEqual(setup.PACKAGES, ("python3-aiohttp",))

    def test_installed_build_metadata_must_match_selected_architecture_and_version(self):
        metadata = dict(upstream_version=setup.VERSION, build_version=setup.BUILD_VERSION,
                        package_version=setup.PACKAGE_VERSION, architecture="amd64", distribution="trixie")
        with patch.object(setup, "validate_release"), patch.object(setup, "read_json_file", return_value=metadata):
            setup.validate_installed_release(Path("/tmp/release"), "amd64")
            with self.assertRaisesRegex(RuntimeError, "unmanaged"):
                setup.validate_installed_release(Path("/tmp/release"), "arm64")

    def test_download_failure_never_installs_or_builds_unverified_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(setup, "RELEASE_ROOT", str(root)), patch.object(setup, "RELEASE", str(root / "release")), \
                    patch.object(setup, "_safe_directory"), patch.object(setup, "binary_architecture", return_value="amd64"), \
                    patch.object(setup, "run", return_value=subprocess.CompletedProcess([], 1, "")) as run, \
                    patch.object(setup, "download_package", side_effect=RuntimeError("checksum mismatch")), \
                    patch.object(setup, "validate_package") as validate:
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    setup.install_release()
                validate.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][0], "dpkg-query")

    def test_namespace_and_fixed_relay_units_are_unprivileged(self):
        units = setup.render_units("agent", 1000, 1000)
        gateway = units["basaltwater-guacamole.service"]
        self.assertIn("PrivateNetwork=true", gateway)
        self.assertIn("User=agent", gateway)
        self.assertIn("NoNewPrivileges=true", gateway)
        self.assertIn("LimitCORE=0", gateway)
        self.assertIn("KillMode=mixed", gateway)
        self.assertIn("Type=notify", gateway)
        self.assertIn("NotifyAccess=main", gateway)
        relay = units["basaltwater-guacamole-rdp.service"]
        self.assertIn("RuntimeDirectoryMode=0700", relay)
        self.assertNotIn("PrivateNetwork=true", relay)
        self.assertNotIn("User=root", "".join(units.values()))

    def test_refuses_unmanaged_units_before_any_mutation(self):
        with patch.object(setup, "snapshot_unit_file", return_value={"content": "unmanaged", "uid": 0, "mode": 0o644}), patch.object(setup, "run") as run:
            with self.assertRaises(RuntimeError):
                setup.remove_guacamole()
            run.assert_not_called()

    def test_slow_and_oversized_binary_downloads_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(b"data")), \
                patch.object(setup.time, "monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, "two minutes"):
                setup.download_package("amd64", Path(temporary))
        with tempfile.TemporaryDirectory() as temporary:
            source = MagicMock()
            source.__enter__.return_value = source
            source.read1.return_value.__len__.return_value = 64 * 1024 * 1024 + 1
            with patch.object(setup.urllib.request, "urlopen", return_value=source):
                with self.assertRaisesRegex(RuntimeError, "64 MiB"):
                    setup.download_package("amd64", Path(temporary))

    def test_configuration_preflight_happens_before_removing_public_ingress(self):
        with patch.object(panel, "_preflight_web_panel_removal", return_value=(None, False)), \
                patch.object(setup, "preflight_units"), \
                patch.object(setup, "preflight_configuration", side_effect=RuntimeError("unsafe")), \
                patch.object(panel, "_remove_nginx_site") as remove:
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                panel.remove_web_panel()
            remove.assert_not_called()

    def test_failed_activation_restores_configuration_and_reloads_prior_service(self):
        for previous in (None, dict(content='{"previous":true}', mode=0o640, uid=0, gid=0)):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.json"
                active = previous is not None
                state = dict(ActiveState="active" if active else "inactive")
                with patch.object(setup, "CONFIG", str(path)), patch.object(setup, "preflight_configuration"), \
                        patch.object(setup, "snapshot_unit_file", return_value=previous), \
                        patch.object(setup, "inspect_unit_state", return_value=state), \
                        patch.object(setup, "replace_units", side_effect=RuntimeError("startup failed")), \
                        patch.object(setup, "write_text_atomic") as restore, patch.object(setup, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "startup failed"):
                        setup.configure_services({"new": True}, {})
                    if active:
                        restore.assert_called_once_with(str(path), **previous)
                        run.assert_called_once_with(["systemctl", "restart", "basaltwater-guacamole.service"])
                    else:
                        self.assertFalse(path.exists())
                        run.assert_not_called()

    def test_release_refuses_mutable_files_and_links_outside_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release"
            release.mkdir(mode=0o755)
            for directory in ("sbin", "share", "lib"):
                (release / directory).mkdir(parents=True, mode=0o755)
            for name in ("sbin/guacd", "share/all.js", "lib/libguac-client-rdp.so.0"):
                (release / name).write_text("fixture")
                (release / name).chmod(0o755 if name.startswith("sbin") else 0o644)
            (release / "lib/libguac-client-rdp.so").symlink_to("libguac-client-rdp.so.0")
            # Inspect real temporary modes, while mocking the target root UID.
            actual_lstat = Path.lstat
            def root_owned(path):
                result = list(actual_lstat(path))
                result[4] = 0
                return os.stat_result(result)
            with patch.object(Path, "lstat", root_owned):
                setup.validate_release(release)
                (release / "share/all.js").chmod(0o666)
                with self.assertRaisesRegex(RuntimeError, "Unsafe.*entry"):
                    setup.validate_release(release)
                (release / "share/all.js").chmod(0o644)
                (release / "share/all.js").unlink()
                (release / "share/all.js").symlink_to(Path(temporary) / "outside.js")
                (Path(temporary) / "outside.js").write_text("outside")
                with self.assertRaisesRegex(RuntimeError, "Unsafe.*link"):
                    setup.validate_release(release)

    def test_socket_refuses_symlink_and_regular_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text("keep")
            link = Path(temporary) / "link"
            link.symlink_to(target)
            for path in (target, link):
                with self.assertRaises(ValueError):
                    service.private_socket(str(path))
            self.assertEqual(target.read_text(), "keep")

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_runs_graceful_service_cleanup(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        async def serve(_config):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        loop = asyncio.get_running_loop()
        with patch.object(service, "serve", side_effect=serve), \
                patch.object(loop, "add_signal_handler") as add, patch.object(loop, "remove_signal_handler") as remove:
            worker = asyncio.create_task(service.run_service(None))
            await asyncio.wait_for(entered.wait(), 1)
            add.call_args.args[1]()
            await asyncio.wait_for(worker, 1)
            self.assertTrue(cleaned.is_set())
            remove.assert_called_once_with(service.signal.SIGTERM)

    def test_ready_notification_supports_filesystem_and_abstract_sockets(self):
        for address, expected in (("/run/systemd/notify", "/run/systemd/notify"), ("@notify", "\0notify")):
            with patch.dict(service.os.environ, NOTIFY_SOCKET=address), patch.object(service.socket, "socket") as socket:
                service.notify_ready()
                socket.return_value.__enter__.return_value.sendto.assert_called_once_with(b"READY=1", expected)

    async def test_native_startup_uses_listen_flag_and_private_pid_file(self):
        process = MagicMock(returncode=1)
        server = MagicMock(wait_closed=AsyncMock())
        runner = MagicMock(cleanup=AsyncMock())
        with patch.object(service.asyncio, "start_server", new=AsyncMock(return_value=server)), \
                patch.object(service.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)) as launch, \
                patch.object(service.web, "AppRunner", return_value=runner):
            with self.assertRaisesRegex(RuntimeError, "guacd failed"):
                await service.serve(dict(username="agent", origins=["https://fixture"]))
        command = launch.call_args.args
        self.assertEqual(command[command.index("-l") + 1], "4822")
        self.assertEqual(command[command.index("-p") + 1], "/run/basaltwater-guacamole/guacd.pid")
        self.assertNotIn("-P", command)
        server.close.assert_called_once()
        runner.cleanup.assert_awaited_once()

    def test_incremental_unicode_and_embedded_delimiters(self):
        parser = Parser()
        text = instruction("test", "😀é,;.")
        results = []
        for character in text:
            results.extend(parser.feed(character))
        self.assertEqual(results, [["test", "😀é,;."]])
        self.assertEqual(parser.buffer, "")

    def test_malformed_and_oversized_instructions_fail(self):
        for text in ("-1.x;", "١.a;", "9999999.x;", "1.ax", "65537.x;", "x" * 65537):
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                Parser().feed(text)

    def test_parser_bounds_each_instruction_and_consumes_completed_elements(self):
        parser = Parser()
        self.assertEqual(parser.feed(instruction("nop") * 12000), [["nop"]] * 12000)
        self.assertEqual(parser.feed("3.key,2.65,"), [])
        self.assertEqual(parser.buffer, "")
        self.assertEqual(parser.feed("1.1;"), [["key", "65", "1"]])
        with self.assertRaises(ValueError):
            parser.feed("1.x," * 257)

    async def test_remote_handoff_is_generation_and_geometry_scoped(self):
        with patch.object(desktop, "request", return_value={}) as request:
            await service.desktop_call("pause", generation="desktop-1", geometry=[1600, 900])
        request.assert_called_once_with({"action": "pause", "generation": "desktop-1", "geometry": [1600, 900]})

    def test_supervisor_rejects_stale_handoffs_before_mutating_pause(self):
        session = desktop.DesktopSession(dict(desktop="i3"), MagicMock())
        session.snapshot_status = MagicMock(return_value=dict(state="running", geometry=[1600, 900]))
        for action in ("pause", "resume"):
            for generation, geometry in (("stale", [1600, 900]), (session.generation, [1280, 720])):
                session.paused = action == "resume"
                with self.assertRaises(ValueError):
                    session.handle({"action": action, "generation": generation, "geometry": geometry})
                self.assertEqual(session.paused, action == "resume")
        session.handle({"action": "pause", "generation": session.generation, "geometry": [1600, 900]})
        self.assertTrue(session.paused)
        session.snapshot_status.return_value["state"] = "stopping"
        with self.assertRaises(RuntimeError):
            session.handle({"action": "resume", "generation": session.generation, "geometry": [1600, 900]})
        self.assertTrue(session.paused)

    def test_modified_client_cannot_input_or_enable_transfers(self):
        self.assertIsNone(filter_instruction(["key", "65", "1"], False))
        self.assertIsNone(filter_instruction(["mouse", "1", "2", "1"], False))
        self.assertEqual(filter_instruction(["key", "65", "1"], True), instruction("key", "65", "1"))
        for elements in (["select", "ssh"], ["connect", "arbitrary"], ["size", "10", "10"], ["clipboard", "0", "text/plain"], ["blob", "0", "secret"], ["key", "65", "2"], ["mouse", "1", "2", "999"], ["sync", "-1"]):
            with self.subTest(elements=elements), self.assertRaises(ValueError):
                filter_instruction(elements, True)

    async def test_native_handshake_fixes_destination_and_pins_certificate(self):
        reader = asyncio.StreamReader()
        names = ["hostname", "port", "username", "password", "cert-fingerprints", "ignore-cert", "disable-copy", "enable-drive", "security"]
        reader.feed_data((instruction("args", "VERSION_1_5_0", *names) + instruction("ready", "$test")).encode())
        writer = MagicMock()
        writer.drain = AsyncMock()
        identifier = await handshake(reader, writer, "agent", "p😀ss", "a" * 64, [1600, 900])
        self.assertEqual(identifier, "$test")
        data = b"".join(call.args[0] for call in writer.write.call_args_list).decode()
        messages = Parser().feed(data)
        connection = next(m for m in messages if m[0] == "connect")
        values = dict(zip(names, connection[2:]))
        self.assertEqual(values["hostname"], "127.0.0.1")
        self.assertEqual(values["port"], "3389")
        self.assertEqual(values["password"], "p😀ss")
        self.assertEqual(values["cert-fingerprints"], "sha256:" + "a" * 64)
        self.assertEqual(values["ignore-cert"], "false")
        self.assertEqual(values["disable-copy"], "true")
        self.assertEqual(values["enable-drive"], "false")

    async def test_native_handshake_requires_pinning_parameter(self):
        reader = asyncio.StreamReader()
        reader.feed_data(instruction("args", "VERSION_1_5_0", "hostname").encode())
        writer = MagicMock(drain=AsyncMock())
        with self.assertRaises(ValueError):
            await handshake(reader, writer, "agent", "secret", "a" * 64, [800, 600])

    async def test_native_instruction_handles_unicode_and_limits(self):
        reader = asyncio.StreamReader()
        reader.feed_data(instruction("test", "😀é").encode())
        self.assertEqual(await read_instruction(reader), ["test", "😀é"])
        reader.feed_data(b"9999999.x;")
        with self.assertRaises(ValueError):
            await read_instruction(reader)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.snapshot = dict(state="running", generation="generation-1", geometry=[1600, 900], paused=False)
        async def desktop_call(action, **scope):
            if scope and (scope["generation"] != self.snapshot["generation"] or scope["geometry"] != self.snapshot["geometry"]):
                raise RuntimeError("Desktop session changed")
            if action == "pause":
                self.snapshot["paused"] = True
            elif action == "resume":
                self.snapshot["paused"] = False
            return dict(self.snapshot)
        self.desktop_call = self.stack.enter_context(patch.object(service, "desktop_call", side_effect=desktop_call))
        self.app = service.create_app(dict(username="agent", origins=["https://desktop-vm:9443"], certificate="a" * 64))
        self.gateway = self.app[service.GATEWAY_KEY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Host": "desktop-vm:9443", "X-Forwarded-Proto": "https", "X-Guacamole-User": "agent", "Origin": "https://desktop-vm:9443"}

    async def asyncTearDown(self):
        await self.client.close()
        self.stack.close()

    async def post(self, endpoint, **fields):
        if endpoint == "disconnect" and "session" not in fields:
            fields["session"] = self.gateway.session.identifier if self.gateway.session else None
        return await self.client.post("/desktop/" + endpoint, headers=self.headers, json={"csrf": self.gateway.csrf, **fields})

    async def session(self):
        response = await self.post("session", password="private-password")
        self.assertEqual(response.status, 200, await response.text())
        return (await response.json())["attachment"]

    async def test_page_headers_and_no_credential_reflection(self):
        response = await self.client.get("/desktop/", headers=self.headers)
        text = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("Take control", text)
        self.assertIn('script src="/desktop/assets/all.js"', text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.desktop_call.assert_not_called()

    async def test_identity_tls_and_origin_are_required(self):
        for header, value in (("Host", "evil.test"), ("X-Forwarded-Proto", "http"), ("X-Guacamole-User", "root"), ("Origin", "https://evil.test")):
            headers = self.headers | {header: value}
            response = await self.client.post("/desktop/session", headers=headers, json={"csrf": self.gateway.csrf, "password": "secret"})
            self.assertEqual(response.status, 403)
        self.desktop_call.assert_not_called()

    async def test_csrf_unknown_fields_duplicate_fields_and_body_limit(self):
        for payload in ({"csrf": "wrong", "password": "secret"}, {"csrf": "☃", "password": "secret"}, {"csrf": self.gateway.csrf, "password": "secret", "hostname": "evil"}):
            response = await self.client.post("/desktop/session", headers=self.headers, json=payload)
            self.assertEqual(response.status, 403)
        response = await self.client.post("/desktop/session", headers=self.headers | {"Content-Type": "application/json"}, data='{"csrf":"a","csrf":"b","password":"secret"}')
        self.assertEqual(response.status, 400)
        response = await self.post("session", password="x" * 9000)
        self.assertEqual(response.status, 413)
        self.desktop_call.assert_not_called()

    async def test_password_is_not_returned_or_represented_and_one_slot_only(self):
        attachment = await self.session()
        self.assertEqual(self.gateway.session.identifier, attachment)
        self.assertNotIn("private-password", repr(self.gateway.session))
        response = await self.post("session", password="second")
        self.assertEqual(response.status, 409)
        self.assertFalse(self.gateway.session.controlled)

    async def test_take_observe_resume_and_disconnect_do_not_implicitly_resume(self):
        attachment = await self.session()
        self.gateway.session.attached = True
        self.gateway.session.ready = True
        result = await self.post("control", session=attachment, action="take")
        self.assertTrue((await result.json())["controlled"])
        self.desktop_call.assert_awaited_with("pause", generation="generation-1", geometry=[1600, 900])
        self.assertTrue(self.snapshot["paused"])
        result = await self.post("control", session=attachment, action="observe")
        self.assertFalse((await result.json())["controlled"])
        self.assertTrue(self.snapshot["paused"])
        await self.post("control", session=attachment, action="resume")
        self.assertFalse(self.snapshot["paused"])
        await self.post("control", session=attachment, action="take")
        old = self.gateway.session
        await self.post("disconnect")
        self.assertIsNone(self.gateway.session)
        self.assertEqual(old.password, "")
        self.assertTrue(self.snapshot["paused"])

    async def test_session_generation_change_blocks_takeover(self):
        attachment = await self.session()
        self.gateway.session.attached = True
        self.gateway.session.ready = True
        self.snapshot["generation"] = "replacement"
        result = await self.post("control", session=attachment, action="take")
        self.assertEqual(result.status, 409)
        self.assertFalse(self.gateway.session.controlled)
        self.assertFalse(self.snapshot["paused"])

    async def test_control_rejects_non_string_actions_without_system_calls(self):
        attachment = await self.session()
        self.desktop_call.reset_mock()
        for action in ([], {}, 1, None):
            result = await self.post("control", session=attachment, action=action)
            self.assertEqual(result.status, 400)
        self.desktop_call.assert_not_called()

    async def test_pending_password_expires_without_connection(self):
        await self.session()
        old = self.gateway.session
        old.created = time.monotonic() - service.ATTACH_TIMEOUT - 1
        worker = asyncio.create_task(self.gateway.expire_pending())
        try:
            await asyncio.sleep(1.1)
            self.assertIsNone(self.gateway.session)
            self.assertEqual(old.password, "")
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_status_revokes_external_resume_without_extending_inactivity(self):
        attachment = await self.session()
        current = self.gateway.session
        current.ready = True
        await self.post("control", session=attachment, action="take")
        activity = current.activity
        self.snapshot["paused"] = False
        result = await self.post("status", session=attachment)
        self.assertEqual(result.status, 200)
        state = await result.json()
        self.assertFalse(state["controlled"])
        self.assertFalse(state["paused"])
        self.assertLessEqual(state["expires_in"], service.MAX_SESSION)
        self.assertLessEqual(state["idle_expires_in"], service.IDLE_TIMEOUT)
        self.assertEqual(current.activity, activity)
        current.activity -= service.IDLE_TIMEOUT + 1
        result = await self.post("control", session=attachment, action="take")
        self.assertEqual(result.status, 409)
        self.assertTrue(current.closed.is_set())
        self.assertFalse(self.snapshot["paused"])

    async def test_stale_disconnect_cannot_revoke_a_replacement_connection(self):
        stale = await self.session()
        await self.post("disconnect", session=stale)
        current = await self.session()
        result = await self.post("disconnect", session=stale)
        self.assertEqual(result.status, 403)
        self.assertEqual(self.gateway.session.identifier, current)
        await self.post("disconnect", session=None)
        self.assertIsNone(self.gateway.session)

    async def test_disconnect_cancels_pending_login_and_cannot_resurrect_slot(self):
        attachment = await self.session()
        current = self.gateway.session
        reader = asyncio.StreamReader()
        writer = MagicMock(drain=AsyncMock(), is_closing=MagicMock(return_value=False))
        entered = asyncio.Event()
        canceled = asyncio.Event()
        async def pending_login(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
        self.stack.enter_context(patch.object(service.asyncio, "open_connection", new=AsyncMock(return_value=(reader, writer))))
        self.stack.enter_context(patch.object(service, "handshake", side_effect=pending_login))
        connection = asyncio.create_task(self.client.ws_connect("/desktop/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",)))
        await asyncio.wait_for(entered.wait(), 2)
        response = await self.post("disconnect", session=attachment)
        self.assertEqual(response.status, 200)
        replacement = await self.session()
        with self.assertRaises(Exception) as failed:
            await asyncio.wait_for(connection, 2)
        self.assertEqual(failed.exception.status, 409)
        self.assertTrue(canceled.is_set())
        self.assertEqual(current.password, "")
        self.assertFalse(current.ready)
        writer.close.assert_called()
        self.assertEqual(self.gateway.session.identifier, replacement)

    async def test_websocket_consumes_ticket_and_filters_modified_client_input(self):
        attachment = await self.session()
        reader = asyncio.StreamReader()
        writer = MagicMock(drain=AsyncMock(), is_closing=MagicMock(return_value=False))
        self.stack.enter_context(patch.object(service.asyncio, "open_connection", new=AsyncMock(return_value=(reader, writer))))
        handshake_mock = self.stack.enter_context(patch.object(service, "handshake", new=AsyncMock(return_value="$native")))
        ws = await self.client.ws_connect("/desktop/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        self.assertEqual((await ws.receive()).data, instruction("", "$native"))
        self.assertEqual(self.gateway.session.password, "")
        self.assertFalse(self.gateway.session.ready)
        result = await self.post("control", session=attachment, action="take")
        self.assertEqual(result.status, 409)
        reader.feed_data(instruction("sync", "1").encode())
        self.assertEqual((await ws.receive()).data, instruction("sync", "1"))
        self.assertTrue(self.gateway.session.ready)
        handshake_mock.assert_awaited_once()
        with self.assertRaises(Exception) as replay:
            await self.client.ws_connect("/desktop/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        self.assertEqual(replay.exception.status, 403)
        await ws.send_str(instruction("key", "65", "1") + instruction("sync", "1"))
        await asyncio.sleep(.05)
        self.assertEqual(b"".join(c.args[0] for c in writer.write.call_args_list), instruction("sync", "1").encode())
        await self.post("control", session=attachment, action="take")
        await ws.send_str(instruction("key", "65", "1"))
        await asyncio.sleep(.05)
        self.assertIn(instruction("key", "65", "1").encode(), [c.args[0] for c in writer.write.call_args_list])
        writer.write.reset_mock()
        self.snapshot["paused"] = False
        await ws.send_str(instruction("key", "65", "1") + instruction("sync", "2"))
        await asyncio.sleep(.05)
        self.assertEqual(b"".join(c.args[0] for c in writer.write.call_args_list), (instruction("key", "65", "0") + instruction("sync", "2")).encode())
        await ws.send_str(instruction("clipboard", "1", "text/plain"))
        message = await ws.receive(timeout=2)
        self.assertIn(message.type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
        await ws.close()

    async def test_takeover_is_blocked_until_native_login_and_websocket_are_ready(self):
        attachment = await self.session()
        self.gateway.session.attached = True
        result = await self.post("control", session=attachment, action="take")
        self.assertEqual(result.status, 409)
        self.assertFalse(self.snapshot["paused"])

    async def test_held_keys_and_buttons_are_released_when_control_ends(self):
        attachment = await self.session()
        current = self.gateway.session
        current.attached = current.ready = current.controlled = True
        current.upstream = MagicMock(is_closing=MagicMock(return_value=False))
        current.keys = {"65507", "65"}
        current.pointer = ["100", "200"]
        await self.post("control", session=attachment, action="observe")
        messages = {call.args[0] for call in current.upstream.write.call_args_list}
        self.assertIn(instruction("key", "65507", "0").encode(), messages)
        self.assertIn(instruction("key", "65", "0").encode(), messages)
        self.assertIn(instruction("mouse", "100", "200", "0").encode(), messages)
        self.assertFalse(current.keys)
        self.assertIsNone(current.pointer)

    async def test_application_shutdown_releases_input_and_revokes_session(self):
        await self.session()
        current = self.gateway.session
        current.ready = current.controlled = True
        current.keys.add("65")
        current.upstream = MagicMock(is_closing=MagicMock(return_value=False))
        await self.app.shutdown()
        self.assertTrue(current.closed.is_set())
        self.assertEqual(current.password, "")
        self.assertFalse(current.controlled)
        current.upstream.write.assert_called_once_with(instruction("key", "65", "0").encode())
        current.upstream.close.assert_called()

    async def test_upstream_login_failure_discards_password_and_frees_slot(self):
        attachment = await self.session()
        reader = asyncio.StreamReader()
        writer = MagicMock(drain=AsyncMock())
        self.stack.enter_context(patch.object(service.asyncio, "open_connection", new=AsyncMock(return_value=(reader, writer))))
        self.stack.enter_context(patch.object(service, "handshake", new=AsyncMock(side_effect=ValueError("private-password"))))
        with self.assertRaises(Exception) as failed:
            await self.client.ws_connect("/desktop/ws?attachment=" + attachment, headers=self.headers, protocols=("guacamole",))
        self.assertEqual(failed.exception.status, 502)
        self.assertIsNone(self.gateway.session)

    async def test_watch_closes_for_idle_absolute_limit_or_generation_change(self):
        for mode in ("idle", "absolute", "generation"):
            await self.session()
            current = self.gateway.session
            if mode == "idle":
                current.activity -= service.IDLE_TIMEOUT + 1
            elif mode == "absolute":
                current.created -= service.MAX_SESSION + 1
            else:
                self.snapshot["generation"] = "replacement"
            with patch.object(service.asyncio, "sleep", new=AsyncMock()):
                await asyncio.wait_for(self.gateway.watch(current), 1)
            await self.post("disconnect")
            self.snapshot["generation"] = "generation-1"


if __name__ == "__main__":
    unittest.main()
