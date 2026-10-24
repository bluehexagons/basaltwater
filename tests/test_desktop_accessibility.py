"""Accessibility behavior without a display, D-Bus, or system mutations."""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop import accessibility as a11y, client, control_state, session_runtime as runtime
from lib.desktop_cli import add_desktop_subparser, run_desktop_command


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.revision = control_state.status()["control_revision"]
        self.api = SimpleNamespace(
            Role=SimpleNamespace(PASSWORD_TEXT="password text"),
            StateType=SimpleNamespace(**{state.upper(): state for state in a11y.STATES}),
            Text=SimpleNamespace(get_text=lambda node, start, end: node.get_text(start, end)),
            Error=RuntimeError,
        )
        self.field = self.node("Editor", "text", text="hello")
        self.app = self.node("Geany", "application", children=[self.field])
        self.app.get_process_id.return_value = 42
        self.api.get_desktop = Mock(return_value=self.node("desktop", children=[self.app]))

    def node(self, name, role="panel", children=(), text=None):
        node = Mock()
        node.app.bus_name = ":1.42"
        node.path = "/accessible/" + name
        node.get_name.return_value = name
        node.get_role_name.return_value = role
        node.get_role.return_value = role
        node.get_state_set.return_value.contains.side_effect = lambda state: state in {
            "enabled", "sensitive", "showing", "editable"}
        node.is_action.return_value = False
        node.is_text.return_value = text is not None
        node.is_editable_text.return_value = True
        node.get_text_iface.return_value.get_character_count.return_value = len(text or "")
        node.get_text_iface.return_value.get_text.side_effect = lambda start, end: text[start:end]
        node.get_child_count.return_value = len(children)
        node.get_child_at_index.side_effect = lambda index: children[index]
        return node

    def inspect(self, **kwargs):
        return a11y.perform({"pid": 42, "control_revision": self.revision, **kwargs}, self.api)

    def test_filtered_inspection_and_unicode_edit(self):
        row = self.inspect(role="text")["elements"][0]
        self.assertEqual(row["text"], "hello")
        result = self.inspect(role="text", operation="set-text", ref=row["ref"], text="café\n日本語")
        self.assertEqual(result["requested"], "set-text")
        self.field.get_editable_text_iface.return_value.set_text_contents.assert_called_once_with("café\n日本語")

    def test_renamed_or_replaced_element_never_receives_action(self):
        row = self.inspect(role="text")["elements"][0]
        self.field.path = "/replacement"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inspect(operation="set-text", ref=row["ref"], text="replacement")
        self.field.get_editable_text_iface.assert_not_called()

    def test_disabled_control_rejected(self):
        row = self.inspect(role="text")["elements"][0]
        self.field.get_state_set.return_value.contains.side_effect = lambda state: state == "showing"
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.inspect(operation="set-text", ref=row["ref"], text="replacement")
        self.field.get_editable_text_iface.assert_not_called()

    def test_ancestor_name_change_invalidates_descendant_reference(self):
        ref = self.inspect(role="text")["elements"][0]["ref"]
        self.app.get_name.return_value = "Another document"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inspect(operation="set-text", ref=ref, text="replacement")
        self.field.get_editable_text_iface.assert_not_called()

    def test_long_names_are_not_exact_prefix_matches_and_full_identity_is_checked(self):
        self.field.get_name.return_value = "x" * 256 + "first"
        row = self.inspect(role="text")["elements"][0]
        self.assertTrue(row["name_truncated"])
        self.assertEqual(self.inspect(name="x" * 256)["elements"], [])
        # The supervisor stores the displayed name, but actions must resolve by ref.
        self.inspect(operation="set-text", ref=row["ref"], name=row["name"], text="updated")
        self.field.get_editable_text_iface.reset_mock()
        self.field.get_name.return_value = "x" * 256 + "second"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inspect(operation="set-text", ref=row["ref"], text="wrong target")
        self.field.get_editable_text_iface.assert_not_called()

    def test_target_state_is_refreshed_immediately_before_mutation(self):
        ref = self.inspect(role="text")["elements"][0]["ref"]
        def disabled():
            self.field.get_state_set.return_value.contains.side_effect = lambda state: state == "showing"
        self.field.clear_cache.side_effect = disabled
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.inspect(operation="set-text", ref=ref, text="must not be written")
        self.field.clear_cache.assert_called_once()
        self.field.get_editable_text_iface.assert_not_called()

    def test_target_rename_during_refresh_rejects_mutation(self):
        ref = self.inspect(role="text")["elements"][0]["ref"]
        self.field.clear_cache.side_effect = lambda: setattr(self.field.get_name, "return_value", "Replacement")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inspect(operation="set-text", ref=ref, text="must not be written")
        self.field.get_editable_text_iface.assert_not_called()

    def test_human_intervention_during_lookup_or_interface_resolution_blocks_mutation(self):
        self.field.is_action.return_value = True
        action = self.field.get_action_iface.return_value
        action.get_n_actions.return_value = 1
        action.get_action_name.return_value = "activate"
        ref = self.inspect(role="text")["elements"][0]["ref"]
        for operation, attribute, method in (
                ("invoke", "get_action_iface", "do_action"),
                ("set-text", "get_editable_text_iface", "set_text_contents"),
                ("focus", "get_component_iface", "grab_focus")):
            for stage in ("lookup", "interface"):
                for handback in (False, True):
                    with self.subTest(operation=operation, stage=stage, handback=handback):
                        self.revision = control_state.change("enabled", human_approved=True)["control_revision"]
                        interface = getattr(self.field, attribute).return_value
                        mutation = getattr(interface, method)
                        mutation.reset_mock()

                        def intervene(*_args):
                            control_state.change("disabled", human_approved=True)
                            if handback:
                                control_state.change("enabled", human_approved=True)
                            return interface

                        def lookup():
                            intervene()
                            return 42
                        target = self.app if stage == "lookup" else self.field
                        name = "get_process_id" if stage == "lookup" else attribute
                        with patch.object(target, name, side_effect=lookup if stage == "lookup" else intervene):
                            expected = ValueError if handback else RuntimeError
                            with self.assertRaises(expected):
                                self.inspect(operation=operation, ref=ref, text="replacement", action_name="activate")
                        mutation.assert_not_called()

    def test_action_lookup_ignores_output_limit_for_unrelated_controls(self):
        ref = self.inspect(role="text")["elements"][0]["ref"]
        with patch.object(a11y, "MAX_RESULTS", 1):
            result = self.inspect(operation="focus", ref=ref)
        self.assertEqual(result["requested"], "focus")

    def test_closing_unrelated_application_does_not_block_lookup(self):
        closing = self.node("closing")
        closing.get_process_id.side_effect = RuntimeError("disconnected")
        self.api.get_desktop.return_value = self.node("desktop", children=[closing, self.app])
        self.assertEqual(self.inspect(role="text")["elements"][0]["text"], "hello")

    def scoped_tree(self):
        other = self.node("Other", children=[self.node("Save", "button") for _ in range(12)])
        save = self.node("Save", "button")
        save.path = "/dialog/save"
        dialog = self.node("Dialog", "dialog", children=[save])
        self.app = self.node("Geany", "application", children=[other, dialog])
        self.app.get_process_id.return_value = 42
        self.api.get_desktop.return_value = self.node("desktop", children=[self.app])
        return other, dialog, save

    def test_scoping_avoids_unrelated_node_budget_and_preserves_references(self):
        other, dialog, save = self.scoped_tree()
        root = self.inspect(role="dialog")["elements"][0]["ref"]
        expected = self.inspect(role="button")["elements"][-1]["ref"]
        other.get_child_at_index.reset_mock()
        with patch.object(a11y, "MAX_NODES", 3):
            result = self.inspect(root=root, name="Save")
            self.assertFalse(result["truncated"])
            self.assertEqual([row["ref"] for row in result["elements"]], [expected])
            self.assertEqual(result["root"], root)
            self.assertEqual(self.inspect(operation="focus", ref=expected)["requested"], "focus")
        other.get_child_at_index.assert_not_called()

    def test_replaced_or_hidden_root_does_not_fall_back_to_whole_application(self):
        other, dialog, save = self.scoped_tree()
        root = self.inspect(role="dialog")["elements"][0]["ref"]
        dialog.path = "/replacement"
        with self.assertRaisesRegex(ValueError, "changed"):
            self.inspect(root=root)
        dialog.path = "/accessible/Dialog"
        dialog.get_state_set.return_value.contains.side_effect = lambda _: False
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.inspect(root=root)

    def test_scope_cannot_traverse_a_password_control(self):
        child = self.node("secret", "text", text="secret")
        self.field.get_child_count.return_value = 1
        self.field.get_child_at_index.side_effect = lambda _: child
        root = self.inspect(name="secret")["elements"][0]["ref"]
        self.field.get_role.return_value = "password text"
        child.get_text_iface.reset_mock()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.inspect(root=root)
        child.get_text_iface.assert_not_called()

    def test_root_hiding_after_resolution_is_not_an_empty_complete_scan(self):
        other, dialog, save = self.scoped_tree()
        root = self.inspect(role="dialog")["elements"][0]["ref"]
        resolve = a11y.resolve_reference
        def hide(*args):
            found = resolve(*args)
            dialog.get_state_set.return_value.contains.side_effect = lambda _: False
            return found
        with patch.object(a11y, "resolve_reference", side_effect=hide):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                self.inspect(root=root)

    def test_invalid_scope_fails_before_application_lookup(self):
        for root in ("ref", [], "-1:" + "a" * 32, "0." * 21 + "0:" + "a" * 32):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.inspect(root=root)
        self.api.get_desktop.assert_not_called()

    def test_hidden_subtrees_are_not_read(self):
        self.field.get_state_set.return_value.contains.side_effect = lambda state: False
        result = self.inspect(role="text")
        self.assertEqual(result["elements"], [])
        self.assertFalse(result["truncated"])
        self.field.get_text_iface.assert_not_called()
        self.field.get_child_count.assert_not_called()

    def test_password_contents_and_descendants_are_not_read(self):
        self.field.get_role.return_value = "password text"
        row = self.inspect()["elements"][1]
        self.assertTrue(row["protected"])
        self.assertEqual(row["name"], "")
        self.assertNotIn("text", row)
        self.field.get_name.assert_not_called()
        self.field.get_text_iface.assert_not_called()
        self.field.get_child_count.assert_not_called()

    def test_partial_scan_and_long_text_are_explicit(self):
        with patch.object(a11y, "MAX_NODES", 1):
            result = self.inspect()
        self.assertTrue(result["truncated"])
        self.field.get_text_iface.return_value.get_character_count.return_value = 10000
        row = self.inspect(role="text")["elements"][0]
        self.assertTrue(row["text_truncated"])
        self.field.get_text_iface.return_value.get_text.assert_called_with(0, 256)

    def test_named_action_must_be_unique_and_report_acceptance(self):
        self.field.is_action.return_value = True
        action = self.field.get_action_iface.return_value
        action.get_n_actions.return_value = 1
        action.get_action_name.return_value = "activate"
        row = self.inspect(role="text")["elements"][0]
        with self.assertRaisesRegex(ValueError, "exact action"):
            self.inspect(operation="invoke", ref=row["ref"], action_name="guess")
        action.do_action.assert_not_called()
        action.do_action.return_value = False
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            self.inspect(operation="invoke", ref=row["ref"], action_name="activate")

    def test_invalid_input_precedes_application_lookup(self):
        for pid in (0, -1, True, "42"):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                a11y.perform({"pid": pid}, self.api)
        self.api.get_desktop.assert_not_called()

    @patch.object(a11y.subprocess, "run", side_effect=subprocess.TimeoutExpired("helper", 8))
    def test_hung_application_is_bounded(self, run):
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            a11y.worker_request({"pid": 42})
        self.assertEqual(run.call_args.kwargs["timeout"], 8)


class DependencyTests(unittest.TestCase):
    @patch.object(a11y.subprocess, "run", return_value=Mock(returncode=0))
    def test_probe_imports_system_bindings_without_querying_desktop(self, run):
        self.assertTrue(a11y.check_dependencies()["available"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["/usr/bin/python3", "-c"])
        self.assertIn("gi.require_version", argv[2])
        self.assertNotIn("get_desktop", argv[2])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch.object(a11y.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 3))
    def test_failed_probe_reports_missing_dependencies(self, run):
        result = a11y.check_dependencies()
        self.assertFalse(result["available"])
        self.assertIn("gir1.2-atspi-2.0", result["error"])

    @patch.object(client, "check_dependencies", return_value={"available": False, "error": "AT-SPI unavailable"})
    @patch.object(client.importlib.util, "find_spec", return_value=True)
    @patch.object(client.shutil, "which", return_value="/usr/bin/tool")
    @patch.object(client.subprocess, "run", return_value=Mock(stdout="active"))
    @patch.object(runtime, "runtime_directory", return_value="/private/runtime")
    @patch.object(runtime, "status", return_value={"state": "stopped"})
    def test_doctor_reports_unhealthy_when_only_accessibility_is_missing(self, status, directory, run, which, spec, check):
        result = client.doctor()
        self.assertFalse(result["healthy"])
        self.assertEqual(result["suggestions"], ["AT-SPI unavailable"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        process = Mock(pid=100)
        process.poll.return_value = None
        self.session = runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, process)
        self.row = {"ref": "ref", "name": "Editor", "role": "text", "states": ["enabled"]}
        self.result = {"pid": 42, "elements": [self.row], "truncated": False}
        patcher = patch.object(a11y, "worker_request", return_value=self.result)
        self.worker = patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self):
        return self.session.handle({"action": "inspect", "generation": self.session.generation, "pid": 42})

    def test_observation_during_override_and_actions_require_current_revision(self):
        control_state.change("human-override", human_approved=True)
        self.assertEqual(self.inspect()["elements"], [self.row])
        payload = {"action": "element", "generation": self.session.generation, "ref": "ref", "operation": "focus"}
        with self.assertRaisesRegex(RuntimeError, "human-override"):
            self.session.handle(payload)
        control_state.change("enabled", human_approved=True)
        with self.assertRaisesRegex(ValueError, "control changed"):
            self.session.handle(payload)
        self.assertEqual(self.worker.call_count, 1)

    def test_reference_is_bound_to_observation_and_consumed_even_on_failure(self):
        self.inspect()
        context = {"generation": self.session.generation, "control_revision": control_state.status()["control_revision"]}
        self.worker.side_effect = RuntimeError("Application rejected")
        payload = {"action": "element", **context, "ref": "ref", "operation": "focus", "pid": 999}
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            self.session.handle(payload)
        self.assertEqual(self.worker.call_args.args[0]["pid"], 42)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.session.handle(payload)

    def test_expired_reference_and_stale_generation_fail_before_worker(self):
        self.inspect()
        context = {"generation": self.session.generation, "control_revision": control_state.status()["control_revision"]}
        self.session.elements["ref"]["observed_at"] -= 61
        with self.assertRaisesRegex(ValueError, "expired"):
            self.session.handle({"action": "element", **context, "ref": "ref", "operation": "focus"})
        with self.assertRaisesRegex(ValueError, "session changed"):
            self.session.handle({"action": "inspect", "generation": "old", "pid": 42})
        self.assertEqual(self.worker.call_count, 1)


class WaitTests(unittest.TestCase):
    @patch.object(runtime, "request", return_value={"generation": "g", "elements": [], "truncated": False})
    def test_scoped_wait_keeps_root_on_each_observation(self, request):
        root = "0:" + "a" * 32
        client.wait_for_element("g", pid=42, root=root, name="Save", state="absent")
        self.assertEqual(request.call_args.args[0]["root"], root)

    @patch.object(runtime, "request", side_effect=RuntimeError("Element or root changed"))
    def test_missing_scope_is_not_successful_absence(self, request):
        with self.assertRaisesRegex(RuntimeError, "root changed"):
            client.wait_for_element("g", pid=42, root="0:" + "a" * 32, name="Save", state="absent")

    @patch.object(runtime, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"})
    @patch.object(runtime, "request", return_value={"elements": [], "truncated": False})
    def test_cli_requires_explicit_generation_for_scoped_inspection(self, request, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        argv = ["desktop", "inspect", "--pid", "42", "--root", "0:" + "a" * 32]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(parser.parse_args(argv)), 1)
        request.assert_not_called()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(parser.parse_args([*argv, "--generation", "g"])), 0)
        self.assertEqual(request.call_args.args[0]["root"], argv[-1])

    @patch.object(runtime, "request", return_value={"generation": "g", "elements": [
        {"ref": "editor", "states": ["focused"]}, {"ref": "search", "states": []}], "truncated": False})
    def test_state_disambiguates_editor_from_other_text_controls(self, request):
        result = client.wait_for_element("g", pid=42, role="text", state="focused")
        self.assertEqual([row["ref"] for row in result["elements"]], ["editor"])

    @patch.object(client.time, "sleep")
    @patch.object(runtime, "request", side_effect=[
        {"generation": "g", "elements": [], "truncated": True},
        {"generation": "g", "elements": [], "truncated": False}])
    def test_absence_requires_complete_scan_without_lease(self, request, sleep):
        result = client.wait_for_element("g", pid=42, name="Save", state="absent")
        self.assertNotIn("error", result)
        self.assertEqual([call.args[0]["action"] for call in request.call_args_list], ["inspect", "inspect"])

    @patch.object(client.time, "monotonic", side_effect=[0, 2])
    @patch.object(runtime, "request", return_value={"generation": "g", "elements": [{}, {}], "truncated": False})
    def test_ambiguous_timeout_preserves_observations(self, request, clock):
        result = client.wait_for_element("g", pid=42, name="Save", timeout=1)
        self.assertIn("Timed out", result["error"])
        self.assertEqual(len(result["elements"]), 2)

    @patch.object(client.time, "monotonic", side_effect=[0, 2])
    @patch.object(runtime, "request", return_value={"generation": "g", "elements": [
        {"states": [], "text": "prefix", "text_truncated": True}], "truncated": False})
    def test_text_prefix_is_not_complete_text(self, request, clock):
        result = client.wait_for_element("g", pid=42, role="text", text="prefix", timeout=1)
        self.assertIn("error", result)

    @patch.object(runtime, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"})
    @patch.object(runtime, "request", return_value={"requested": "invoke"})
    def test_cli_routes_named_action_with_current_revision(self, request, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "element", "invoke", "--generation", "g", "--ref", "ref", "--action-name", "click"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(args), 0)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0]["action_name"], "click")
        self.assertEqual(request.call_args.args[0]["control_revision"], "r")
