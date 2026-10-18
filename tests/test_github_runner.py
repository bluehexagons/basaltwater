"""Runner identity, credential transport and dedicated service boundaries."""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import github_runner_gate as gate
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.github_runner_config import ACCOUNT, PROFILE, SERVICE, VERSION, PAYLOAD_NAME
from lib.setup_payloads import PAYLOAD_NAMES, receive_payloads
from lib.system_types import get_steps_for_system_type
from web import github_runner_steps as steps


def policy() -> dict:
    return dict(schema=1, repository="owner/repo", allowed_users=["alice"], labels=["debian-ci"], runner_version=VERSION)


def delivery(name: str) -> tuple[dict, dict]:
    repo = {"full_name": "owner/repo", "private": True}
    event = {"repository": repo, "sender": {"login": "alice", "type": "User"}, "deleted": False}
    if name == "pull_request":
        event.update(action="opened", pull_request={"user": {"login": "alice", "type": "User"},
                                                    "head": {"repo": repo}, "base": {"repo": repo}})
    env = dict(GITHUB_EVENT_NAME=name, GITHUB_REPOSITORY="owner/repo", GITHUB_SERVER_URL="https://github.com",
               GITHUB_ACTOR="alice", GITHUB_TRIGGERING_ACTOR="alice", GITHUB_RUN_ATTEMPT="1", GITHUB_RUN_ID="123")
    return event, env


def config(**overrides) -> SetupConfig:
    return SetupConfig(**(dict(host="localhost", username="operator", system_type=PROFILE,
                               machine_type="vm", ci_repository="owner/repo", ci_allowed_users=["alice"],
                               ci_labels=["debian-ci"]) | overrides))


class IdentityTests(unittest.TestCase):
    def test_supported_triggers_allow_deny_missing_and_malformed(self) -> None:
        for name in ("push", "workflow_dispatch", "pull_request"):
            for identity in ("alice", "mallory", None, "alice\n"):
                with self.subTest(event=name, identity=identity):
                    event, env = delivery(name)
                    event["sender"]["login"] = identity
                    env["GITHUB_ACTOR"] = identity
                    env["GITHUB_TRIGGERING_ACTOR"] = identity
                    self.assertEqual(gate.evaluate_job(policy(), event, env)[0], identity == "alice")
            event, env = delivery(name)
            del event["sender"]
            self.assertFalse(gate.evaluate_job(policy(), event, env)[0])
            for key in ("GITHUB_ACTOR", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL"):
                missing = dict(env)
                missing.pop(key)
                self.assertFalse(gate.evaluate_job(policy(), event, missing)[0])

    def test_pr_author_and_sender_must_both_be_allowed_and_forks_are_denied(self) -> None:
        for author in ("mallory", None, "alice\n", 42):
            event, env = delivery("pull_request")
            event["pull_request"]["user"]["login"] = author
            self.assertFalse(gate.evaluate_job(policy(), event, env)[0])
        for side in ("head", "base"):
            event, env = delivery("pull_request")
            event = copy.deepcopy(event)
            event["pull_request"][side]["repo"]["full_name"] = "fork/repo"
            self.assertFalse(gate.evaluate_job(policy(), event, env)[0])

    def test_public_repositories_reruns_bots_unknown_events_and_spoofing_denied(self) -> None:
        for name in ("push", "workflow_dispatch", "pull_request"):
            for kind in ("public", "rerun", "bot", "sender", "triggering", "server", "repository"):
                event, env = delivery(name)
                if kind == "public":
                    event["repository"]["private"] = False
                elif kind == "rerun":
                    env["GITHUB_RUN_ATTEMPT"] = "2"
                elif kind == "bot":
                    event["sender"]["type"] = "Bot"
                elif kind == "sender":
                    event["sender"]["login"] = "mallory"
                elif kind == "triggering":
                    env["GITHUB_TRIGGERING_ACTOR"] = "mallory"
                elif kind == "server":
                    env["GITHUB_SERVER_URL"] = "https://example.com"
                else:
                    env["GITHUB_REPOSITORY"] = "other/repo"
                with self.subTest(event=name, kind=kind):
                    self.assertFalse(gate.evaluate_job(policy(), event, env)[0])
        for name in ("pull_request_target", "schedule", "workflow_run", "repository_dispatch", ""):
            event, env = delivery(name)
            self.assertFalse(gate.evaluate_job(policy(), event, env)[0])

    def test_malformed_payloads_fail_closed_without_using_commit_author(self) -> None:
        for name in ("push", "workflow_dispatch", "pull_request"):
            event, env = delivery(name)
            for malformed in (None, [], "alice", {"repository": 3}, {"repository": {"private": True, "full_name": 3}}):
                self.assertFalse(gate.evaluate_job(policy(), malformed, env)[0])
        event, env = delivery("push")
        event["sender"]["login"] = "mallory"
        event["head_commit"] = {"author": {"name": "alice", "username": "alice"}}
        self.assertFalse(gate.evaluate_job(policy(), event, env)[0])

    def test_hook_kills_worker_before_even_always_canary_on_every_failure(self) -> None:
        # Real, disposable child processes qualify the shell's parent boundary;
        # no runner, account, service, network or host setup is invoked.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake_gate = directory / "gate.py"
            hook = directory / "hook.sh"
            canary = directory / "workflow-step"
            worker = directory / "worker.py"
            worker.write_text("import subprocess,sys,pathlib\nsubprocess.run(['/bin/bash', sys.argv[1]])\npathlib.Path(sys.argv[2]).touch()\n")
            with patch.object(steps, "GATE", str(fake_gate)):
                hook.write_text(steps.render_hook())
            for program, denied in (("raise SystemExit(1)", True), ("raise RuntimeError('broken')", True),
                                    (None, True), ("raise SystemExit(0)", False)):
                if program is None:
                    fake_gate.unlink()
                else:
                    fake_gate.write_text(program)
                canary.unlink(missing_ok=True)
                process = subprocess.run([sys.executable, str(worker), str(hook), str(canary)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                self.assertEqual(canary.exists(), not denied)
                self.assertEqual(process.returncode, -signal.SIGKILL if denied else 0)

    def test_gate_errors_and_telemetry_failure_deny_without_payload_output(self) -> None:
        event, env = delivery("workflow_dispatch")
        with (patch.object(gate, "check_installation", return_value=policy()),
              patch.dict(os.environ, env | {"GITHUB_EVENT_PATH": "/event"}, clear=True),
              patch.object(gate, "read_json_file", return_value=event),
              patch.object(gate, "record_outcome", side_effect=OSError("private details")),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            self.assertEqual(gate.main(), 1)
            self.assertNotIn("private details", output.getvalue())

    def test_outcomes_are_bounded_and_do_not_contain_event_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(gate, "OUTCOMES", temporary + "/outcomes.json"):
            for _ in range(102):
                gate.record_outcome(False, "actor-denied", "mallory", {"GITHUB_EVENT_NAME": "push", "secret": "never-store"})
            value = json.loads(Path(temporary, "outcomes.json").read_text())
            self.assertEqual(len(value), 100)
            self.assertNotIn("never-store", json.dumps(value))


class ConfigurationTests(unittest.TestCase):
    def test_public_values_round_trip_but_one_shot_token_does_not(self) -> None:
        original = config(ci_registration_token_file="/private/token", ci_registration_payload=True)
        self.assertNotIn("/private/token", repr(original))
        saved = original.to_dict()
        self.assertNotIn("ci_registration_token_file", saved)
        self.assertNotIn("ci_registration_payload", saved)
        restored = SetupConfig.from_dict("localhost", PROFILE, saved)
        self.assertEqual(restored.ci_allowed_users, ["alice"])
        self.assertIsNone(restored.ci_registration_token_file)
        tokens = shlex.split(" ".join(original.to_remote_args()))
        self.assertIn("--ci-registration-payload", tokens)
        self.assertNotIn("/private/token", tokens)
        parser = create_setup_argument_parser("Remote test", for_remote=True)
        args = parser.parse_args(tokens)
        args.host = "localhost"
        remote = SetupConfig.from_args(args, PROFILE)
        self.assertEqual(remote.ci_repository, original.ci_repository)
        self.assertEqual(remote.ci_labels, original.ci_labels)
        self.assertTrue(remote.ci_registration_payload)
        self.assertNotIn("registration", " ".join(original.to_setup_command()))

    def test_invalid_policy_and_profile_inputs_rejected(self) -> None:
        for fields in ({"ci_repository": "https://github.com/owner/repo"}, {"ci_repository": "owner/.."},
                       {"ci_allowed_users": []}, {"ci_allowed_users": ["alice\n"]},
                       {"ci_labels": ["bad\nlabel"]}, {"machine_type": "wsl"},
                       {"username": ACCOUNT}, {"system_type": "server_lite"},
                       {"enable_cicd": True}, {"agent_tools": ["codex"]}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                config(**fields)

    def test_profile_avoids_docker_and_unrelated_services_and_can_include_panel(self) -> None:
        functions = [function.__name__ for _, function in get_steps_for_system_type(config(web_panel_port=8080))]
        self.assertEqual(functions, ["check_runner_platform", "setup_user", "setup_github_runner", "configure_web_panel"])
        unit = steps.render_unit()
        for required in (f"User={ACCOUNT}", "NoNewPrivileges=yes", "ProtectSystem=strict", "WantedBy=multi-user.target"):
            self.assertIn(required, unit)
        self.assertNotIn("docker", unit.lower())

    def test_platform_and_dry_run_do_not_mutate(self) -> None:
        with (patch.object(steps, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}),
              patch.object(steps.platform, "machine", return_value="x86_64"),
              patch.object(steps, "is_dry_run", return_value=True), patch.object(steps, "run") as run,
              patch.object(steps, "_download_release") as download):
            steps.setup_github_runner(config())
            run.assert_not_called()
            download.assert_not_called()
        with patch.object(steps, "read_os_release", return_value={"ID": "ubuntu", "VERSION_ID": "24.04"}):
            with self.assertRaises(ValueError):
                steps.check_runner_platform(config())

    def test_private_token_staging_rejects_links_permissions_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "source")
            token = b"A" * 40
            source.write_bytes(token)
            source.chmod(0o600)
            target = Path(temporary, PAYLOAD_NAME)
            steps.stage_registration_token(str(source), str(target))
            self.assertEqual((target / "token").read_bytes(), token)
            self.assertEqual((target / "token").stat().st_mode & 0o777, 0o600)
            source.chmod(0o644)
            with self.assertRaises(ValueError):
                steps._private_token(str(source))
            source.chmod(0o600)
            source.write_bytes(b"A" * 5000)
            with self.assertRaises(ValueError):
                steps._private_token(str(source))
            link = Path(temporary, "link")
            link.symlink_to(source)
            with self.assertRaises(OSError):
                steps._private_token(str(link))

    def test_private_payload_receiver_supports_runner_token(self) -> None:
        self.assertIn(PAYLOAD_NAME, PAYLOAD_NAMES)
        with tempfile.TemporaryDirectory() as temporary:
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                member = tarfile.TarInfo(PAYLOAD_NAME + "/token")
                member.size = 40
                archive.addfile(member, io.BytesIO(b"A" * 40))
            data.seek(0)
            receive_payloads(data, temporary)
            target = Path(temporary, PAYLOAD_NAME, "token")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_archive_rejects_symlinks_and_traversal(self) -> None:
        for name, kind in (("../escape", tarfile.REGTYPE), ("linked", tarfile.SYMTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary, "runner.tar.gz")
                with tarfile.open(archive, "w:gz") as output:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                    output.addfile(member)
                with self.assertRaises(ValueError):
                    steps._extract_release(archive, Path(temporary, "app"))

    def test_archive_preserves_safe_node_links_after_extracting_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary, "runner.tar.gz")
            with tarfile.open(archive, "w:gz") as output:
                member = tarfile.TarInfo(".")
                member.type = tarfile.DIRTYPE
                output.addfile(member)
                link = tarfile.TarInfo("bin/npm")
                link.type = tarfile.SYMTYPE
                link.linkname = "../lib/npm.js"
                output.addfile(link)
                script = tarfile.TarInfo("lib/npm.js")
                script.size = 4
                output.addfile(script, io.BytesIO(b"code"))
                directory = tarfile.TarInfo("bin")
                directory.type = tarfile.DIRTYPE
                output.addfile(directory)
            destination = Path(temporary, "app")
            steps._extract_release(archive, destination)
            self.assertTrue((destination / "bin/npm").is_symlink())
            self.assertEqual((destination / "bin/npm").read_bytes(), b"code")

    def test_registration_uses_environment_and_preserves_only_generated_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary, "app")
            application.mkdir()
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            token = b"A" * 40

            def register(_command, **kwargs):
                self.assertNotIn(token.decode(), shlex.join(_command))
                self.assertEqual(kwargs["env"]["ACTIONS_RUNNER_INPUT_TOKEN"], token.decode())
                self.assertIn("--disableupdate", _command)
                for name in (".runner", ".credentials", ".credentials_rsaparams"):
                    Path(kwargs["cwd"], name).write_bytes(b"generated")
                return subprocess.CompletedProcess(_command, 0)

            with (patch.object(steps, "DATA", temporary), patch.object(steps, "_extract_release"),
                  patch.object(steps.os, "chown"), patch.object(steps, "write_bytes_atomic") as write,
                  patch.object(steps, "run", side_effect=register)):
                steps._register(Path(temporary, "release"), application, policy(), token, account)
            self.assertEqual(write.call_count, 3)
            self.assertTrue(all(call.kwargs["mode"] == 0o640 for call in write.call_args_list))
            self.assertTrue(all(call.kwargs["uid"] == 0 for call in write.call_args_list))

    def test_registration_failure_suppresses_vendor_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (patch.object(steps, "DATA", temporary), patch.object(steps, "_extract_release"),
                  patch.object(steps.os, "chown"),
                  patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 1, stderr="sensitive token"))):
                with self.assertRaisesRegex(RuntimeError, "fresh repository registration token") as error:
                    steps._register(Path("unused"), Path(temporary), policy(), b"A" * 40,
                                    SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
                self.assertNotIn("sensitive", str(error.exception))

    def test_allowlist_reconfigure_stops_listener_and_needs_no_token(self) -> None:
        with (patch.object(steps, "check_runner_platform"), patch.object(steps, "is_dry_run", return_value=False),
              patch.object(steps, "check_directory_path"), patch.object(steps, "read_json_file", return_value=policy()),
              patch.object(steps, "_account", return_value=SimpleNamespace(pw_uid=123, pw_gid=123)),
              patch.object(steps, "_directory"), patch.object(steps, "run") as run,
              patch.object(steps, "write_text_atomic"), patch.object(steps, "write_json_atomic") as write,
              patch.object(steps, "replace_units") as units, patch.object(steps, "_register") as register):
            steps.setup_github_runner(config(ci_allowed_users=["bob"]))
            register.assert_not_called()
            self.assertIn(unittest.mock.call(["systemctl", "stop", SERVICE], timeout=120), run.call_args_list)
            self.assertEqual(write.call_args.args[1]["allowed_users"], ["bob"])
            units.assert_called_once()

    def test_missing_registration_and_replacement_fail_before_any_mutation(self) -> None:
        for previous in (None, policy() | {"repository": "other/repo"}):
            with (patch.object(steps, "check_runner_platform"), patch.object(steps, "is_dry_run", return_value=False),
                  patch.object(steps, "check_directory_path"),
                  patch.object(steps, "read_json_file", side_effect=FileNotFoundError() if previous is None else None,
                               return_value=previous), patch.object(steps.os.path, "lexists", return_value=False),
                  patch.object(steps, "run") as run):
                with self.assertRaises(ValueError):
                    steps.setup_github_runner(config())
                run.assert_not_called()
