"""Tests for basaltwater.json manifest deployment wiring (phase 2).

Covers the systemd unit generator, component -> nginx descriptor mapping, the
DeploymentOrchestrator.deploy_manifest flow (with run/service/health mocked),
and that the generated descriptors drive correct nginx config.
"""

from __future__ import annotations

import json
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.deployment import DeploymentOrchestrator
from lib.local_http import open_loopback
from lib.nginx_config import generate_merged_nginx_config
from lib.operation_state import OperationStateError, OperationStateStore
from lib.project_manifest import Component, Manifest, parse_manifest
from lib.systemd_service import generate_managed_service


def _static_component(**overrides: object) -> Component:
    data = {
        "name": "site",
        "type": "static",
        "domain": "example.com",
        "build": "npm ci && npm run build",
        "output": "dist",
    }
    data.update(overrides)
    return parse_manifest({"version": 1, "components": [data]}).components[0]


def _service_component(**overrides: object) -> Component:
    data = {
        "name": "api",
        "type": "service",
        "domain": "api.example.com",
        "build": "server/build.sh",
        "binary": "server/app",
        "port": 8090,
    }
    data.update(overrides)
    return parse_manifest({"version": 1, "components": [data]}).components[0]


class TestLoopbackReadiness(unittest.TestCase):
    @patch.dict(os.environ, {"http_proxy": "http://proxy.invalid:8888", "no_proxy": ""}, clear=True)
    def test_proxy_environment_is_ignored_and_redirects_never_followed(self):
        requests = []

        def respond(_handler, request):
            requests.append(request)
            response = urllib.response.addinfourl(
                io.BytesIO(b""), {"Location": "http://external.invalid/health"},
                request.full_url, 302,
            )
            response.msg = "Found"
            return response

        with patch.object(urllib.request.HTTPHandler, "http_open", respond):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                open_loopback("http://127.0.0.1:8080/health", timeout=1)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].host, "127.0.0.1:8080")
        self.assertFalse(requests[0].has_proxy())

    def test_non_loopback_urls_are_rejected_before_opening(self):
        with patch("urllib.request.build_opener") as opener:
            for url in ("http://example.com/", "http://192.0.2.1/", "file:///etc/passwd", "http://user@127.0.0.1/"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    open_loopback(url, timeout=1)
            opener.assert_not_called()


class TestGenerateManagedService(unittest.TestCase):
    def test_includes_exec_and_hardening(self):
        unit = generate_managed_service(
            "app-api", "/var/www/site/server/app", "/var/www/site",
            web_user="app-site-api", web_group="app-site-api",
        )
        self.assertIn("ExecStart=/var/www/site/server/app", unit)
        self.assertIn("WorkingDirectory=/var/www/site", unit)
        self.assertIn("User=app-site-api", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("PrivateDevices=true", unit)
        self.assertIn("[Install]", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_env_file_optional(self):
        without = generate_managed_service("app-api", "/bin/app", "/srv")
        self.assertNotIn("EnvironmentFile=", without)

        with_env = generate_managed_service(
            "app-api", "/bin/app", "/srv", env_file="/opt/app/.env"
        )
        self.assertIn("EnvironmentFile=/opt/app/.env", with_env)

    def test_runtime_environment_is_written_and_escaped(self):
        unit = generate_managed_service(
            "app-api", "/bin/app", "/srv",
            runtime_env={"APP_DATA": "/var/lib/app", "APP_QUOTE": 'a"b'},
        )
        self.assertIn('Environment="APP_DATA=/var/lib/app"', unit)
        self.assertIn('Environment="APP_QUOTE=a\\"b"', unit)

    def test_writable_paths_are_explicit(self):
        unit = generate_managed_service(
            "app-api", "/bin/app", "/srv", writable_paths=["/var/lib/app"]
        )
        self.assertIn("ReadWritePaths=/var/lib/app", unit)

    def test_rejects_systemd_privilege_prefix(self):
        with self.assertRaisesRegex(ValueError, "privilege-control prefixes"):
            generate_managed_service("app-api", "+/bin/sh", "/srv")

    def test_rejects_multiline_unit_values(self):
        with self.assertRaisesRegex(ValueError, "control characters"):
            generate_managed_service(
                "app-api",
                "/bin/true",
                "/srv\nUser=root",
            )


class TestComponentDescriptor(unittest.TestCase):
    def setUp(self):
        self.orch = DeploymentOrchestrator(base_dir="/var/www")

    def test_static_descriptor(self):
        dep = self.orch._component_dep(_static_component(), "/var/www/site")
        self.assertEqual(dep['project_type'], 'static')
        self.assertFalse(dep['needs_proxy'])
        self.assertEqual(dep['serve_path'], "/var/www/site/dist")
        self.assertEqual(dep['domain'], "example.com")
        self.assertIsNone(dep['backend_port'])

    def test_service_descriptor(self):
        dep = self.orch._component_dep(_service_component(), "/var/www/site")
        self.assertEqual(dep['project_type'], 'service')
        self.assertTrue(dep['needs_proxy'])
        self.assertEqual(dep['backend_port'], 8090)
        self.assertEqual(dep['proxy_port'], 8090)
        self.assertEqual(dep['domain'], "api.example.com")
        self.assertNotIn('api_subdomain', dep)
        self.assertTrue(dep['preserve_path'])

    def test_deploy_domain_placeholder(self):
        dep = self.orch._component_dep(
            _static_component(domain="{{domain}}"), "/var/www/site", "deployed.example"
        )
        self.assertEqual(dep['domain'], "deployed.example")

    def test_service_identity(self):
        unit, user = self.orch._service_identity("/var/www/example_com", _service_component())
        self.assertEqual(unit, "app-example_com-api")
        self.assertEqual(user, "app-example_com-api")

    def test_service_identity_caps_username(self):
        comp = _service_component(name="a-very-long-component-name-indeed")
        unit, user = self.orch._service_identity("/var/www/some_long_app_dir", comp)
        # systemd unit name keeps the full identity; the Linux username is capped.
        self.assertGreater(len(unit), 31)
        self.assertLessEqual(len(user), 31)
        self.assertTrue(user.startswith("app-"))

    def test_working_dir_defaults_to_release(self):
        ctx = self.orch._service_context(_service_component(), "/var/www/site")
        self.assertEqual(ctx['working_dir'], "/var/www/site")

    def test_working_dir_relative_resolved(self):
        comp = _service_component(working_dir="server")
        ctx = self.orch._service_context(comp, "/var/www/site")
        self.assertEqual(ctx['working_dir'], "/var/www/site/server")


class TestServiceContext(unittest.TestCase):
    def setUp(self):
        self.orch = DeploymentOrchestrator(base_dir="/var/www")

    def test_resolves_binary_working_dir_env_file(self):
        comp = _service_component(
            working_dir="{{release_dir}}/server",
            env_file="{{base_dir}}/{{name}}/.env",
        )
        ctx = self.orch._service_context(comp, "/var/www/shop")
        self.assertEqual(ctx['release_dir'], "/var/www/shop")
        self.assertEqual(ctx['base_dir'], "/var/www")
        self.assertEqual(ctx['port'], "8090")
        self.assertEqual(ctx['binary'], "/var/www/shop/server/app")
        self.assertEqual(ctx['working_dir'], "/var/www/shop/server")
        self.assertEqual(ctx['env_file'], "/var/www/api/.env")
        self.assertEqual(ctx['service_name'], "app-shop-api")
        # Dedicated user and managed dirs.
        self.assertEqual(ctx['web_user'], "app-shop-api")
        self.assertEqual(ctx['web_group'], "app-shop-api")
        self.assertEqual(ctx['shared_dir'], "/var/www/.basaltwater_shared/shop/api")
        self.assertEqual(ctx['data_dir'], "/var/www/.basaltwater_shared/shop/api/data")

    def test_shared_dir_can_be_referenced(self):
        comp = _service_component(env_file="{{shared_dir}}/.env")
        ctx = self.orch._service_context(comp, "/var/www/shop")
        self.assertEqual(ctx['env_file'], "/var/www/.basaltwater_shared/shop/api/.env")

    def test_deploy_domain_available_to_service_templates(self):
        comp = _service_component(domain="{{domain}}", env_file="{{base_dir}}/{{domain}}/.env")
        ctx = self.orch._service_context(comp, "/var/www/shop", "deployed.example")
        self.assertEqual(ctx['domain'], "deployed.example")
        self.assertEqual(ctx['env_file'], "/var/www/deployed.example/.env")

    def test_exec_component_context_has_no_binary(self):
        # With exec (and no binary field), {{binary}} is intentionally absent;
        # referencing it would fail fast at render time.
        comp = _service_component(binary=None, exec="/srv/app --port {{port}}")
        ctx = self.orch._service_context(comp, "/var/www/shop")
        self.assertNotIn('binary', ctx)
        self.assertEqual(ctx['port'], "8090")

    def test_auto_port_assignment_is_persisted(self):
        component = _service_component(port="auto")
        manifest = Manifest(version=1, components=[component])
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            with patch.object(orchestrator, '_get_used_ports', return_value=set()), \
                 patch.object(orchestrator, '_find_free_port', return_value=8123) as find_port:
                resolved = orchestrator._resolve_manifest_ports(manifest, dest_path)
                orchestrator._save_manifest_ports(dest_path, resolved)
                repeated = orchestrator._resolve_manifest_ports(manifest, dest_path)

        self.assertEqual(resolved.components[0].port, 8123)
        self.assertEqual(repeated.components[0].port, 8123)
        find_port.assert_called_once()

    def test_duplicate_fixed_ports_are_rejected(self):
        manifest = Manifest(version=1, components=[
            _service_component(name="one", port=8123),
            _service_component(name="two", port=8123),
        ])
        with patch.object(self.orch, '_get_used_ports', return_value=set()):
            with self.assertRaisesRegex(RuntimeError, "already assigned"):
                self.orch._resolve_manifest_ports(manifest, "/var/www/shop")

    def test_health_check_uses_wall_clock_deadline(self):
        component = _service_component(health="/health")
        with patch(
            "lib.deployment.open_loopback",
            side_effect=urllib.error.URLError("not ready"),
        ) as urlopen, patch(
            "time.monotonic",
            side_effect=[100.0, 100.0, 130.0],
        ), patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "within 30 seconds"):
                self.orch._poll_health(component, timeout=30.0)

        urlopen.assert_called_once_with(
            "http://127.0.0.1:8090/health",
            timeout=3.0,
        )
        sleep.assert_not_called()

    @patch('lib.deployment.run')
    def test_sqlite_backup_uses_online_backup_and_retention(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        component = _service_component(
            sqlite_backup="{{data_dir}}/app.sqlite3",
            backup_retention=2,
        )
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            data_dir = orchestrator._component_data_dir(dest_path, component)
            os.makedirs(data_dir)
            database_path = os.path.join(data_dir, "app.sqlite3")
            database = sqlite3.connect(database_path)
            database.execute("CREATE TABLE values_table (value INTEGER)")
            database.execute("INSERT INTO values_table VALUES (7)")
            database.commit()
            database.close()

            orchestrator._backup_component_sqlite(component, dest_path, None)

            backup_dir = os.path.join(orchestrator._component_shared_dir(dest_path, component), "backups")
            backups = os.listdir(backup_dir)
            self.assertEqual(len(backups), 1)
            restored = sqlite3.connect(os.path.join(backup_dir, backups[0]))
            self.assertEqual(restored.execute("SELECT value FROM values_table").fetchone(), (7,))
            restored.close()

    @patch('lib.deployment.run')
    def test_sqlite_backup_refuses_preexisting_temporary_path(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        component = _service_component(sqlite_backup="{{data_dir}}/app.sqlite3")
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            data_dir = orchestrator._component_data_dir(dest_path, component)
            backup_dir = os.path.join(
                orchestrator._component_shared_dir(dest_path, component), "backups"
            )
            os.makedirs(data_dir)
            os.makedirs(backup_dir)
            database = sqlite3.connect(os.path.join(data_dir, "app.sqlite3"))
            database.execute("CREATE TABLE values_table (value INTEGER)")
            database.close()
            temporary_path = os.path.join(
                backup_dir, "api_20260102_030405_123456.sqlite3.tmp"
            )
            with open(temporary_path, "w", encoding="utf-8") as handle:
                handle.write("untrusted")

            fixed_datetime = MagicMock()
            fixed_datetime.now.return_value.strftime.return_value = (
                "20260102_030405_123456"
            )
            with patch('lib.deployment.datetime', fixed_datetime):
                with self.assertRaises(FileExistsError):
                    orchestrator._backup_component_sqlite(component, dest_path, None)

            with open(temporary_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "untrusted")

    @patch('lib.deployment.run')
    def test_sqlite_backup_rejects_symlink_outside_managed_state(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        component = _service_component(sqlite_backup="{{data_dir}}/app.sqlite3")
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            data_dir = orchestrator._component_data_dir(dest_path, component)
            os.makedirs(data_dir)
            external_database = os.path.join(base_dir, "privileged.sqlite3")
            database = sqlite3.connect(external_database)
            database.execute("CREATE TABLE secret (value TEXT)")
            database.close()
            os.symlink(external_database, os.path.join(data_dir, "app.sqlite3"))

            with self.assertRaisesRegex(RuntimeError, "managed data directory"):
                orchestrator._backup_component_sqlite(component, dest_path, None)

    @patch('lib.deployment.run')
    def test_sqlite_backup_rejects_symlinked_backup_directory(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        component = _service_component(sqlite_backup="{{data_dir}}/app.sqlite3")
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            data_dir = orchestrator._component_data_dir(dest_path, component)
            os.makedirs(data_dir)
            database = sqlite3.connect(os.path.join(data_dir, "app.sqlite3"))
            database.execute("CREATE TABLE values_table (value INTEGER)")
            database.close()
            external_dir = os.path.join(base_dir, "external-backups")
            os.makedirs(external_dir)
            os.symlink(
                external_dir,
                os.path.join(orchestrator._component_shared_dir(dest_path, component), "backups"),
            )

            with self.assertRaisesRegex(RuntimeError, "symlinked backup directory"):
                orchestrator._backup_component_sqlite(component, dest_path, None)

    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.run')
    def test_service_install_rejects_symlinked_data_directory(
        self, mock_run, mock_service
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        component = _service_component(binary=None, exec="/bin/true")
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            dest_path = os.path.join(base_dir, "shop")
            shared_dir = orchestrator._component_shared_dir(dest_path, component)
            external_dir = os.path.join(base_dir, "external-data")
            os.makedirs(shared_dir)
            os.makedirs(external_dir)
            os.symlink(external_dir, os.path.join(shared_dir, "data"))

            with self.assertRaisesRegex(RuntimeError, "symlinked data directory"):
                orchestrator._install_service_component(component, dest_path)

        mock_service.assert_not_called()

    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.run')
    def test_env_file_must_be_read_only_to_service_user(self, mock_run, mock_service):
        component = _service_component(
            binary=None,
            exec="/bin/true",
            env_file="/root/private.env",
        )

        def run_result(command: str, **_kwargs: object) -> MagicMock:
            return MagicMock(
                returncode=1 if " -- test -r " in command else 0,
                stdout="",
                stderr="",
            )

        mock_run.side_effect = run_result
        with tempfile.TemporaryDirectory() as base_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            with self.assertRaisesRegex(RuntimeError, "readable but not writable"):
                orchestrator._install_service_component(
                    component,
                    os.path.join(base_dir, "shop"),
                )

        mock_service.assert_not_called()


class TestNginxIntegration(unittest.TestCase):
    """The descriptors must drive correct per-domain nginx config."""

    def setUp(self):
        self.orch = DeploymentOrchestrator(base_dir="/var/www")

    def test_static_site_served_from_output(self):
        dep = self.orch._component_dep(_static_component(), "/var/www/site")
        cfg = generate_merged_nginx_config("example.com", [dep], enable_https_redirect=False)
        self.assertIn("/var/www/site/dist", cfg)
        self.assertIn("server_name example.com;", cfg)

    def test_service_reverse_proxied_to_loopback_port(self):
        dep = self.orch._component_dep(_service_component(), "/var/www/site")
        cfg = generate_merged_nginx_config("api.example.com", [dep], enable_https_redirect=False)
        self.assertIn("proxy_pass http://127.0.0.1:8090", cfg)
        self.assertIn("server_name api.example.com;", cfg)

    def test_service_subpath_preserves_request_uri(self):
        dep = self.orch._component_dep(
            _service_component(domain="example.com", path="/api"), "/var/www/site"
        )
        cfg = generate_merged_nginx_config("example.com", [dep], enable_https_redirect=False)
        self.assertIn("location /api/", cfg)
        self.assertIn("proxy_pass http://127.0.0.1:8090;", cfg)

    def test_duplicate_resolved_routes_are_rejected(self):
        manifest = Manifest(
            version=1,
            components=[
                _static_component(name="first", domain="{{domain}}"),
                _static_component(name="second", domain="example.com"),
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "both declare example.com/"):
            self.orch._validate_manifest_routes(manifest, "example.com")

    def test_internal_services_do_not_claim_nginx_routes(self):
        manifest = Manifest(
            version=1,
            components=[
                _service_component(name="worker", domain="example.com", reverse_proxy=False),
                _static_component(name="site", domain="example.com"),
            ],
        )

        self.orch._validate_manifest_routes(manifest, "example.com")


class TestManifestBuildToolchains(unittest.TestCase):
    def setUp(self):
        self.orch = DeploymentOrchestrator(base_dir="/var/www")

    @patch('lib.deployment.run')
    def test_build_loads_app_scoped_node_and_python_tools(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as build_home:
            nvm_dir = os.path.join(build_home, ".nvm")
            os.makedirs(nvm_dir)
            with open(os.path.join(nvm_dir, "nvm.sh"), "w", encoding="utf-8") as handle:
                handle.write("# nvm")

            component = _static_component(build="npm run build")
            with patch.object(self.orch, "_build_home", return_value=build_home):
                self.orch._run_component_build(component, source, "build-example")

        command = mock_run.call_args.args[0]
        self.assertIn(f"export NVM_DIR={nvm_dir}", command)
        self.assertIn(os.path.join(build_home, ".local", "bin"), command)
        self.assertIn("/usr/local/go/bin", command)

    @patch('lib.deployment.run')
    @patch('common.common_steps.install_or_update_uv')
    @patch('common.common_steps.install_node_for_user')
    def test_prepares_detected_toolchains_once(
        self, mock_install_node, mock_install_uv, mock_run
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as build_home:
            for filename in ("package.json", "pyproject.toml", ".nvmrc"):
                with open(os.path.join(source, filename), "w", encoding="utf-8") as handle:
                    handle.write("{}" if filename != ".nvmrc" else "22\n")

            def install_node(_user: str, home: str) -> None:
                os.makedirs(os.path.join(home, ".nvm"), exist_ok=True)
                with open(os.path.join(home, ".nvm", "nvm.sh"), "w", encoding="utf-8"):
                    pass

            def install_uv(home: str, username: str) -> bool:
                uv_dir = os.path.join(home, ".local", "bin")
                os.makedirs(uv_dir, exist_ok=True)
                with open(os.path.join(uv_dir, "uv"), "w", encoding="utf-8"):
                    pass
                return True

            mock_install_node.side_effect = install_node
            mock_install_uv.side_effect = install_uv
            with patch.object(self.orch, "_build_home", return_value=build_home):
                self.orch._prepare_build_toolchain(source, "build-example")
                self.orch._prepare_build_toolchain(source, "build-example")

        mock_install_node.assert_called_once_with("build-example", build_home)
        mock_install_uv.assert_called_once_with(build_home, username="build-example")
        self.assertEqual(
            sum("nvm install" in call.args[0] for call in mock_run.call_args_list),
            2,
        )


class TestDeployManifest(unittest.TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="manifest_base_")
        self.source = tempfile.mkdtemp(prefix="manifest_src_")
        self.orch = DeploymentOrchestrator(base_dir=self.base_dir)

        # A repo with a static site and a service, plus the artifacts a build
        # would have produced (so the binary-exists check passes).
        manifest = {
            "version": 1,
            "components": [
                {"name": "site", "type": "static", "domain": "example.com",
                 "build": "npm run build", "output": "dist"},
                {"name": "api", "type": "service", "domain": "api.example.com",
                 "build": "server/build.sh", "binary": "server/app",
                 "env_file": "/opt/app/.env",
                 "runtime_env": {"APP_DATA": "{{data_dir}}/app.sqlite3"},
                 "port": 8090, "health": "/health"},
            ],
        }
        with open(os.path.join(self.source, "basaltwater.json"), "w") as f:
            json.dump(manifest, f)
        os.makedirs(os.path.join(self.source, "dist"))
        with open(os.path.join(self.source, "dist", "index.html"), "w") as f:
            f.write("<html></html>")
        os.makedirs(os.path.join(self.source, "server"))
        with open(os.path.join(self.source, "server", "app"), "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(self.source, "server", "app"), 0o755)
        with open(os.path.join(self.source, "basaltwater.json")) as f:
            self.manifest: Manifest = parse_manifest(json.load(f))

    def tearDown(self):
        for path in (self.base_dir, self.source):
            if os.path.exists(path):
                shutil.rmtree(path)

    def _operation_marker_path(self) -> str:
        return os.path.join(
            self.base_dir,
            ".basaltwater_shared",
            "example_com",
            "manifest-operation.json",
        )

    def test_service_unit_snapshot_failure_aborts_closed(self):
        with patch("lib.deployment.os.path.isdir", return_value=True), patch(
            "lib.deployment.os.listdir",
            return_value=["app-example_com-api.service"],
        ), patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RuntimeError, "Could not snapshot managed"):
                self.orch._app_unit_snapshots(
                    os.path.join(self.base_dir, "example_com")
                )

    @patch('lib.deployment.run')
    def test_stop_requires_unit_to_be_inactive(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with self.assertRaisesRegex(RuntimeError, "remained active"):
            self.orch._stop_app_unit("app-example_com-api")

    @patch('lib.deployment.run')
    def test_restart_requires_unit_to_be_active(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=3, stdout="", stderr=""),
        ]

        with self.assertRaisesRegex(RuntimeError, "not active after restart"):
            self.orch._restart_app_units(["app-example_com-api"])

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_deploys_all_components(self, mock_run, mock_meta, mock_service, mock_health):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        deps = self.orch.deploy_manifest(
            manifest=self.manifest,
            source_path=self.source,
            domain="example.com",
            path="/",
            git_url="https://git.example.com/shop.git",
            commit_hash="abc123",
            keep_source=True,
        )

        dest = os.path.join(self.base_dir, "example_com")

        # One descriptor per component, with the right shapes.
        self.assertEqual(len(deps), 2)
        by_domain = {d['domain']: d for d in deps}
        self.assertEqual(by_domain['example.com']['serve_path'], os.path.join(dest, "dist"))
        self.assertFalse(by_domain['example.com']['needs_proxy'])
        self.assertEqual(by_domain['api.example.com']['backend_port'], 8090)
        self.assertTrue(by_domain['api.example.com']['needs_proxy'])

        # Service was installed under its app-scoped name, as its dedicated user,
        # with the resolved binary path and env_file.
        mock_service.assert_called_once()
        args, kwargs = mock_service.call_args
        self.assertEqual(args[0], "app-example_com-api")           # service name
        self.assertEqual(args[1], os.path.join(dest, "server", "app"))  # exec_start
        self.assertEqual(args[3], "app-example_com-api")           # web_user (dedicated)
        self.assertEqual(args[4], "app-example_com-api")           # web_group
        self.assertEqual(kwargs['env_file'], "/opt/app/.env")
        self.assertEqual(
            kwargs['writable_paths'],
            [os.path.join(self.base_dir, ".basaltwater_shared", "example_com", "api", "data")],
        )
        self.assertEqual(
            kwargs['runtime_env'],
            {"APP_DATA": os.path.join(self.base_dir, ".basaltwater_shared", "example_com", "api", "data", "app.sqlite3")},
        )
        mock_health.assert_called_once()

        # The managed, service-owned data dir was created outside the release.
        data_dir = os.path.join(self.base_dir, ".basaltwater_shared", "example_com", "api", "data")
        self.assertTrue(os.path.isdir(data_dir))
        ownership_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(any("chown root:root" in command for command in ownership_commands))
        self.assertFalse(any("chown -R" in command and ".basaltwater_shared" in command
                             for command in ownership_commands))
        # A dedicated service user was ensured (id lookup attempted).
        self.assertTrue(any("id app-example_com-api" in c.args[0] for c in mock_run.call_args_list))

        # Both build commands ran at the release root.
        build_cmds = [c.args[0] for c in mock_run.call_args_list if "&&" in c.args[0]]
        self.assertTrue(any("npm run build" in c for c in build_cmds))
        self.assertTrue(any("server/build.sh" in c for c in build_cmds))

        # Metadata persisted, and the deployed tree is in place.
        mock_meta.assert_called_once()
        self.assertTrue(os.path.exists(os.path.join(dest, "basaltwater.json")))
        self.assertFalse(os.path.exists(self._operation_marker_path()))

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_stops_old_service_before_sqlite_backup(
        self, mock_run, _mock_meta, _mock_service, _mock_health
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        events = []

        def stop_unit(_unit_name):
            events.append("stop")
            return True

        def backup_database(_component, _dest_path, _domain):
            events.append("backup")

        with patch.object(
            self.orch,
            '_app_unit_snapshots',
            return_value={'app-example_com-api': 'old unit'},
        ), patch.object(
            self.orch,
            '_stop_app_unit',
            side_effect=stop_unit,
        ), patch.object(
            self.orch,
            '_backup_component_sqlite',
            side_effect=backup_database,
        ):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertEqual(events, ["stop", "backup"])

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_build_failure_aborts(self, mock_run, mock_meta, mock_service, mock_health):
        # Fail only the build command; systemctl/chown calls succeed.
        def run_side_effect(cmd, *a, **k):
            if "npm run build" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="boom")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = run_side_effect

        with self.assertRaises(RuntimeError) as ctx:
            self.orch.deploy_manifest(
                manifest=self.manifest, source_path=self.source,
                domain="example.com", path="/",
                git_url="https://git.example.com/shop.git", commit_hash="abc123",
                keep_source=True,
            )
        self.assertIn("build failed", str(ctx.exception))
        self.assertFalse(os.path.exists(self._operation_marker_path()))

    @patch.object(
        DeploymentOrchestrator,
        '_run_component_build',
        side_effect=KeyboardInterrupt,
    )
    @patch('lib.deployment.run')
    def test_interruption_leaves_marker_for_next_invocation(
        self, mock_run, _mock_build
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with self.assertRaises(KeyboardInterrupt):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        record = OperationStateStore(self._operation_marker_path()).load()
        self.assertIsNotNone(record)
        self.assertEqual(record.phase, "building")
        self.assertIn("staging_path", record.context)

        with self.assertRaisesRegex(OperationStateError, "Unfinished manifest_deploy"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_build_failure_preserves_active_release(self, mock_run, mock_meta, mock_service, mock_health):
        active = os.path.join(self.base_dir, "example_com")
        os.makedirs(active)
        marker = os.path.join(active, "active-release.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("still serving")

        def run_side_effect(cmd, *a, **k):
            if "npm run build" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="broken build")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        with self.assertRaises(RuntimeError):
            self.orch.deploy_manifest(
                manifest=self.manifest, source_path=self.source,
                domain="example.com", path="/",
                git_url="https://git.example.com/shop.git", commit_hash="abc123",
                keep_source=True,
            )

        self.assertTrue(os.path.exists(marker))
        self.assertEqual(
            [name for name in os.listdir(self.base_dir) if name.startswith(".example_com.build-")],
            [],
        )

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_missing_binary_aborts(self, mock_run, mock_meta, mock_service, mock_health):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        os.remove(os.path.join(self.source, "server", "app"))

        with self.assertRaises(RuntimeError) as ctx:
            self.orch.deploy_manifest(
                manifest=self.manifest, source_path=self.source,
                domain="example.com", path="/",
                git_url="https://git.example.com/shop.git", commit_hash="abc123",
                keep_source=True,
            )
        self.assertIn("binary not found", str(ctx.exception))
        self.assertFalse(
            any("systemctl stop" in call.args[0] for call in mock_run.call_args_list)
        )

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_missing_static_output_aborts_before_service_stop(
        self, mock_run, _mock_meta, _mock_service, _mock_health
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        shutil.rmtree(os.path.join(self.source, "dist"))

        with self.assertRaisesRegex(RuntimeError, "static output not found"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertFalse(
            any("systemctl stop" in call.args[0] for call in mock_run.call_args_list)
        )

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_non_executable_binary_aborts_before_service_stop(
        self, mock_run, _mock_meta, _mock_service, _mock_health
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        os.chmod(os.path.join(self.source, "server", "app"), 0o644)

        with self.assertRaisesRegex(RuntimeError, "binary is not executable"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertFalse(
            any("systemctl stop" in call.args[0] for call in mock_run.call_args_list)
        )

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service', side_effect=RuntimeError("service failed"))
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_service_failure_restores_previous_release(
        self, mock_run, mock_meta, _mock_service, _mock_health
    ):
        active_checks = 0

        def run_side_effect(command, *args, **kwargs):
            nonlocal active_checks
            del args, kwargs
            if "is-active --quiet" in command:
                active_checks += 1
                return MagicMock(
                    returncode=0 if active_checks == 1 else 3,
                    stdout="",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect
        active = os.path.join(self.base_dir, "example_com")
        os.makedirs(active)
        marker = os.path.join(active, "previous.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("previous")

        with self.assertRaisesRegex(RuntimeError, "service failed"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertTrue(os.path.exists(marker))
        mock_meta.assert_not_called()
        self.assertFalse(os.path.exists(self._operation_marker_path()))

    @patch.object(
        DeploymentOrchestrator,
        '_app_unit_snapshots',
        return_value={'app-example_com-api': 'old unit'},
    )
    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_stop_failure_aborts_before_release_activation(
        self,
        mock_run,
        mock_meta,
        mock_service,
        _mock_health,
        _mock_snapshots,
    ):
        active = os.path.join(self.base_dir, "example_com")
        os.makedirs(active)
        marker = os.path.join(active, "previous.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("previous")

        def run_side_effect(command, *args, **kwargs):
            del args, kwargs
            return MagicMock(
                returncode=1 if "systemctl stop" in command else 0,
                stdout="",
                stderr="permission denied" if "systemctl stop" in command else "",
            )

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "Could not stop"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertTrue(os.path.exists(marker))
        mock_service.assert_not_called()
        mock_meta.assert_not_called()

    @patch.object(
        DeploymentOrchestrator,
        '_poll_health',
    )
    @patch('lib.deployment.create_managed_service', side_effect=RuntimeError("service failed"))
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_rollback_stop_failure_is_reported_after_release_restore(
        self, mock_run, mock_meta, _mock_service, _mock_health
    ):
        active = os.path.join(self.base_dir, "example_com")
        os.makedirs(active)
        marker = os.path.join(active, "previous.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("previous")

        def run_side_effect(command, *args, **kwargs):
            del args, kwargs
            return MagicMock(
                returncode=1 if "systemctl stop" in command else 0,
                stdout="",
                stderr="permission denied" if "systemctl stop" in command else "",
            )

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "service recovery was incomplete"):
            self.orch.deploy_manifest(
                manifest=self.manifest,
                source_path=self.source,
                domain="example.com",
                path="/",
                git_url="https://git.example.com/shop.git",
                commit_hash="abc123",
                keep_source=True,
            )

        self.assertTrue(os.path.exists(marker))
        mock_meta.assert_not_called()
        record = OperationStateStore(self._operation_marker_path()).load()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "recovery_required")
        self.assertTrue(record.context["errors"])

    @patch.object(DeploymentOrchestrator, '_poll_health')
    @patch('lib.deployment.create_managed_service')
    @patch('lib.deployment.save_deployment_metadata')
    @patch('lib.deployment.run')
    def test_reverse_proxy_false_omits_nginx_descriptor(
        self, mock_run, _mock_meta, _mock_service, _mock_health
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.manifest.components[1].reverse_proxy = False

        deps = self.orch.deploy_manifest(
            manifest=self.manifest,
            source_path=self.source,
            domain="example.com",
            path="/",
            git_url="https://git.example.com/shop.git",
            commit_hash="abc123",
            keep_source=True,
        )

        self.assertEqual([dep["domain"] for dep in deps], ["example.com"])


if __name__ == "__main__":
    unittest.main()
