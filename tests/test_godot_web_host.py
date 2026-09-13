"""Tests for the managed Godot HTTPS publishing origin."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from common import godot_web_steps
from common.agent_steps import BASE_AGENT_SKILL_NAMES
from common.service_tools import godot_web_publish


class TestGodotWebHost(unittest.TestCase):
    def test_nginx_host_supplies_secure_context_and_isolation_headers(self) -> None:
        content = godot_web_steps.render_nginx_config(
            "/certs/server.crt",
            "/certs/server.key",
        )

        self.assertIn("listen 8443 ssl", content)
        self.assertIn("Cross-Origin-Opener-Policy \"same-origin\"", content)
        self.assertIn("Cross-Origin-Embedder-Policy \"require-corp\"", content)
        self.assertIn("location /sites/", content)
        self.assertIn("application/x-x509-ca-cert", content)
        self.assertIn("autoindex on", content)
        self.assertIn("gzip_static on", content)
        self.assertIn("application/wasm", content)

    def test_certificate_identities_include_remote_and_loopback_access(self) -> None:
        self.assertEqual(
            godot_web_steps.identities_for_config("Games.Example", "godot-vm"),
            ["games.example", "godot-vm", "localhost", "127.0.0.1", "::1"],
        )

    def test_discovers_active_addresses_before_local_hostnames(self) -> None:
        with (
            patch.object(
                godot_web_steps,
                "run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="192.0.2.10 fd00::10\n",
                ),
            ),
            patch.object(godot_web_steps.socket, "getfqdn", return_value="godot-vm"),
            patch.object(godot_web_steps.socket, "gethostname", return_value="godot-vm"),
        ):
            identities = godot_web_steps.discover_local_web_identities()

        self.assertEqual(
            identities,
            [
                "192.0.2.10",
                "fd00::10",
                "godot-vm",
                "localhost",
                "127.0.0.1",
                "::1",
            ],
        )

    def test_invalid_certificate_identity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid internal HTTPS identity"):
            godot_web_steps.validate_web_identities(["bad host name"])

    def test_configure_host_creates_user_publish_root_and_landing_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            web_root = os.path.join(temporary_dir, "web")
            games_root = os.path.join(web_root, "games")
            sites_root = os.path.join(web_root, "sites")
            url_file = os.path.join(temporary_dir, "config", "base-url")
            ca_cert = os.path.join(temporary_dir, "ca.crt")
            ca_download = os.path.join(web_root, "infra-tools-ca.crt")
            with open(ca_cert, "w", encoding="utf-8") as cert_file:
                cert_file.write("test CA\n")

            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with (
                patch.object(godot_web_steps, "GODOT_WEB_ROOT", web_root),
                patch.object(godot_web_steps, "GODOT_WEB_GAMES_ROOT", games_root),
                patch.object(godot_web_steps, "INTERNAL_WEB_SITES_ROOT", sites_root),
                patch.object(godot_web_steps, "GODOT_WEB_URL_FILE", url_file),
                patch.object(godot_web_steps, "GODOT_WEB_CA_CERT", ca_cert),
                patch.object(
                    godot_web_steps,
                    "GODOT_WEB_CA_DOWNLOAD",
                    ca_download,
                ),
                patch.object(godot_web_steps, "_ensure_nginx"),
                patch.object(
                    godot_web_steps,
                    "_certificate_for_identities",
                    return_value=("/cert", "/key", True, True),
                ) as certificate,
                patch.object(
                    godot_web_steps,
                    "discover_local_web_identities",
                    return_value=["198.51.100.20", "godot-vm"],
                ),
                patch.object(
                    godot_web_steps,
                    "_install_chromium_ca_trust",
                    return_value=True,
                ) as chromium_trust,
                patch.object(godot_web_steps, "_install_publisher_links", return_value=True),
                patch.object(godot_web_steps, "_configure_nginx_site", return_value=True) as nginx,
                patch.object(godot_web_steps, "_configure_web_policy", return_value=True),
                patch.object(godot_web_steps.pwd, "getpwnam", return_value=account),
            ):
                changed = godot_web_steps.configure_godot_web_host(
                    ["192.0.2.10", "localhost"],
                    ["agent"],
                )

            self.assertTrue(changed)
            self.assertTrue(os.path.isdir(os.path.join(games_root, "agent")))
            self.assertTrue(os.path.isdir(os.path.join(sites_root, "agent")))
            with open(os.path.join(web_root, "index.html"), encoding="utf-8") as page:
                content = page.read()
                self.assertIn("infra-web publish godot", content)
                self.assertIn("infra-web publish site", content)
                self.assertIn("agent sites", content)
                self.assertIn(">games</a>", content)
            with open(url_file, encoding="utf-8") as base_url:
                self.assertEqual(base_url.read(), "https://192.0.2.10:8443\n")
            certificate.assert_called_once_with(
                ["192.0.2.10", "localhost", "198.51.100.20", "godot-vm"]
            )
            chromium_trust.assert_called_once_with(["agent"])
            nginx.assert_called_once()

    def test_web_policy_exposes_the_user_readable_ca_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            policy_file = os.path.join(temporary_dir, "policy.json")
            ca_download = "/srv/infra-tools/web/infra-tools-ca.crt"
            with (
                patch.object(godot_web_steps, "GODOT_WEB_POLICY_FILE", policy_file),
                patch.object(
                    godot_web_steps,
                    "GODOT_WEB_CA_DOWNLOAD",
                    ca_download,
                ),
                patch.object(
                    godot_web_steps,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                ),
            ):
                changed = godot_web_steps._configure_web_policy(
                    "https://192.0.2.10:8443",
                    "/cert",
                    "/key",
                    True,
                    ["agent"],
                    ["192.0.2.0/24"],
                )

            self.assertTrue(changed)
            with open(policy_file, encoding="utf-8") as file_obj:
                policy = json.load(file_obj)
            self.assertEqual(policy["ca_certificate"], ca_download)

    def test_installs_local_ca_in_managed_users_chromium_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            home = os.path.join(temporary_dir, "home")
            os.mkdir(home)
            trust_cert = os.path.join(temporary_dir, "ca.crt")
            with open(trust_cert, "w", encoding="utf-8") as cert_file:
                cert_file.write(
                    "-----BEGIN CERTIFICATE-----\nmanaged-ca\n"
                    "-----END CERTIFICATE-----\n"
                )
            account = SimpleNamespace(pw_dir=home)

            def run_command(command: str, **_kwargs: object) -> SimpleNamespace:
                if " certutil -L " in command:
                    return SimpleNamespace(returncode=255, stdout="", stderr="missing")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch.object(godot_web_steps, "GODOT_WEB_TRUST_CERT", trust_cert),
                patch.object(godot_web_steps, "is_package_installed", return_value=True),
                patch.object(godot_web_steps.pwd, "getpwnam", return_value=account),
                patch.object(godot_web_steps, "run", side_effect=run_command) as run,
            ):
                changed = godot_web_steps._install_chromium_ca_trust(["agent"])

            self.assertTrue(changed)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(" certutil -N " in command for command in commands))
            self.assertTrue(any(" certutil -A " in command for command in commands))
            self.assertFalse(any(" certutil -D " in command for command in commands))

    def test_existing_chromium_ca_is_not_readded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            home = os.path.join(temporary_dir, "home")
            database = os.path.join(home, ".local", "share", "pki", "nssdb")
            os.makedirs(database)
            with open(os.path.join(database, "cert9.db"), "w", encoding="utf-8"):
                pass
            trust_cert = os.path.join(temporary_dir, "ca.crt")
            certificate = (
                "-----BEGIN CERTIFICATE-----\nmanaged-ca\n"
                "-----END CERTIFICATE-----\n"
            )
            with open(trust_cert, "w", encoding="utf-8") as cert_file:
                cert_file.write(certificate)
            account = SimpleNamespace(pw_dir=home)

            def run_command(command: str, **_kwargs: object) -> SimpleNamespace:
                stdout = certificate if " certutil -L " in command else ""
                return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

            with (
                patch.object(godot_web_steps, "GODOT_WEB_TRUST_CERT", trust_cert),
                patch.object(godot_web_steps, "is_package_installed", return_value=True),
                patch.object(godot_web_steps.pwd, "getpwnam", return_value=account),
                patch.object(godot_web_steps, "run", side_effect=run_command) as run,
            ):
                changed = godot_web_steps._install_chromium_ca_trust(["agent"])

            self.assertFalse(changed)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(" certutil -M " in command for command in commands))
            self.assertFalse(any(" certutil -N " in command for command in commands))
            self.assertFalse(any(" certutil -A " in command for command in commands))

    def test_installs_shared_agent_skills_for_codex_or_opencode(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            account = SimpleNamespace(
                pw_dir=home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            source_root = os.path.join(
                os.path.dirname(godot_web_steps.__file__),
                "agent_skills",
            )
            with (
                patch.object(godot_web_steps, "GODOT_AGENT_SKILLS_ROOT", source_root),
                patch("common.agent_steps.pwd.getpwnam", return_value=account),
                patch("common.agent_steps.os.chown"),
            ):
                changed = godot_web_steps.configure_godot_agent_skills(
                    "agent",
                    ["codex"],
                )

            self.assertTrue(changed)
            self.assertEqual(
                set(godot_web_steps.GODOT_AGENT_SKILLS),
                {
                    *BASE_AGENT_SKILL_NAMES,
                    "infra-tools-godot-web",
                    "infra-tools-web-gateway",
                },
            )
            for skill_name in godot_web_steps.GODOT_AGENT_SKILLS:
                skill_path = os.path.join(
                    home,
                    ".agents",
                    "skills",
                    skill_name,
                    "SKILL.md",
                )
                self.assertTrue(os.path.isfile(skill_path))
                with open(skill_path, encoding="utf-8") as file_obj:
                    self.assertIn("managed-by: infra_tools", file_obj.read())

    def test_does_not_install_agent_skills_without_supported_agent(self) -> None:
        with patch("common.agent_steps.pwd.getpwnam") as getpwnam:
            changed = godot_web_steps.configure_godot_agent_skills("agent", ["gh"])

        self.assertFalse(changed)
        getpwnam.assert_not_called()


class TestGodotWebPublisher(unittest.TestCase):
    def test_json_export_streams_logs_without_buffering_or_polluting_result(self) -> None:
        for returncode in (0, 1):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as directory:
                project = os.path.join(directory, "project")
                games = os.path.join(directory, "games")
                os.makedirs(project)
                os.makedirs(os.path.join(games, "agent"))
                with open(os.path.join(project, "project.godot"), "w", encoding="utf-8"):
                    pass
                output, errors = io.StringIO(), io.StringIO()

                def export(command, **kwargs):
                    self.assertNotIn("capture_output", kwargs)
                    self.assertIs(kwargs["stdout"], errors)
                    print("export diagnostic", file=kwargs["stdout"])
                    if returncode == 0:
                        with open(command[-1], "w", encoding="utf-8") as exported:
                            exported.write("game")
                    return SimpleNamespace(returncode=returncode)

                with (
                    patch.object(godot_web_publish, "GAMES_ROOT", games),
                    patch.object(godot_web_publish, "_base_url", return_value=None),
                    patch.object(godot_web_publish, "_current_account", return_value=SimpleNamespace(pw_name="agent", pw_uid=os.getuid())),
                    patch.object(godot_web_publish, "run", side_effect=export),
                    redirect_stdout(output),
                    redirect_stderr(errors),
                ):
                    status = godot_web_publish.main(["demo", "--project", project, "--json"])
                self.assertEqual(status, returncode)
                self.assertEqual(json.loads(output.getvalue())["ok"], returncode == 0)
                self.assertEqual(errors.getvalue(), "export diagnostic\n")

    def test_failed_activation_restores_previous_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            destination_dir = os.path.join(temporary_dir, "demo")
            staging_dir = os.path.join(temporary_dir, ".demo-staging")
            os.mkdir(destination_dir)
            os.mkdir(staging_dir)
            previous_file = os.path.join(destination_dir, "index.html")
            with open(previous_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("previous")

            real_replace = os.replace

            def fail_activation(source: str, destination: str) -> None:
                if source == staging_dir and destination == destination_dir:
                    raise OSError("activation failed")
                real_replace(source, destination)

            with patch.object(
                godot_web_publish.os,
                "replace",
                side_effect=fail_activation,
            ):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    godot_web_publish._replace_export(staging_dir, destination_dir)

            with open(previous_file, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "previous")

    def test_publish_uses_release_preset_and_atomically_activates_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_dir = os.path.join(temporary_dir, "project")
            games_root = os.path.join(temporary_dir, "games")
            user_root = os.path.join(games_root, "agent")
            os.makedirs(project_dir)
            os.makedirs(user_root)
            with open(os.path.join(project_dir, "project.godot"), "w", encoding="utf-8"):
                pass
            url_file = os.path.join(temporary_dir, "base-url")
            with open(url_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("https://games.example:8443\n")

            account = SimpleNamespace(pw_uid=os.getuid(), pw_name="agent")

            def create_export(command: list[str], **_kwargs: object) -> SimpleNamespace:
                export_path = command[-1]
                with open(export_path, "w", encoding="utf-8") as export_file:
                    export_file.write("game")
                with open(os.path.join(os.path.dirname(export_path), "demo.wasm"), "wb") as wasm:
                    wasm.write(b"wasm" * 1024)
                with open(os.path.join(os.path.dirname(export_path), "demo.pck"), "wb") as pack:
                    pack.write(b"pack" * 1024)
                return SimpleNamespace(returncode=0)

            with (
                patch.object(godot_web_publish, "GAMES_ROOT", games_root),
                patch.object(godot_web_publish, "BASE_URL_FILE", url_file),
                patch.object(godot_web_publish, "_current_account", return_value=account),
                patch.object(
                    godot_web_publish,
                    "run",
                    side_effect=create_export,
                ) as run_export,
            ):
                result = godot_web_publish.main(["demo", project_dir])

            self.assertEqual(result, 0)
            published_file = os.path.join(user_root, "demo", "index.html")
            self.assertTrue(os.path.isfile(published_file))
            self.assertEqual(os.stat(os.path.dirname(published_file)).st_mode & 0o777, 0o755)
            self.assertEqual(os.stat(published_file).st_mode & 0o777, 0o644)
            self.assertTrue(os.path.isfile(os.path.join(user_root, "demo", "demo.wasm.gz")))
            self.assertTrue(os.path.isfile(os.path.join(user_root, "demo", ".infra-tools.json")))
            self.assertTrue(os.path.isfile(os.path.join(user_root, "index.html")))
            command = run_export.call_args.args[0]
            self.assertEqual(command[:2], ["godot", "--headless"])
            self.assertIn("--export-release", command)
            self.assertIn("Web", command)

    def test_publish_rejects_unsafe_game_name_before_running_godot(self) -> None:
        with (
            patch.object(godot_web_publish, "_current_account") as current_account,
            patch.object(godot_web_publish, "run") as run_export,
        ):
            result = godot_web_publish.main(["../escape"])

        self.assertEqual(result, 2)
        current_account.assert_not_called()
        run_export.assert_not_called()

    def test_publish_derives_slug_from_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_dir = os.path.join(temporary_dir, "project")
            games_root = os.path.join(temporary_dir, "games")
            user_root = os.path.join(games_root, "agent")
            os.makedirs(project_dir)
            os.makedirs(user_root)
            with open(os.path.join(project_dir, "project.godot"), "w", encoding="utf-8") as project:
                project.write('[application]\nconfig/name="My Great Game!"\n')
            account = SimpleNamespace(pw_uid=os.getuid(), pw_name="agent")

            def create_export(command: list[str], **_kwargs: object) -> SimpleNamespace:
                with open(command[-1], "w", encoding="utf-8") as export_file:
                    export_file.write("game")
                return SimpleNamespace(returncode=0)

            with (
                patch.object(godot_web_publish, "GAMES_ROOT", games_root),
                patch.object(godot_web_publish, "BASE_URL_FILE", os.path.join(temporary_dir, "url")),
                patch.object(godot_web_publish, "_current_account", return_value=account),
                patch.object(godot_web_publish, "run", side_effect=create_export),
            ):
                result = godot_web_publish.main(
                    ["--no-precompress", "--project", project_dir]
                )

            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(user_root, "my-great-game", "index.html")))

    def test_json_publish_reports_artifact_sizes_elapsed_time_and_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            project_dir = os.path.join(temporary_dir, "project")
            games_root = os.path.join(temporary_dir, "games")
            user_root = os.path.join(games_root, "agent")
            os.makedirs(project_dir)
            os.makedirs(user_root)
            with open(os.path.join(project_dir, "project.godot"), "w", encoding="utf-8"):
                pass
            url_file = os.path.join(temporary_dir, "base-url")
            with open(url_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("https://games.example:8443\n")
            account = SimpleNamespace(pw_uid=os.getuid(), pw_name="agent")

            def create_export(command: list[str], **_kwargs: object) -> SimpleNamespace:
                export_dir = os.path.dirname(command[-1])
                with open(command[-1], "w", encoding="utf-8") as export_file:
                    export_file.write("game")
                with open(os.path.join(export_dir, "demo.wasm"), "wb") as wasm:
                    wasm.write(b"wasm" * 1024)
                return SimpleNamespace(returncode=0)

            results: list[dict[str, object]] = []
            with (
                patch.object(godot_web_publish, "GAMES_ROOT", games_root),
                patch.object(godot_web_publish, "BASE_URL_FILE", url_file),
                patch.object(godot_web_publish, "_current_account", return_value=account),
                patch.object(godot_web_publish, "run", side_effect=create_export),
            ):
                for _attempt in range(2):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        result = godot_web_publish.main(
                            ["demo", "--project", project_dir, "--json"]
                        )
                    self.assertEqual(result, 0)
                    results.append(json.loads(output.getvalue()))

            self.assertFalse(results[0]["replaced"])
            self.assertTrue(results[1]["replaced"])
            self.assertEqual(results[0]["artifact_count"], 3)
            self.assertEqual(results[0]["compressed_artifact_count"], 1)
            self.assertGreater(results[0]["artifact_bytes"], 4096)
            self.assertGreater(results[0]["compressed_artifact_bytes"], 0)
            self.assertGreaterEqual(results[0]["elapsed_seconds"], 0)
            self.assertEqual(results[0]["url"], "https://games.example:8443/games/agent/demo/")


if __name__ == "__main__":
    unittest.main()
