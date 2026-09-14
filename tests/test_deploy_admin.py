"""Security tests for privileged remote deployment operations."""

from __future__ import annotations

import os
import json
import stat
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import web.service_tools.deploy_admin as deploy_admin
from lib.remote_deploy import _validate_config_name, _validate_deploy_path
from web.service_tools.deploy_admin import (
    validate_config_name,
    validate_operation_id,
    validate_service_name,
)

SITE_REQUEST = json.dumps({
    'domain': 'example', 'path': '/', 'serve_path': '/var/www/example', 'project_type': 'static',
}).encode()
OLD_CONFIG = (deploy_admin.GENERATED_CONFIG_MARKER + '\nold config\n').encode()
OPERATION_ID = "0123456789abcdef0123456789abcdef"


class TestDeployAdminValidation(unittest.TestCase):
    def test_nginx_config_name_rejects_path_traversal(self):
        self.assertEqual(validate_config_name("example_com"), "example_com")
        self.assertEqual(_validate_config_name("example.com"), "example_com")
        for invalid in ("../example", "example/name", "example name", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_config_name(invalid)

    def test_service_name_is_limited_to_generated_app_units(self):
        self.assertEqual(validate_service_name("node-api.service"), "node-api.service")
        self.assertEqual(validate_service_name("node-shop"), "node-shop.service")
        for invalid in (
            "nginx.service",
            "rails-../../ssh.service",
            "rails-shop.service",
            "rails-shop.service.service",
            "node-.service",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_service_name(invalid)

    def test_operation_id_requires_a_full_lowercase_hex_token(self):
        self.assertEqual(validate_operation_id(OPERATION_ID), OPERATION_ID)
        for invalid in ("", "abc", "A" * 32, "g" * 32, "0" * 33):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_operation_id(invalid)

    def test_deployment_removal_cannot_escape_configured_root(self):
        self.assertEqual(_validate_deploy_path("/var/www/shop", "/var/www"), "/var/www/shop")
        for invalid in ("/var/www", "/var/www/../../etc", "/etc/passwd", "relative/path"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _validate_deploy_path(invalid, "/var/www")


class TestDeployAdminFileOperations(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = patch.object(deploy_admin, '_allowed_site_roots', return_value=['/var/www'])
        self.policy.start()
        self.addCleanup(self.policy.stop)
        renderer = patch.object(deploy_admin, 'generate_merged_nginx_config', return_value='server {}\n')
        self.render = renderer.start()
        self.addCleanup(renderer.stop)

    def _paths(self, root: str) -> tuple[str, str, str]:
        available = os.path.join(root, "available")
        enabled = os.path.join(root, "enabled")
        staged_prefix = os.path.join(root, "staged-")
        os.makedirs(available)
        os.makedirs(enabled)
        return available, enabled, staged_prefix

    def test_nginx_mutation_lock_uses_a_private_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            lock_path = os.path.join(root, "locks", "nginx.lock")
            with patch.object(deploy_admin, "NGINX_LOCK_PATH", lock_path):
                with deploy_admin._nginx_mutation_lock():
                    lock_info = os.stat(lock_path)
                    directory_info = os.stat(os.path.dirname(lock_path))

            self.assertTrue(stat.S_ISREG(lock_info.st_mode))
            self.assertEqual(stat.S_IMODE(lock_info.st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory_info.st_mode), 0o700)

    def test_install_writes_config_enables_site_and_removes_stage(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, staged_prefix = self._paths(root)
            staged_path = f"{staged_prefix}example-{OPERATION_ID}.json"
            with open(staged_path, "wb") as staged:
                staged.write(SITE_REQUEST)

            with patch.object(deploy_admin, "NGINX_AVAILABLE_DIR", available), patch.object(deploy_admin, "NGINX_ENABLED_DIR", enabled), patch.object(deploy_admin, "STAGED_CONFIG_PREFIX", staged_prefix), patch.object(deploy_admin, "NGINX_BINARY", "/mock/nginx"), patch.object(deploy_admin, "_run_checked") as run_checked:
                deploy_admin.install_nginx_config("example", OPERATION_ID)

            installed = os.path.join(available, "example")
            link = os.path.join(enabled, "example")
            with open(installed, "rb") as config:
                self.assertEqual(config.read(), b"server {}\n")
            self.assertEqual(os.readlink(link), installed)
            self.assertFalse(os.path.exists(staged_path))
            run_checked.assert_called_once_with(["/mock/nginx", "-t"])
            self.assertTrue(self.render.call_args.kwargs['disable_symlinks'])

    def test_install_rolls_back_previous_site_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, staged_prefix = self._paths(root)
            installed = os.path.join(available, "example")
            link = os.path.join(enabled, "example")
            with open(installed, "wb") as config:
                config.write(OLD_CONFIG)
            os.chmod(installed, 0o640)
            os.symlink(installed, link)
            staged_path = f"{staged_prefix}example-{OPERATION_ID}.json"
            with open(staged_path, "wb") as staged:
                staged.write(SITE_REQUEST)

            with patch.object(deploy_admin, "NGINX_AVAILABLE_DIR", available), patch.object(deploy_admin, "NGINX_ENABLED_DIR", enabled), patch.object(deploy_admin, "STAGED_CONFIG_PREFIX", staged_prefix), patch.object(deploy_admin, "_run_checked", side_effect=RuntimeError("invalid nginx")):
                with self.assertRaisesRegex(RuntimeError, "invalid nginx"):
                    deploy_admin.install_nginx_config("example", OPERATION_ID)

            with open(installed, "rb") as config:
                self.assertEqual(config.read(), OLD_CONFIG)
            self.assertEqual(os.stat(installed).st_mode & 0o777, 0o640)
            self.assertEqual(os.readlink(link), installed)
            self.assertFalse(os.path.exists(staged_path))

    def test_install_rejects_stage_owned_by_another_user(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, staged_prefix = self._paths(root)
            staged_path = f"{staged_prefix}example-{OPERATION_ID}.json"
            with open(staged_path, "wb") as staged:
                staged.write(SITE_REQUEST)

            with patch.object(deploy_admin, "NGINX_AVAILABLE_DIR", available), patch.object(deploy_admin, "NGINX_ENABLED_DIR", enabled), patch.object(deploy_admin, "STAGED_CONFIG_PREFIX", staged_prefix), patch.dict(deploy_admin.os.environ, {"SUDO_UID": str(os.getuid() + 1)}):
                with self.assertRaisesRegex(ValueError, "owned by the invoking user"):
                    deploy_admin.install_nginx_config("example", OPERATION_ID)

            self.assertTrue(os.path.exists(staged_path))

    def test_remove_deletes_site_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, staged_prefix = self._paths(root)
            installed = os.path.join(available, "example")
            link = os.path.join(enabled, "example")
            with open(installed, "wb") as config:
                config.write(OLD_CONFIG)
            os.symlink(installed, link)

            with patch.object(deploy_admin, "NGINX_AVAILABLE_DIR", available), patch.object(deploy_admin, "NGINX_ENABLED_DIR", enabled), patch.object(deploy_admin, "NGINX_BINARY", "/mock/nginx"), patch.object(deploy_admin, "_run_checked") as run_checked:
                deploy_admin.remove_nginx_config("example")

            self.assertFalse(os.path.lexists(installed))
            self.assertFalse(os.path.lexists(link))
            run_checked.assert_called_once_with(["/mock/nginx", "-t"])

    def test_remove_restores_site_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, _ = self._paths(root)
            installed = os.path.join(available, "example")
            link = os.path.join(enabled, "example")
            with open(installed, "wb") as config:
                config.write(OLD_CONFIG)
            os.symlink(installed, link)

            with patch.object(deploy_admin, "NGINX_AVAILABLE_DIR", available), patch.object(deploy_admin, "NGINX_ENABLED_DIR", enabled), patch.object(deploy_admin, "_run_checked", side_effect=RuntimeError("invalid nginx")):
                with self.assertRaisesRegex(RuntimeError, "invalid nginx"):
                    deploy_admin.remove_nginx_config("example")

            with open(installed, "rb") as config:
                self.assertEqual(config.read(), OLD_CONFIG)
            self.assertEqual(os.readlink(link), installed)

    def test_install_and_remove_preserve_unmanaged_sites(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, staged_prefix = self._paths(root)
            installed = os.path.join(available, 'example')
            link = os.path.join(enabled, 'example')
            with open(installed, 'wb') as config:
                config.write(b'# Administrator-owned site\nserver {}\n')
            os.symlink(installed, link)
            with open(f'{staged_prefix}example-{OPERATION_ID}.json', 'wb') as staged:
                staged.write(SITE_REQUEST)
            with patch.object(deploy_admin, 'NGINX_AVAILABLE_DIR', available), patch.object(deploy_admin, 'NGINX_ENABLED_DIR', enabled), patch.object(deploy_admin, 'STAGED_CONFIG_PREFIX', staged_prefix), patch.object(deploy_admin, '_run_checked') as run:
                with self.assertRaisesRegex(ValueError, 'unmanaged'):
                    deploy_admin.install_nginx_config('example', OPERATION_ID)
                with self.assertRaisesRegex(ValueError, 'unmanaged'):
                    deploy_admin.remove_nginx_config('example')
                run.assert_not_called()
            with open(installed, 'rb') as config:
                self.assertEqual(config.read(), b'# Administrator-owned site\nserver {}\n')
            self.assertEqual(os.readlink(link), installed)

    def test_remove_preserves_link_to_another_site(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            available, enabled, _ = self._paths(root)
            installed = os.path.join(available, 'example')
            link = os.path.join(enabled, 'example')
            with open(installed, 'wb') as config:
                config.write(OLD_CONFIG)
            os.symlink('/unrelated/site', link)
            with patch.object(deploy_admin, 'NGINX_AVAILABLE_DIR', available), patch.object(deploy_admin, 'NGINX_ENABLED_DIR', enabled), patch.object(deploy_admin, '_run_checked') as run:
                with self.assertRaisesRegex(ValueError, 'does not reference'):
                    deploy_admin.remove_nginx_config('example')
                run.assert_not_called()
            self.assertEqual(os.readlink(link), '/unrelated/site')

    def test_regular_file_reader_rejects_directories_and_limits_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = os.path.join(directory, "config")
            with open(file_path, "wb") as config:
                config.write(b"abc")
            content, mode, owner = deploy_admin._read_regular_file(file_path, 3)
            self.assertEqual(content, b"abc")
            self.assertEqual(mode, stat.S_IMODE(os.stat(file_path).st_mode))
            self.assertEqual(owner, os.stat(file_path).st_uid)
            with self.assertRaisesRegex(ValueError, "exceeds"):
                deploy_admin._read_regular_file(file_path, 2)
            with self.assertRaisesRegex(ValueError, "regular file"):
                deploy_admin._read_regular_file(directory, 10)

    def test_regular_file_reader_rejects_fifo_without_blocking_open(self) -> None:
        with patch.object(deploy_admin.os, 'open', return_value=42) as open_fd, patch.object(deploy_admin.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO)), patch.object(deploy_admin.os, 'close') as close_fd:
            with self.assertRaisesRegex(ValueError, 'regular file'):
                deploy_admin._read_regular_file('/mock/request.json', 1024)
            self.assertTrue(open_fd.call_args.args[1] & os.O_NONBLOCK)
            close_fd.assert_called_once_with(42)


class TestDeployAdminCommands(unittest.TestCase):
    def test_reload_and_restart_build_privileged_commands(self) -> None:
        with patch.object(deploy_admin, "_run_checked") as run_checked:
            deploy_admin.reload_nginx()
            deploy_admin.restart_service("node-api")

        self.assertEqual(run_checked.call_args_list[0].args, ([deploy_admin.NGINX_BINARY, "-t"],))
        self.assertEqual(run_checked.call_args_list[1].args, ([deploy_admin.SYSTEMCTL_BINARY, "reload", "nginx.service"],))
        self.assertEqual(run_checked.call_args_list[2].args, ([deploy_admin.SYSTEMCTL_BINARY, "restart", "node-api.service"],))

    def test_main_requires_root_before_parsing_operation(self) -> None:
        with patch.object(deploy_admin.os, "geteuid", return_value=1000):
            self.assertEqual(deploy_admin.main(["reload-nginx"]), 1)

        with patch.object(deploy_admin.os, "geteuid", return_value=0), patch.object(
            deploy_admin, "_nginx_mutation_lock"
        ) as mutation_lock, patch.object(
            deploy_admin, "reload_nginx"
        ) as reload_nginx:
            self.assertEqual(deploy_admin.main(["reload-nginx"]), 0)
        mutation_lock.assert_called_once_with()
        reload_nginx.assert_called_once_with()

    def test_main_passes_operation_id_to_locked_install(self) -> None:
        with patch.object(deploy_admin.os, "geteuid", return_value=0), patch.object(
            deploy_admin, "_nginx_mutation_lock"
        ) as mutation_lock, patch.object(
            deploy_admin, "install_nginx_config"
        ) as install:
            self.assertEqual(
                deploy_admin.main(["install-site", "example", OPERATION_ID]),
                0,
            )

        mutation_lock.assert_called_once_with()
        install.assert_called_once_with("example", OPERATION_ID)


if __name__ == "__main__":
    unittest.main()
