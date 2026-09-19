"""Tests for lib/project_manifest.py: basaltwater.json parsing and validation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.project_manifest import (
    MANIFEST_FILENAME,
    Manifest,
    has_placeholder,
    infer_manifest,
    load_manifest,
    parse_manifest,
    render_template,
)


def _static(**overrides: object) -> dict:
    base = {
        "name": "site",
        "type": "static",
        "domain": "example.com",
        "build": "npm ci && npm run build",
        "output": "dist",
    }
    base.update(overrides)
    return base


def _service(**overrides: object) -> dict:
    base = {
        "name": "api",
        "type": "service",
        "domain": "api.example.com",
        "build": "server/deploy/build.sh",
        "binary": "server/app",
        "port": 8080,
    }
    base.update(overrides)
    return base


def _manifest(*components: dict, version: object = 1) -> dict:
    return {"version": version, "components": list(components)}


class TestValidParse(unittest.TestCase):
    def test_multi_component(self):
        manifest = parse_manifest(_manifest(_static(), _service()))
        self.assertIsInstance(manifest, Manifest)
        self.assertEqual(manifest.version, 1)
        self.assertEqual([c.name for c in manifest.components], ["site", "api"])

        site, api = manifest.components
        self.assertTrue(site.is_static)
        self.assertEqual(site.output, "dist")
        self.assertEqual(site.path, "/")  # default
        self.assertEqual(site.build, ["npm ci && npm run build"])

        self.assertTrue(api.is_service)
        self.assertEqual(api.port, 8080)
        self.assertEqual(api.binary, "server/app")
        self.assertTrue(api.reverse_proxy)  # default

    def test_build_as_array(self):
        comp = parse_manifest(_manifest(_static(build=["a", "b"]))).components[0]
        self.assertEqual(comp.build, ["a", "b"])

    def test_build_omitted(self):
        comp = parse_manifest(_manifest(_static(build=None))).components[0]
        self.assertEqual(comp.build, [])

    def test_env_parsed(self):
        comp = parse_manifest(_manifest(_static(env={"VITE_SHOP": "1"}))).components[0]
        self.assertEqual(comp.env, {"VITE_SHOP": "1"})

    def test_service_with_exec_instead_of_binary(self):
        comp = parse_manifest(
            _manifest(_service(binary=None, exec="/usr/bin/app --serve"))
        ).components[0]
        self.assertIsNone(comp.binary)
        self.assertEqual(comp.exec, "/usr/bin/app --serve")

    def test_service_optional_fields(self):
        comp = parse_manifest(
            _manifest(
                _service(
                    env_file="/opt/app/.env",
                    health="/api/health",
                    reverse_proxy=False,
                    working_dir="server",
                    sqlite_backup="{{data_dir}}/app.sqlite3",
                    backup_retention=7,
                )
            )
        ).components[0]
        self.assertEqual(comp.env_file, "/opt/app/.env")
        self.assertEqual(comp.health, "/api/health")
        self.assertFalse(comp.reverse_proxy)
        self.assertEqual(comp.working_dir, "server")
        self.assertEqual(comp.sqlite_backup, "{{data_dir}}/app.sqlite3")
        self.assertEqual(comp.backup_retention, 7)

    def test_service_auto_port(self):
        comp = parse_manifest(_manifest(_service(port="auto"))).components[0]
        self.assertIsNone(comp.port)

    def test_runtime_env_accepts_deploy_templates(self):
        comp = parse_manifest(
            _manifest(
                _service(
                    runtime_env={
                        "APP_DATABASE": "{{data_dir}}/app.sqlite3",
                        "APP_PORT": "{{port}}",
                    }
                )
            )
        ).components[0]
        self.assertEqual(comp.runtime_env["APP_DATABASE"], "{{data_dir}}/app.sqlite3")


class TestRejects(unittest.TestCase):
    def _assert_rejected(self, data: object, needle: str = "") -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_manifest(data)
        if needle:
            self.assertIn(needle, str(ctx.exception))

    def test_not_an_object(self):
        self._assert_rejected([], "must be a JSON object")

    def test_bad_version(self):
        self._assert_rejected(_manifest(_static(), version=2), "version")

    def test_missing_version(self):
        self._assert_rejected({"components": [_static()]}, "version")

    def test_empty_components(self):
        self._assert_rejected(_manifest(), "non-empty")

    def test_components_not_array(self):
        self._assert_rejected({"version": 1, "components": {}}, "non-empty")

    def test_duplicate_names(self):
        self._assert_rejected(
            _manifest(_static(name="dup"), _service(name="dup")), "duplicate"
        )

    def test_unknown_type(self):
        self._assert_rejected(_manifest(_static(type="lambda")), "'static', 'godot-web' or 'service'")

    def test_missing_required_field(self):
        comp = _static()
        del comp["output"]
        self._assert_rejected(_manifest(comp), "output")

    def test_missing_domain(self):
        comp = _static()
        del comp["domain"]
        self._assert_rejected(_manifest(comp), "domain")

    def test_invalid_domain(self):
        self._assert_rejected(_manifest(_static(domain="not a host")), "domain")

    def test_domain_placeholder_accepted(self):
        comp = parse_manifest(_manifest(_static(domain="{{domain}}"))).components[0]
        self.assertEqual(comp.domain, "{{domain}}")

    def test_invalid_name(self):
        self._assert_rejected(_manifest(_static(name="Site_1")), "name")

    def test_unknown_field(self):
        self._assert_rejected(_manifest(_static(extra="x")), "unknown field")

    def test_top_level_unknown_field(self):
        data = _manifest(_static())
        data["extras"] = True
        self._assert_rejected(data, "unknown field")

    def test_output_escapes_root(self):
        self._assert_rejected(_manifest(_static(output="../etc")), "escape")

    def test_output_absolute(self):
        self._assert_rejected(_manifest(_static(output="/etc/passwd")), "relative")

    def test_binary_escapes_root(self):
        self._assert_rejected(_manifest(_service(binary="../../bin/sh")), "escape")

    def test_port_out_of_range(self):
        self._assert_rejected(_manifest(_service(port=80)), "between")

    def test_port_too_high(self):
        self._assert_rejected(_manifest(_service(port=70000)), "between")

    def test_port_not_int(self):
        self._assert_rejected(_manifest(_service(port="8080")), "integer")

    def test_custom_systemd_unit_is_rejected(self):
        self._assert_rejected(
            _manifest(_service(systemd_unit="deploy/app.service")),
            "unknown field",
        )

    def test_port_bool_rejected(self):
        self._assert_rejected(_manifest(_service(port=True)), "integer")

    def test_service_both_binary_and_exec(self):
        self._assert_rejected(
            _manifest(_service(exec="run")), "exactly one"
        )

    def test_service_neither_binary_nor_exec(self):
        self._assert_rejected(_manifest(_service(binary=None)), "exactly one")

    def test_exec_rejects_systemd_privilege_prefixes(self):
        for command in ("+/bin/sh", "!/bin/sh", "-+/bin/sh"):
            with self.subTest(command=command):
                self._assert_rejected(
                    _manifest(_service(binary=None, exec=command)),
                    "privilege-control prefixes",
                )

    def test_exec_rejects_injected_unit_directive(self):
        self._assert_rejected(
            _manifest(_service(binary=None, exec="/bin/true\nUser=root")),
            "control characters",
        )

    def test_env_file_not_absolute(self):
        self._assert_rejected(_manifest(_service(env_file="relative/.env")), "absolute")

    def test_env_value_not_string(self):
        self._assert_rejected(_manifest(_static(env={"K": 1})), "must be a string")

    def test_env_name_with_shell_syntax_rejected(self):
        self._assert_rejected(
            _manifest(_static(env={"SAFE; touch /tmp/unsafe": "1"})),
            "env variable",
        )

    def test_env_name_starting_with_digit_rejected(self):
        self._assert_rejected(_manifest(_static(env={"1INVALID": "1"})), "env variable")

    def test_env_name_with_underscore_accepted(self):
        component = parse_manifest(
            _manifest(_static(env={"_PRIVATE_VALUE_2": "1"}))
        ).components[0]
        self.assertEqual(component.env, {"_PRIVATE_VALUE_2": "1"})

    def test_build_empty_string(self):
        self._assert_rejected(_manifest(_static(build="  ")), "non-empty")

    def test_build_wrong_type(self):
        self._assert_rejected(_manifest(_static(build=5)), "string or array")

    def test_path_without_leading_slash(self):
        self._assert_rejected(_manifest(_static(path="sub")), "path")

    def test_health_without_leading_slash(self):
        self._assert_rejected(_manifest(_service(health="health")), "health")

    def test_path_rejects_injected_nginx_directive(self):
        self._assert_rejected(
            _manifest(_static(path="/\nlocation /admin")),
            "control characters",
        )

    def test_reverse_proxy_not_bool(self):
        self._assert_rejected(_manifest(_service(reverse_proxy="yes")), "boolean")

    def test_sqlite_backup_must_be_absolute_or_templated(self):
        self._assert_rejected(_manifest(_service(sqlite_backup="data/app.db")), "absolute")

    def test_backup_retention_is_bounded(self):
        self._assert_rejected(_manifest(_service(backup_retention=0)), "backup_retention")


class TestTemplating(unittest.TestCase):
    def test_render_substitutes(self):
        out = render_template(
            "{{base_dir}}/{{name}}/.env",
            {"base_dir": "/var/www", "name": "api"},
        )
        self.assertEqual(out, "/var/www/api/.env")

    def test_render_allows_inner_whitespace(self):
        self.assertEqual(render_template("{{ port }}", {"port": "8080"}), "8080")

    def test_render_unknown_variable_raises(self):
        with self.assertRaises(ValueError) as ctx:
            render_template("{{nope}}", {"port": "8080"})
        self.assertIn("nope", str(ctx.exception))

    def test_render_no_placeholder_unchanged(self):
        self.assertEqual(render_template("/opt/app/.env", {}), "/opt/app/.env")

    def test_has_placeholder(self):
        self.assertTrue(has_placeholder("{{base_dir}}/x"))
        self.assertFalse(has_placeholder("/opt/app"))


class TestTemplatedFieldValidation(unittest.TestCase):
    def _assert_rejected(self, data: object, needle: str = "") -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_manifest(data)
        if needle:
            self.assertIn(needle, str(ctx.exception))

    def test_env_file_with_placeholder_accepted(self):
        comp = parse_manifest(
            _manifest(_service(env_file="{{base_dir}}/{{name}}/.env"))
        ).components[0]
        self.assertEqual(comp.env_file, "{{base_dir}}/{{name}}/.env")

    def test_env_file_unknown_placeholder_rejected(self):
        self._assert_rejected(
            _manifest(_service(env_file="{{bogus}}/.env")), "unknown template variable"
        )

    def test_exec_placeholders_accepted(self):
        comp = parse_manifest(
            _manifest(_service(binary=None, exec="{{binary}} --port {{port}}"))
        ).components[0]
        self.assertEqual(comp.exec, "{{binary}} --port {{port}}")

    def test_exec_unknown_placeholder_rejected(self):
        self._assert_rejected(
            _manifest(_service(binary=None, exec="{{wat}}")), "unknown template variable"
        )

    def test_malformed_placeholder_rejected(self):
        self._assert_rejected(
            _manifest(_service(env_file="{{shared_dir}/.env")),
            "malformed template placeholder",
        )

    def test_working_dir_placeholder_accepted(self):
        comp = parse_manifest(
            _manifest(_service(working_dir="{{release_dir}}/server"))
        ).components[0]
        self.assertEqual(comp.working_dir, "{{release_dir}}/server")

    def test_working_dir_unknown_placeholder_rejected(self):
        self._assert_rejected(
            _manifest(_service(working_dir="{{nope}}")), "unknown template variable"
        )

    def test_runtime_env_unknown_placeholder_rejected(self):
        self._assert_rejected(
            _manifest(_service(runtime_env={"APP_VALUE": "{{nope}}"})),
            "unknown template variable",
        )


class TestLoadManifest(unittest.TestCase):
    def test_absent_returns_none(self):
        with tempfile.TemporaryDirectory() as repo:
            self.assertIsNone(load_manifest(repo))

    def test_loads_from_disk(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, MANIFEST_FILENAME), "w", encoding="utf-8") as f:
                json.dump(_manifest(_static(), _service()), f)
            manifest = load_manifest(repo)
            assert manifest is not None
            self.assertEqual(len(manifest.components), 2)

    def test_retired_manifest_stops_deployment_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "infra.json"), "w", encoding="utf-8") as handle:
                json.dump(_manifest(_static(), _service()), handle)
            with self.assertRaisesRegex(ValueError, "Rename infra.json to basaltwater.json"):
                load_manifest(repo)

    def test_canonical_manifest_takes_precedence_over_retired_file(self):
        with tempfile.TemporaryDirectory() as repo:
            for name, data in (("infra.json", _manifest(_service())),
                               (MANIFEST_FILENAME, _manifest(_static()))):
                with open(os.path.join(repo, name), "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
            manifest = load_manifest(repo)
            self.assertEqual([component.name for component in manifest.components], ["site"])

    def test_broken_manifest_links_do_not_trigger_automatic_detection(self):
        for name in (MANIFEST_FILENAME, "infra.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as repo:
                os.symlink("missing.json", os.path.join(repo, name))
                with self.assertRaises(ValueError):
                    load_manifest(repo)

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, MANIFEST_FILENAME), "w", encoding="utf-8") as f:
                f.write("{ not json")
            with self.assertRaises(ValueError) as ctx:
                load_manifest(repo)
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_infers_conventional_go_server(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "go.mod"), "w", encoding="utf-8") as f:
                f.write("module example.com/app\n\ngo 1.25\n")
            os.makedirs(os.path.join(repo, "cmd", "server"))
            with open(os.path.join(repo, "cmd", "server", "main.go"), "w", encoding="utf-8") as f:
                f.write("package main\n\nfunc main() {}\n")

            manifest = infer_manifest(repo)

        assert manifest is not None
        component = manifest.components[0]
        self.assertEqual(component.name, "app")
        self.assertEqual(component.binary, ".basaltwater/bin/app")
        self.assertIn("./cmd/server", component.build[0])
        self.assertIsNone(component.port)
        self.assertEqual(component.runtime_env["HOST"], "127.0.0.1")
        self.assertEqual(component.runtime_env["PORT"], "{{port}}")

    def test_does_not_infer_non_main_go_module(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "go.mod"), "w", encoding="utf-8") as f:
                f.write("module example.com/library\n\ngo 1.25\n")
            self.assertIsNone(infer_manifest(repo))

    def test_bluehexagons_example(self):
        """The shipped bluehexagons basaltwater.json must validate, if present."""
        repo = os.path.join(os.path.dirname(__file__), "..", "..", "bluehexagons")
        manifest_path = os.path.join(repo, MANIFEST_FILENAME)
        if not os.path.exists(manifest_path):
            self.skipTest("bluehexagons repo not checked out alongside basaltwater")
        manifest = load_manifest(repo)
        assert manifest is not None
        names = {c.name for c in manifest.components}
        self.assertEqual(names, {"site"})
        self.assertEqual(manifest.components[0].domain, "{{domain}}")


if __name__ == "__main__":
    unittest.main()
