"""HTTP handler contracts for task follow-up decisions and saves."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from io import BytesIO
from types import ModuleType
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_features import PanelFeatures
from lib.agent_tasks import AgentTasks


class AgentTaskFlowRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = os.path.realpath(temporary.name)
        self.state = panel.WebPanelState({"host": "host.example", "username": "operator",
            "system_type": "agent_vm", "features": {}, "services": [], "access": []}, agent_home=self.home)
        self.addCleanup(self.state.agent_tasks.close)
        self.enterContext(patch.object(AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(self.state, "features", return_value=PanelFeatures(tools=frozenset({"codex", "git"}))))

    def handler(self, path: str, fields: dict[str, str] | list[tuple[str, str]]) -> panel.WebPanelHandler:
        entries = list(fields.items()) if isinstance(fields, dict) else fields
        body = urlencode([("csrf", self.state.csrf_token), *entries]).encode()
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path = self.state, path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        for name in ("_send", "send_response", "send_header", "end_headers"):
            setattr(handler, name, Mock())
        return handler

    def followups_module(self, decide: Mock) -> ModuleType:
        module = ModuleType("lib.agent_task_followups")
        module.decide_follow_up = decide  # type: ignore[attr-defined]
        return module

    def test_follow_up_approval_routes_with_run_provenance(self) -> None:
        for action, decision in (("follow-up-approve", "approve"), ("follow-up-block", "block"),
                                 ("follow-up-privilege", "request-privilege")):
            with self.subTest(action=action):
                decide = Mock()
                handler = self.handler("/actions/agent-task", {
                    "id": "a" * 32, "run_id": "b" * 32, "action": action,
                })
                with patch.dict(sys.modules, {"lib.agent_task_followups": self.followups_module(decide)}), \
                     patch.object(self.state.agent_tasks, "action") as ordinary:
                    handler.do_POST()
                decide.assert_called_once_with(self.state.agent_tasks, "a" * 32, "b" * 32, decision)
                ordinary.assert_not_called()
                handler.send_response.assert_called_once_with(303)
                handler.send_header.assert_any_call("Location", "/agents?run=" + "b" * 32)

    def settings(self, **changes: str) -> dict[str, str]:
        return {"title": "Follow-up", "prompt": "Inspect", "directory": self.home,
                "mode": "workspace", "interval": "once", "submit": "draft", **changes}

    def test_follow_up_form_normalizes_lists_and_forwards_draft_choice(self) -> None:
        decide = Mock()
        module = self.followups_module(decide)
        handler = self.handler("/actions/agent-task/save", self.settings(**{
            "source_task_id": "a" * 32, "source_run_id": "b" * 32,
            "writable_directories": " /srv/data  \n\n /var/lib/app ",
            "operator_requirements": " Check approval \nRestart window ",
            "service_restarts": " app.service \n ", "keep": "1",
        }))
        with patch.dict(sys.modules, {"lib.agent_task_followups": module}):
            handler.do_POST()
        args, kwargs = decide.call_args
        self.assertEqual(args[:4], (self.state.agent_tasks, "a" * 32, "b" * 32, "approve"))
        self.assertEqual(kwargs["settings"]["writable_directories"], ["/srv/data", "/var/lib/app"])
        self.assertEqual(kwargs["settings"]["operator_requirements"], ["Check approval", "Restart window"])
        self.assertEqual(kwargs["settings"]["service_restarts"], ["app.service"])
        self.assertTrue(kwargs["settings"]["keep"])
        self.assertFalse(kwargs["run_now"])
        self.assertTrue(kwargs["draft"])
        handler.send_response.assert_called_once_with(303)
        handler.send_header.assert_any_call("Location", "/agents?run=" + "b" * 32)

    def test_follow_up_run_forwards_reviewed_settings_without_direct_creation(self) -> None:
        decide = Mock()
        handler = self.handler("/actions/agent-task/save", self.settings(
            source_task_id="a" * 32, source_run_id="b" * 32, submit="run",
            custom_model="chosen-model", network="1", service_restarts=" app.service ",
        ))
        with patch.dict(sys.modules, {"lib.agent_task_followups": self.followups_module(decide)}), \
             patch.object(self.state.agent_tasks, "create") as create, \
             patch.object(self.state.agent_tasks, "update") as update:
            handler.do_POST()
        args, kwargs = decide.call_args
        self.assertEqual(args, (self.state.agent_tasks, "a" * 32, "b" * 32, "approve"))
        self.assertEqual(kwargs["settings"]["model"], "chosen-model")
        self.assertEqual(kwargs["settings"]["service_restarts"], ["app.service"])
        self.assertTrue(kwargs["settings"]["network"])
        self.assertFalse(kwargs["settings"]["keep"])
        self.assertEqual((kwargs["run_now"], kwargs["draft"]), (True, False))
        create.assert_not_called()
        update.assert_not_called()
        handler.send_response.assert_called_once_with(303)
        handler.send_header.assert_any_call("Location", "/agents?run=" + "b" * 32)

    def test_partial_follow_up_provenance_rerenders_submitted_form(self) -> None:
        handler = self.handler("/actions/agent-task/save", {
            "source_task_id": "a" * 32, "source_run_id": "", "title": "Retain me",
            "prompt": "Inspect", "directory": self.home, "mode": "workspace",
            "interval": "once", "submit": "draft", "writable_directories": "/srv/data",
            "keep": "1",
        })
        with patch.object(panel, "render_agents", return_value="Correct the form") as render:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0].value, 422)
        submitted = render.call_args.kwargs["submitted"]
        self.assertEqual(submitted["source_task_id"], "a" * 32)
        self.assertEqual(submitted["source_run_id"], "")
        self.assertEqual(submitted["title"], "Retain me")
        self.assertEqual(submitted["writable_directories"], ["/srv/data"])
        self.assertTrue(submitted["keep"])
        self.assertIn("both required", render.call_args.kwargs["error"])

    def test_decision_errors_render_existing_agents_error_response(self) -> None:
        for error, status in ((ValueError("Run no longer available"), 422),
                              (OSError("Storage failed"), 503), (RuntimeError("Unavailable"), 503)):
            with self.subTest(error=type(error).__name__):
                handler = self.handler("/actions/agent-task", {
                    "id": "a" * 32, "run_id": "b" * 32, "action": "follow-up-block",
                })
                decide = Mock(side_effect=error)
                with patch.dict(sys.modules, {"lib.agent_task_followups": self.followups_module(decide)}), \
                     patch.object(panel, "render_agents", return_value="Decision failed") as render:
                    handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], status)
                if status == 422:
                    self.assertIsNone(render.call_args.kwargs["submitted"])
                    self.assertEqual(render.call_args.kwargs["error"], str(error))
                handler.send_response.assert_not_called()

    def test_ordinary_actions_still_use_task_manager(self) -> None:
        handler = self.handler("/actions/agent-task", {"id": "a" * 32, "action": "pause"})
        with patch.object(self.state.agent_tasks, "action") as action:
            handler.do_POST()
        action.assert_called_once_with("a" * 32, "pause")
        handler.send_response.assert_called_once_with(303)
        handler.send_header.assert_any_call("Location", "/agents?view=tasks")

    def test_readiness_and_t3_update_return_to_diagnostics_with_or_without_codex(self) -> None:
        for tools in (frozenset({"codex"}), frozenset()):
            with self.subTest(tools=tools), patch.object(
                self.state, "features", return_value=PanelFeatures(tools=tools, t3=True),
            ), patch.object(self.state.agent_diagnostics, "trigger", return_value=True) as diagnose, \
                 patch.object(self.state, "t3_update_available", return_value=True), \
                 patch.object(self.state, "trigger_t3_update", return_value=True) as update:
                for path, fields in (("/actions/agent-diagnostics", {}),
                                     ("/actions/t3-update", {"return": "agents"})):
                    handler = self.handler(path, fields)
                    handler.do_POST()
                    handler.send_response.assert_called_once_with(303)
                    handler.send_header.assert_any_call("Location", "/agents?view=diagnostics")
                diagnose.assert_called_once_with()
                update.assert_called_once_with()

    def test_busy_readiness_check_does_not_redirect_or_start_a_task(self) -> None:
        handler = self.handler("/actions/agent-diagnostics", {})
        with patch.object(self.state.agent_diagnostics, "trigger", return_value=False), \
             patch.object(self.state.agent_tasks, "create") as create:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 409)
        handler.send_response.assert_not_called()
        create.assert_not_called()

    def test_follow_up_rejects_schedule_edit_and_incomplete_provenance(self) -> None:
        changes = ({"submit": "schedule"}, {"id": "c" * 32, "submit": "save"},
                   {"source_task_id": ""}, {"source_run_id": ""})
        for change in changes:
            with self.subTest(change=change):
                decide = Mock()
                handler = self.handler("/actions/agent-task/save", self.settings(**{
                    "source_task_id": "a" * 32, "source_run_id": "b" * 32, **change,
                }))
                with patch.dict(sys.modules, {"lib.agent_task_followups": self.followups_module(decide)}), \
                     patch.object(panel, "render_agents", return_value="Invalid form"), \
                     patch.object(self.state.agent_tasks, "create") as create, \
                     patch.object(self.state.agent_tasks, "update") as update:
                    handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], 422)
                decide.assert_not_called()
                create.assert_not_called()
                update.assert_not_called()

    def test_follow_up_validation_error_preserves_provenance_and_metadata(self) -> None:
        decide = Mock(side_effect=ValueError("Follow-up is unavailable"))
        handler = self.handler("/actions/agent-task/save", self.settings(
            source_task_id="a" * 32, source_run_id="b" * 32, keep="1",
            operator_requirements=" Review window ", service_restarts=" app.service ",
        ))
        with patch.dict(sys.modules, {"lib.agent_task_followups": self.followups_module(decide)}), \
             patch.object(panel, "render_agents", return_value="Invalid form") as render:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 422)
        submitted = render.call_args.kwargs["submitted"]
        self.assertEqual((submitted["source_task_id"], submitted["source_run_id"]), ("a" * 32, "b" * 32))
        self.assertEqual(submitted["operator_requirements"], ["Review window"])
        self.assertEqual(submitted["service_restarts"], ["app.service"])
        self.assertTrue(submitted["keep"])

    def test_non_follow_up_restarts_rejected_for_every_save_choice(self) -> None:
        for choice in ("run", "schedule", "draft", "save"):
            with self.subTest(choice=choice):
                handler = self.handler("/actions/agent-task/save", self.settings(
                    submit=choice, service_restarts=" app.service \n",
                    id="a" * 32 if choice == "save" else "",
                ))
                with patch.object(panel, "render_agents", return_value="Invalid form"), \
                     patch.object(self.state.agent_tasks, "create") as create, \
                     patch.object(self.state.agent_tasks, "update") as update:
                    handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], 422)
                create.assert_not_called()
                update.assert_not_called()

    def test_ordinary_save_normalizes_lists_and_strips_empty_restart_metadata(self) -> None:
        for editing in (False, True):
            with self.subTest(editing=editing):
                handler = self.handler("/actions/agent-task/save", self.settings(
                    id="a" * 32 if editing else "", submit="save" if editing else "draft",
                    writable_directories=" /srv/data \r\n\n /srv/other ",
                    operator_requirements=" Confirm window \n", service_restarts=" \n", keep="1",
                ))
                with patch.object(self.state.agent_tasks, "create") as create, \
                     patch.object(self.state.agent_tasks, "update") as update:
                    handler.do_POST()
                settings = update.call_args.args[1] if editing else create.call_args.args[0]
                self.assertEqual(settings["writable_directories"], ["/srv/data", "/srv/other"])
                self.assertEqual(settings["operator_requirements"], ["Confirm window"])
                self.assertTrue(settings["keep"])
                self.assertNotIn("service_restarts", settings)
                (create if editing else update).assert_not_called()
                handler.send_response.assert_called_once_with(303)

    def test_new_fields_preserve_csrf_feature_and_duplicate_guards(self) -> None:
        for fields, status in (([("csrf", "wrong")], 403),
                               ([("keep", "1"), ("keep", "1")], 400),
                               ([("writable_directories", "/srv/a"), ("writable_directories", "/srv/b")], 400),
                               ([("run_id", "a" * 32)], 400),
                               ([("keep", "yes")], 422)):
            with self.subTest(fields=fields):
                handler = self.handler("/actions/agent-task/save", [*self.settings().items(), *fields])
                with patch.object(panel, "render_agents", return_value="Invalid form"), \
                     patch.object(self.state.agent_tasks, "create") as create:
                    handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], status)
                create.assert_not_called()
        for path in ("/actions/agent-task", "/actions/agent-task/save"):
            handler = self.handler(path, self.settings())
            with patch.object(self.state, "features", return_value=PanelFeatures()), \
                 patch.object(self.state.agent_tasks, "create") as create:
                handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], 404)
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
