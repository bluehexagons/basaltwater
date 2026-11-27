"""Focused manifest selectors keep unrelated discovery out of agent context."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from lib.agent_environment import add_manifest_parser, run_manifest_command, load_project_environment, _container_engine_inventory
from lib.agent_probe import ProbeResult
from lib.workstation_report import inspect_workstation


class ManifestOutputTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {"schema_version": 1, "host_profile": "cachyos-workstation",
                "basaltwater": {"commit": "current"}, "project_runtime": None,
                "installation_context": None, "health_command": "doctor",
                "tools": {"blender": "/bin/blender", "inkscape": "/bin/inkscape", "git": "/bin/git"},
                "desktop_applications": {"blender": {"readiness": "unverified"},
                                         "inkscape": {"readiness": "unverified"}},
                "desktop_skills": ["/skills/desktop"], "skills": {"large_catalog": True},
                "development": {"readiness": "unverified"}, "required_tools": {}, "recipes": {},
                "browser": {"unrelated": True}}

    def run_cli(self, *argv):
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        with (patch("lib.agent_environment.inspect_environment", return_value=self.manifest()),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            code = run_manifest_command(parser.parse_args(["manifest", "--json", *argv]))
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_default_retains_full_inventory(self) -> None:
        self.assertEqual(self.run_cli(), self.manifest())

    def test_scoped_discovery_only_runs_relevant_probes(self) -> None:
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        skill = {"name": "basaltwater-desktop", "path": "/skills/desktop/SKILL.md", "status": "current"}
        inventory = {"catalogs": [{"providers": ["codex"], "skills": [skill]}]}
        installation = {"commit": "current"}
        for scope, expected in (
            ("workspace", (False, False, False, True, True)),
            ("browser", (True, False, False, False, False)),
            ("web", (True, True, False, True, True)),
            ("development", (False, True, False, True, False)),
            ("desktop", (False, False, True, False, False)),
            ("skills", (False, False, True, False, False)),
            ("installation", (False, False, True, False, False)),
            ("publishing", (False, False, False, False, False)),
        ):
            with (self.subTest(scope=scope), tempfile.TemporaryDirectory() as root,
                  patch("lib.agent_workspace._repository_root", return_value=root),
                  patch("lib.agent_workspace._effective_home", return_value=root),
                  patch("lib.agent_workspace._git", return_value=CompletedProcess([], 0)) as git,
                  patch("lib.agent_workspace._worktree_record",
                        return_value={"branch": "main", "head": "a" * 40, "dirty": False}) as worktree,
                  patch("lib.agent_environment.load_project_environment", wraps=load_project_environment) as load_project,
                  patch("lib.agent_environment.is_cachyos", return_value=False),
                  patch("lib.agent_environment.platform.release", return_value="6.12-debian"),
                  patch("lib.agent_environment.inspect_installation_provenance", return_value=installation),
                  patch("lib.agent_environment.shutil.which", side_effect={
                      "blender": "/bin/blender", "musescore4": "/bin/musescore4", "vim": "/bin/vim",
                  }.get) as which,
                  patch("lib.agent_environment.browser_context_inventory", return_value=[]) as browser,
                  patch("lib.agent_environment._desktop_development", return_value={}) as development,
                  patch("lib.agent_environment.inspect_skill_catalogs", return_value=inventory) as skills,
                  patch("sys.stdout", new_callable=io.StringIO) as output):
                (Path(root) / "basaltwater-agent.json").write_text(
                    json.dumps({"version": 1, "artifact_directories": [".artifacts"]}),
                    encoding="utf-8",
                )
                args = parser.parse_args(["manifest", "--capability", scope, "--json"])
                self.assertEqual(run_manifest_command(args), 0)
                result = json.loads(output.getvalue())
                self.assertEqual((browser.called, development.called, skills.called,
                                  load_project.called, worktree.called), expected)
                if worktree.called:
                    git.assert_called_once_with(root, ["check-ignore", "-q", "--", ".artifacts/"], check=False)
                    self.assertEqual(result["workspace"]["artifact_directories"],
                                     [{"path": ".artifacts", "ignored": True}])
                else:
                    git.assert_not_called()
                if scope in {"installation", "skills"}:
                    which.assert_not_called()
                if scope in {"development", "web"}:
                    self.assertEqual(result["tools"]["vim"], "/bin/vim")
                    self.assertIsNone(result["tools"]["rg"])
                else:
                    self.assertTrue({"vim", "rg"}.isdisjoint(call.args[0] for call in which.call_args_list))
                if scope == "browser":
                    self.assertEqual({entry.args[0] for entry in which.call_args_list},
                                     {"basaltwater-playwright-mcp", "firefox", "firefox-esr", "librewolf", "node",
                                      "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"})
                self.assertEqual(result["provenance"]["source_revisions"]["invoked"], "current")
                if scope in {"installation", "skills"}:
                    skills.assert_called_once_with(root)
                    self.assertEqual(result["provenance"]["delivered_skills"][0]["name"], skill["name"])
                else:
                    self.assertNotIn("delivered_skills", result["provenance"])
                if scope == "desktop":
                    skills.assert_called_once_with(root, names=("basaltwater-desktop",))
                    self.assertEqual(result["desktop_skills"], [skill["path"]])
                    self.assertEqual(result["tools"]["musescore"], "/bin/musescore4")

    def test_named_application_implies_desktop_scope_and_keeps_provenance(self) -> None:
        result = self.run_cli("--application", "blender")
        self.assertEqual(set(result["desktop_applications"]), {"blender"})
        self.assertEqual(result["tools"], {"blender": "/bin/blender"})
        self.assertEqual(result["basaltwater"], {"commit": "current"})
        self.assertEqual(result["desktop_applications"]["blender"]["readiness"], "unverified")
        self.assertNotIn("skills", result)
        self.assertNotIn("browser", result)

    def test_named_browsers_report_their_supervised_launch_commands(self) -> None:
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        browsers = ("firefox-esr", "chromium", "librewolf")
        with (tempfile.TemporaryDirectory() as root,
              patch("lib.agent_workspace._repository_root", return_value=root),
              patch("lib.agent_environment.is_cachyos", return_value=False),
              patch("lib.agent_environment.platform.release", return_value="6.12-debian"),
              patch("lib.agent_environment.inspect_installation_provenance", return_value={}),
              patch("lib.agent_environment.inspect_skill_catalogs", return_value={"catalogs": []}),
              patch("lib.agent_environment.shutil.which",
                    side_effect={name: f"/usr/bin/{name}" for name in browsers}.get),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            selectors = [arg for name in browsers for arg in ("--application", name)]
            args = parser.parse_args(["manifest", *selectors, "--json"])
            self.assertEqual(run_manifest_command(args), 0)
        apps = json.loads(output.getvalue())["desktop_applications"]
        self.assertEqual(set(apps), set(browsers))
        for name in browsers:
            self.assertEqual(apps[name]["launch_argv"], [
                "basaltw", "desktop", "develop", "browser", "--browser", name, "--project", root,
            ])

    def test_desktop_scope_and_repeated_applications(self) -> None:
        result = self.run_cli("--capability", "desktop")
        self.assertEqual(set(result["desktop_applications"]), {"blender", "inkscape"})
        result = self.run_cli("--application", "blender", "--application", "inkscape",
                              "--capability", "development")
        self.assertEqual(set(result["desktop_applications"]), {"blender", "inkscape"})
        self.assertIn("development", result)
        self.assertNotIn("skills", result)

    def test_uninstalled_named_application_is_empty_inventory(self) -> None:
        result = self.run_cli("--application", "krita")
        self.assertEqual(result["desktop_applications"], {})
        self.assertNotIn("tools", result)

    def test_development_scope_reports_godot_preparation_per_checkout(self) -> None:
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        with (tempfile.TemporaryDirectory() as temporary,
              patch("lib.agent_workspace._repository_root", side_effect=lambda path: path),
              patch("lib.agent_environment.is_cachyos", return_value=True),
              patch("lib.agent_environment.platform.release", return_value="6.18-cachyos"),
              patch("lib.agent_environment.inspect_installation_provenance", return_value={}),
              patch("lib.agent_environment.shutil.which", return_value="/usr/bin/godot")):
            prepared, fresh = (Path(temporary) / name for name in ("prepared", "fresh worktree"))
            declaration = {"version": 1, "recipes": {"graphics": {
                "description": "Graphics smoke", "argv": ["do-not-run"], "requires": ["godot"],
                "godot_prepare": {"project": "game", "asset_directories": ["assets/art"]}}}}
            for root in (prepared, fresh):
                (root / "game").mkdir(parents=True)
                (root / "game/project.godot").touch()
                (root / "basaltwater-agent.json").write_text(json.dumps(declaration))
            (prepared / "game/assets/art").mkdir(parents=True)
            (prepared / "game/.godot/imported").mkdir(parents=True)

            def discover(root):
                args = parser.parse_args(["manifest", str(root), "--capability", "development", "--json"])
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(run_manifest_command(args), 0)
                recipe = json.loads(output.getvalue())["recipes"]["graphics"]
                self.assertEqual(recipe["missing_tools"], [])
                return recipe["godot_preparation"]

            result = discover(fresh)
            self.assertEqual(result["project"], str(fresh / "game"))
            self.assertEqual(result["state"], "needs-preparation")
            self.assertEqual(result["missing_asset_directories"], ["assets/art"])
            self.assertEqual(result["import_cache"], "missing")
            command = ["basaltw", "desktop", "--native", "develop", "check", "graphics",
                       "--project", str(fresh), "--prepare"]
            self.assertEqual(result["prepare_argv"], [*command, "--asset-bundle", "/absolute/local-qa-bundle"])
            self.assertFalse((fresh / "game/.godot").exists())
            (fresh / "game/assets/art").mkdir(parents=True)
            self.assertEqual(discover(fresh)["prepare_argv"], command)

            result = discover(prepared)
            self.assertEqual(result["state"], "prerequisites-present")
            self.assertEqual(result["missing_asset_directories"], [])
            self.assertEqual(result["import_cache"], "present")
            self.assertEqual(result["gpu_readiness"], "unverified")
            self.assertIsNone(result["prepare_argv"])

            (fresh / "game/.godot").symlink_to(prepared / "game/.godot", target_is_directory=True)
            result = discover(fresh)
            self.assertEqual(result["state"], "unverified")
            self.assertEqual(result["import_cache"], "unverified")
            self.assertIn("without symlinks", result["issue"])

    def test_workstation_scope_shares_project_declaration_and_reports_engines(self) -> None:
        parser = argparse.ArgumentParser()
        add_manifest_parser(parser.add_subparsers())
        with (tempfile.TemporaryDirectory() as root,
              patch("lib.agent_workspace._repository_root", return_value=root),
              patch("lib.agent_workspace._worktree_record") as worktree,
              patch("lib.agent_environment.load_project_environment", wraps=load_project_environment) as load_project,
              patch("lib.agent_environment._desktop_development", return_value={}),
              patch("lib.agent_environment.inspect_installation_provenance", return_value={"commit": "current"}),
              patch("lib.workstation_report.inspect_workstation", return_value={"status": "observed"}) as report,
              patch("lib.agent_environment.shutil.which", side_effect={
                  "podman": "/usr/bin/podman", "example-tool": "/bin/example-tool",
              }.get),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            source = Path(root) / "basaltwater-agent.json"
            source.write_text(json.dumps({"version": 1, "required_tools": ["example-tool"]}), encoding="utf-8")
            code = run_manifest_command(parser.parse_args([
                "manifest", "--capability", "workstation", "--capability", "development", "--json",
            ]))
        self.assertEqual(code, 0)
        load_project.assert_called_once_with(root)
        worktree.assert_not_called()
        self.assertEqual(report.call_count, 1)
        repository, declaration = report.call_args.args
        self.assertEqual(repository, root)
        self.assertEqual(declaration["required_tools"], ["example-tool"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["project_source"], str(source))
        self.assertEqual(result["required_tools"], {
            "example-tool": {"executable": "/bin/example-tool", "status": "available"},
        })
        self.assertEqual(result["workstation"]["status"], "observed")
        self.assertEqual(result["basaltwater"], {"commit": "current"})
        self.assertNotIn("desktop_applications", result)
        self.assertNotIn("skills", result)
        self.assertEqual(result["workstation"]["container_engines"], {
            "podman": {"status": "available", "executable": "/usr/bin/podman",
                       "argv": ["/usr/bin/podman"]},
            "docker": {"status": "unavailable", "executable": None, "argv": None},
        })

    def test_declared_wrapper_is_discovered_without_executing_project_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            declaration = {"version": 1, "recipes": {"engine": {
                "description": "Project wrapper", "argv": ["bash", "scripts/podman.sh"],
                "requires": ["bash"]}}, "container_engines": {"podman": "engine"}}
            manifest = root / "basaltwater-agent.json"
            manifest.write_text(json.dumps(declaration))
            with patch("lib.agent_environment.shutil.which", return_value=None):
                project = load_project_environment(temporary)
                result = _container_engine_inventory(temporary, project)
            self.assertEqual(result["podman"]["status"], "declared")
            self.assertEqual(result["podman"]["argv"], ["bash", "scripts/podman.sh"])
            self.assertEqual(Path(result["podman"]["directory"]), root)
            self.assertEqual(result["docker"]["status"], "unavailable")
            for engines in ({"other": "engine"}, {"podman": "missing"}, {"podman": []}):
                manifest.write_text(json.dumps({**declaration, "container_engines": engines}))
                with self.subTest(engines=engines), self.assertRaisesRegex(ValueError, "container_engines"):
                    load_project_environment(temporary)


class WorkstationReportTests(unittest.TestCase):
    def test_versions_are_bounded_system_probes_and_never_verify_a_project(self) -> None:
        project = {"source": None, "required_tools": [], "recipes": {
            "capture": {"description": "Project capture", "requires": ["missing"], "argv": ["do-not-run"]}}}
        def which(name):
            return {"git": "/usr/bin/git", "node": "/home/user/bin/node",
                    "blender": "/usr/bin/blender"}.get(name)
        with (patch("lib.workstation_report.shutil.which", side_effect=which),
              patch("lib.workstation_report.run_probe", return_value=ProbeResult("git version 2.50.1\n")) as probe,
              patch("lib.workstation_report._gpus", return_value={"source": "unavailable", "devices": []})):
            result = inspect_workstation("/project with spaces", project)
        probe.assert_called_once_with(["/usr/bin/git", "--version"], timeout=2, max_bytes=8192,
                                      env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(result["tools"]["git"]["version"], "2.50.1")
        self.assertTrue(result["tools"]["blender"]["installed"])
        self.assertIsNone(result["tools"]["git"]["usable_in_session"])
        self.assertIsNone(result["tools"]["git"]["verified_by_project"])
        self.assertEqual(result["recipes"]["capture"]["missing_tools"], ["missing"])
        self.assertFalse(result["rendering_backends"]["xvfb_glx"]["tools_installed"])
        self.assertIn("/project with spaces", result["commands"]["project_check"])

    def test_local_wrappers_are_not_executed_and_probe_failure_stays_unknown(self) -> None:
        from lib.workstation_report import _tool

        with (patch("lib.workstation_report.shutil.which", return_value="/home/user/bin/git"),
              patch("lib.workstation_report.run_probe") as probe):
            self.assertEqual(_tool("git")["version_probe"], "not-run")
        probe.assert_not_called()
        with (patch("lib.workstation_report.shutil.which", return_value="/usr/bin/git"),
              patch("lib.workstation_report.run_probe", return_value=ProbeResult(issue="probe_timeout"))):
            result = _tool("git")
        self.assertTrue(result["installed"])
        self.assertEqual(result["version_probe"], "probe_timeout")
        self.assertIsNone(result["version"])

    def test_ffmpeg_release_tag_is_not_confused_with_linked_library_versions(self) -> None:
        from lib.workstation_report import _tool

        for banner, expected in (("ffmpeg version n8.1.2 Copyright", "n8.1.2"),
                                 ("ffmpeg version 8.1.2-3 Copyright", "8.1.2-3"),
                                 ("unrecognized banner", None)):
            with (self.subTest(banner=banner),
                  patch("lib.workstation_report.shutil.which", return_value="/usr/bin/ffmpeg"),
                  patch("lib.workstation_report.run_probe", return_value=ProbeResult(banner + "\nlibavutil 61.4.100\n")) as probe):
                self.assertEqual(_tool("ffmpeg")["version"], expected)
                self.assertEqual(probe.call_args.args[0], ["/usr/bin/ffmpeg", "-version"])

    def test_gpu_identity_is_kernel_metadata_without_a_device_probe(self) -> None:
        from lib.workstation_report import _gpus

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "card0" / "device"
            device.mkdir(parents=True)
            (device / "vendor").write_text("0x1002\n")
            (device / "device").write_text("0x1234\n")
            (device / "driver").symlink_to("/sys/bus/pci/drivers/amdgpu")
            (root / "card0-DP-1").mkdir()
            with patch("lib.workstation_report.Path", side_effect=lambda path: root if path == "/sys/class/drm" else Path(path)):
                result = _gpus()
        self.assertEqual(result["devices"], [{"card": "card0", "pci_id": "1002:1234",
                                             "driver": "amdgpu", "usable_in_session": None}])


if __name__ == "__main__":
    unittest.main()
