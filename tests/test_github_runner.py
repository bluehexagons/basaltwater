"""Runner identity, credential transport and dedicated service boundaries."""

from __future__ import annotations

import copy
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import shlex
import signal
import stat
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
from lib.github_runner_config import ACCOUNT, PROFILE, SERVICE, VERSION, PAYLOAD_NAME, validate_policy
from lib.setup_payloads import PAYLOAD_NAMES, receive_payloads
from lib.system_types import get_steps_for_system_type
from plugins.post_setup import build_post_setup_steps
from web import github_runner_steps as steps


def policy() -> dict:
    return dict(schema=1, repository="owner/repo", allowed_users=["alice"], labels=["debian-ci"], runner_version=VERSION,
                registration="registered")


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
    def test_organization_accepts_multiple_private_repositories_but_not_other_owners_or_cross_repo_prs(self) -> None:
        configured = policy() | dict(repository="", organization="owner", runner_group="CI")
        for name in ("push", "workflow_dispatch", "pull_request"):
            for repository in ("owner/repo", "OWNER/second", "other/repo", "owner-lookalike/repo", "owner/../repo"):
                event, env = delivery(name)
                event["repository"].update(full_name=repository, owner=dict(login=repository.split("/")[0], type="Organization"))
                env["GITHUB_REPOSITORY"] = repository
                with self.subTest(event=name, repository=repository):
                    self.assertEqual(gate.evaluate_job(configured, event, env)[0], repository in {"owner/repo", "OWNER/second"})
            event, env = delivery(name)
            event["repository"]["owner"] = dict(login="owner", type="User")
            self.assertFalse(gate.evaluate_job(configured, event, env)[0])
        event, env = delivery("pull_request")
        event = copy.deepcopy(event)
        event["repository"]["owner"] = dict(login="owner", type="Organization")
        event["pull_request"]["head"]["repo"]["full_name"] = "owner/another"
        self.assertFalse(gate.evaluate_job(configured, event, env)[0])

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
                event, env = delivery(name)
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

    def test_supported_pr_actions_deleted_pushes_and_case_insensitive_identities(self) -> None:
        for action in ("opened", "reopened", "synchronize", "closed", None):
            event, env = delivery("pull_request")
            event["action"] = action
            self.assertEqual(gate.evaluate_job(policy(), event, env)[0], action in {"opened", "reopened", "synchronize"})
        for deleted in (True, None, "false"):
            event, env = delivery("push")
            event["deleted"] = deleted
            self.assertFalse(gate.evaluate_job(policy(), event, env)[0])
        event, env = delivery("workflow_dispatch")
        event["sender"]["login"] = "Alice"
        event["repository"]["full_name"] = "OWNER/REPO"
        env["GITHUB_ACTOR"] = "ALICE"
        self.assertTrue(gate.evaluate_job(policy(), event, env)[0])

    def test_preflight_rejects_untrusted_unreadable_and_special_installation_files(self) -> None:
        executables = {gate.APP + "/bin/Runner.Listener", gate.APP + "/bin/Runner.Worker"}
        files = {gate.POLICY, gate.HOOK, gate.GATE, *(gate.APP + "/" + name for name in
                 (".env", ".path", ".runner", ".credentials", ".credentials_rsaparams")), *executables}

        def metadata(path):
            mode = (stat.S_IFREG | (0o755 if path in executables else 0o644)) if path in files else stat.S_IFDIR | 0o755
            return SimpleNamespace(st_uid=0, st_mode=mode)

        for failure in ("none", "owner", "writable", "symlink", "fifo", "directory", "unreadable", "not-executable"):
            def inspect(path):
                info = metadata(path)
                if path == gate.HOOK:
                    if failure == "owner":
                        info.st_uid = 123
                    if failure == "writable":
                        info.st_mode |= 0o020
                    if failure in {"symlink", "fifo", "directory"}:
                        info.st_mode = {"symlink": stat.S_IFLNK, "fifo": stat.S_IFIFO, "directory": stat.S_IFDIR}[failure] | 0o755
                return info

            def accessible(path, access):
                self.assertTrue(access & os.R_OK)
                if path in executables:
                    self.assertTrue(access & os.X_OK)
                return not ((failure == "unreadable" and path == gate.HOOK)
                            or (failure == "not-executable" and path in executables))

            with (self.subTest(failure=failure), patch.object(gate.os, "lstat", side_effect=inspect),
                  patch.object(gate.os, "access", side_effect=accessible),
                  patch.object(gate, "read_json_file", return_value=policy())):
                if failure == "none":
                    self.assertEqual(gate.check_installation(), policy())
                else:
                    with self.assertRaises(ValueError):
                        gate.check_installation()

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
    def test_organization_scope_round_trips_and_rejects_ambiguous_or_invalid_scope(self) -> None:
        original = config(ci_repository="", ci_organization="owner", ci_runner_group="Private CI")
        parser = create_setup_argument_parser("Remote test", for_remote=True)
        args = parser.parse_args(shlex.split(" ".join(original.to_remote_args())))
        args.host = "localhost"
        remote = SetupConfig.from_args(args, PROFILE)
        saved = SetupConfig.from_dict("localhost", PROFILE, original.to_dict())
        for restored in (remote, saved):
            self.assertEqual(restored.ci_organization, "owner")
            self.assertEqual(restored.ci_runner_group, "Private CI")
        self.assertIn("--ci-organization owner", original.to_setup_command())
        for fields in (dict(ci_repository=""), dict(ci_organization="owner"), dict(ci_runner_group="CI"),
                       dict(ci_repository="", ci_organization="bad/org"),
                       dict(ci_repository="", ci_organization="owner", ci_runner_group="bad\ngroup")):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                config(**fields)
        legacy = policy()
        del legacy["registration"]
        self.assertEqual(validate_policy(legacy), policy())

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

    def test_dedicated_profile_rejects_unrelated_post_setup_work(self) -> None:
        for fields in ({"deploy_specs": [["example.test", "https://example.test/repo.git"]]},
                       {"enable_cloudflare": True}, {"enable_samba": True}, {"smb_mounts": [["fixture"]]},
                       {"sync_specs": [["fixture"]]}, {"backup_specs": [["fixture"]]}, {"scrub_specs": [["fixture"]]},
                       {"gogs": ["example.test"]}, {"antistatic_server": "example.test"},
                       {"disable_privilege_broker": True}, {"maintenance_agent": True},
                       {"maintenance_agent": False}, {"maintenance_agent_interval": 60},
                       {"custom_steps": "reconcile_samba_shares"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                config(**fields)

    def test_profile_avoids_docker_and_unrelated_services_and_can_include_panel(self) -> None:
        runner = config(web_panel_port=8080, enable_ssl=True, ssl_email="operator@example.test")
        functions = [function.__name__ for _, function in
                     get_steps_for_system_type(runner) + build_post_setup_steps(runner, lambda *_: ("", ""))]
        self.assertEqual(functions, ["check_runner_platform", "setup_user", "setup_github_runner", "configure_web_panel"])
        other = SetupConfig(host="localhost", username="operator", system_type="server_lite", web_panel_port=8080)
        self.assertIn("configure_maintenance_agent", [function.__name__ for _, function in
                      build_post_setup_steps(other, lambda *_: ("", ""))])
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
            factory = tempfile.TemporaryDirectory

            def register(_command, **kwargs):
                self.assertNotIn(token.decode(), shlex.join(_command))
                self.assertEqual(kwargs["env"]["ACTIONS_RUNNER_INPUT_TOKEN"], token.decode())
                self.assertIn("--disableupdate", _command)
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                for name in (".runner", ".credentials", ".credentials_rsaparams"):
                    Path(kwargs["cwd"], name).write_bytes(b"generated")
                return subprocess.CompletedProcess(_command, 0)

            with (patch.object(steps, "DATA", temporary), patch.object(steps.shutil, "copytree"),
                  patch.object(steps.tempfile, "TemporaryDirectory", wraps=tempfile.TemporaryDirectory) as temp,
                  patch.object(steps.os, "chown"), patch.object(steps, "write_bytes_atomic") as write,
                  patch.object(steps, "run", side_effect=register)):
                # Keep volatile diagnostic fixtures inside our test directory.
                temp.side_effect = lambda **kwargs: factory(**(kwargs | {"dir": temporary}))
                steps._register(application, policy(), token, account)
            self.assertEqual(write.call_count, 3)
            self.assertTrue(all(call.kwargs["mode"] == 0o640 for call in write.call_args_list))
            self.assertTrue(all(call.kwargs["uid"] == 0 for call in write.call_args_list))

    def test_organization_registration_sets_group_and_validates_all_credentials_before_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary, "app")
            application.mkdir()
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            configured = policy() | dict(repository="", organization="owner", runner_group="Private CI")
            factory = tempfile.TemporaryDirectory

            def register(command, **kwargs):
                self.assertEqual(command[command.index("--url") + 1], "https://github.com/owner")
                self.assertEqual(command[command.index("--runnergroup") + 1], "Private CI")
                self.assertNotIn("--replace", command)
                self.assertTrue(Path(kwargs["cwd"], "_diag").is_symlink())
                for name in (".runner", ".credentials", ".credentials_rsaparams"):
                    Path(kwargs["cwd"], name).write_bytes(b"generated" if name != ".credentials_rsaparams" else b"")
                return subprocess.CompletedProcess(command, 0)

            with (patch.object(steps, "DATA", temporary), patch.object(steps.shutil, "copytree"),
                  patch.object(steps.tempfile, "TemporaryDirectory", side_effect=lambda **kwargs: factory(**(kwargs | {"dir": temporary}))),
                  patch.object(steps.os, "chown"), patch.object(steps, "write_bytes_atomic") as write,
                  patch.object(steps, "run", side_effect=register)):
                with self.assertRaisesRegex(ValueError, "credential file"):
                    steps._register(application, configured, b"A" * 40, account)
            write.assert_not_called()
            self.assertEqual(list(Path(temporary).iterdir()), [application])

    def test_registration_failure_suppresses_vendor_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            factory = tempfile.TemporaryDirectory
            with (patch.object(steps, "DATA", temporary), patch.object(steps.shutil, "copytree"),
                  patch.object(steps.tempfile, "TemporaryDirectory", side_effect=lambda **kwargs: factory(**(kwargs | {"dir": temporary}))),
                  patch.object(steps.os, "chown"),
                  patch.object(steps, "run", return_value=subprocess.CompletedProcess([], 1, stderr="sensitive token"))):
                with self.assertRaisesRegex(RuntimeError, "fresh registration token") as error:
                    steps._register(Path(temporary), policy(), b"A" * 40,
                                    SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
                self.assertNotIn("sensitive", str(error.exception))

    def test_allowlist_reconfigure_stops_listener_and_needs_no_token(self) -> None:
        with (patch.object(steps, "check_runner_platform"), patch.object(steps, "is_dry_run", return_value=False),
              patch.object(steps, "runner_lock", side_effect=nullcontext), patch.object(gate, "check_installation"),
              patch.object(steps, "check_directory_path"), patch.object(steps, "read_json_file", return_value=policy()),
              patch.object(steps, "_account", return_value=SimpleNamespace(pw_uid=123, pw_gid=123)),
              patch.object(steps, "_directory"), patch.object(steps, "run") as run,
              patch.object(steps, "write_text_atomic"), patch.object(steps, "write_json_atomic") as write,
              patch.object(steps, "replace_units") as units, patch.object(steps, "_register") as register):
            steps.setup_github_runner(config(ci_repository="OWNER/REPO", ci_allowed_users=["bob"]))
            register.assert_not_called()
            self.assertIn(unittest.mock.call(["systemctl", "stop", SERVICE], timeout=120), run.call_args_list)
            self.assertEqual(write.call_args.args[1]["allowed_users"], ["bob"])
            units.assert_called_once()

    def test_scope_group_and_label_replacement_fail_before_host_commands(self) -> None:
        for previous in (policy() | {"repository": "other/repo"}, policy() | {"labels": ["other"]},
                         policy() | dict(repository="", organization="owner", runner_group="CI")):
            with (patch.object(steps, "check_runner_platform"), patch.object(steps, "is_dry_run", return_value=False),
                  patch.object(steps, "runner_lock", side_effect=nullcontext),
                  patch.object(steps, "check_directory_path"),
                  patch.object(steps, "read_json_file", return_value=previous),
                  patch.object(steps, "run") as run):
                with self.assertRaises(ValueError):
                    steps.setup_github_runner(config())
                run.assert_not_called()

    def test_account_rejects_root_or_shared_primary_group_and_extra_groups(self) -> None:
        for uid, gid, group, groups in ((0, 123, ACCOUNT, [123]), (123, 0, "root", [0]),
                                       (123, 123, "shared", [123]), (123, 123, ACCOUNT, [123, 456]),
                                       (123, 123, ACCOUNT, [123])):
            account = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_dir=steps.HOME, pw_shell="/usr/sbin/nologin")
            with (self.subTest(uid=uid, gid=gid, group=group, groups=groups),
                  patch.object(steps.pwd, "getpwnam", return_value=account),
                  patch.object(steps.grp, "getgrgid", return_value=SimpleNamespace(gr_name=group)),
                  patch.object(steps.os, "getgrouplist", return_value=groups), patch.object(steps, "run") as run):
                if uid == 123 and gid == 123 and group == ACCOUNT and groups == [123]:
                    self.assertIs(steps._account(), account)
                else:
                    with self.assertRaises(ValueError):
                        steps._account()
                run.assert_not_called()


class InstallationTests(unittest.TestCase):
    """Exercise first publication in a private tree, mocking account/host commands."""

    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory
        self.root = Path(self.enterContext(temporary_directory()))
        self.opt = self.root / "opt"
        self.opt.mkdir()
        paths = {"APP": self.opt / "runner", "DATA": self.root / "data",
                 "HOME": self.root / "data/home", "HOOK_DIR": self.root / "etc/hook",
                 "HOOK": self.root / "etc/hook/job-started.sh", "POLICY": self.root / "etc/policy.json",
                 "TOKEN_FILE": self.root / "registration-token"}
        for name, path in paths.items():
            self.enterContext(patch.object(steps, name, str(path)))
        self.token = Path(steps.TOKEN_FILE)
        self.token.write_bytes(b"A" * 40)
        self.token.chmod(0o600)
        self.enterContext(patch.object(steps, "check_runner_platform"))
        self.enterContext(patch.object(steps, "runner_lock", side_effect=nullcontext))
        self.enterContext(patch.object(gate, "check_installation"))
        self.enterContext(patch.object(steps, "is_dry_run", return_value=False))
        self.enterContext(patch.object(steps, "_account", return_value=SimpleNamespace(pw_uid=123, pw_gid=123)))

        def directory(path, **kwargs):
            Path(path).mkdir(parents=True, exist_ok=True)
            Path(path).chmod(kwargs.get("mode", 0o755))

        def staging(**kwargs):
            self.assertEqual(kwargs["dir"], "/opt")
            return temporary_directory(**(kwargs | {"dir": str(self.opt)}))

        def download(destination):
            with tarfile.open(destination, "w:gz") as archive:
                for name in ("bin/Runner.Listener", "bin/Runner.Worker", "config.sh"):
                    member = tarfile.TarInfo(name)
                    member.mode, member.size = 0o755, 4
                    archive.addfile(member, io.BytesIO(b"code"))

        def register(application, _policy, token, _account):
            self.assertEqual(token, b"A" * 40)
            for name in (".runner", ".credentials", ".credentials_rsaparams"):
                (application / name).write_text("generated configuration")

        self.enterContext(patch.object(steps, "_directory", side_effect=directory))
        self.enterContext(patch.object(steps.tempfile, "TemporaryDirectory", side_effect=staging))
        self.enterContext(patch.object(steps, "_download_release", side_effect=download))
        self.register = self.enterContext(patch.object(steps, "_register", side_effect=register))
        self.run = self.enterContext(patch.object(steps, "run"))

        def activate(_units, **_kwargs):
            current = json.loads(Path(steps.POLICY).read_text())
            self.assertEqual(Path(steps.HOOK).read_text(), steps.render_hook())
            for name in (".env", ".path", "bin/Runner.Worker"):
                self.assertTrue(Path(steps.APP, name).is_file())
            self.assertEqual(bool(_kwargs["activate"]), current["registration"] == "registered")

        self.units = self.enterContext(patch.object(steps, "replace_units", side_effect=activate))

    def test_first_install_publishes_complete_durable_tree_before_activation(self) -> None:
        with (patch.object(steps, "fsync_tree", wraps=steps.fsync_tree) as sync,
              patch.object(steps, "rename_path_durable", wraps=steps.rename_path_durable) as rename):
            steps.setup_github_runner(config(ci_registration_payload=True))
        self.assertEqual(sync.call_args.args[0], rename.call_args.args[0])
        self.assertEqual(rename.call_args.args[1], steps.APP)
        self.assertTrue(Path(steps.APP, "bin/Runner.Worker").is_file())
        self.assertEqual(Path(steps.APP, "_diag").readlink(), Path(steps.DATA, "diag"))
        self.assertEqual(Path(steps.APP, ".env").read_text(), "")
        self.assertEqual(Path(steps.HOOK).read_text(), steps.render_hook())
        self.assertEqual(json.loads(Path(steps.POLICY).read_text()), policy())
        self.assertFalse(self.token.exists())
        self.assertEqual(list(self.opt.iterdir()), [Path(steps.APP)])
        self.assertEqual(self.units.call_args_list, [unittest.mock.call({SERVICE: steps.render_unit()}, activate=()),
                                                   unittest.mock.call({SERVICE: steps.render_unit()}, activate=(SERVICE,))])
        self.assertTrue(all("docker" not in shlex.join(call.args[0]) for call in self.run.call_args_list))

    def test_registration_failure_consumes_token_and_leaves_disabled_pending_installation_retryable(self) -> None:
        self.register.side_effect = RuntimeError("registration failed")
        with self.assertRaisesRegex(RuntimeError, "registration failed"):
            steps.setup_github_runner(config(ci_registration_payload=True))
        self.assertFalse(self.token.exists())
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["registration"], "pending")
        self.assertTrue(Path(steps.APP, "bin/Runner.Worker").is_file())
        self.units.assert_called_once_with({SERVICE: steps.render_unit()}, activate=())
        self.assertIn(unittest.mock.call(["systemctl", "disable", "--now", SERVICE], timeout=120), self.run.call_args_list)
        self.register.side_effect = lambda application, *_: [(application / name).write_text("generated")
                                                           for name in (".runner", ".credentials", ".credentials_rsaparams")]
        self.token.write_bytes(b"A" * 40)
        self.token.chmod(0o600)
        steps.setup_github_runner(config(ci_registration_payload=True))
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["registration"], "registered")

    def test_tokenless_install_and_update_stay_pending_until_later_registration(self) -> None:
        steps.setup_github_runner(config())
        self.register.assert_not_called()
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["registration"], "pending")
        steps.setup_github_runner(config())
        self.assertTrue(all(call.kwargs["activate"] == () for call in self.units.call_args_list))
        steps.setup_github_runner(config(ci_registration_payload=True))
        self.register.assert_called_once()
        steps.setup_github_runner(config())
        self.register.assert_called_once()
        self.assertEqual(self.units.call_args.kwargs["activate"], (SERVICE,))

    def test_panel_operation_registers_existing_pending_files_without_persisting_token(self) -> None:
        steps.setup_github_runner(config())
        self.token.unlink()
        pending = json.loads(Path(steps.POLICY).read_text())
        with patch.object(gate, "check_installation", return_value=pending):
            steps.register_pending_runner(b"A" * 40)
        self.assertEqual(json.loads(Path(steps.POLICY).read_text()), policy())
        self.assertEqual(self.units.call_args.kwargs["activate"], (SERVICE,))
        self.register.assert_called_once()
        for path in self.root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                self.assertNotIn(b"A" * 40, path.read_bytes(), str(path))

    def test_partial_credentials_are_preserved_for_explicit_recovery(self) -> None:
        steps.setup_github_runner(config())
        Path(steps.APP, ".credentials").write_text("incomplete")
        with self.assertRaisesRegex(ValueError, "Partial runner registration"):
            steps.setup_github_runner(config(ci_registration_payload=True))
        self.register.assert_not_called()

    def test_activation_failure_preserves_registration_for_tokenless_service_repair(self) -> None:
        self.units.side_effect = [None, RuntimeError("activation failed")]
        with self.assertRaisesRegex(RuntimeError, "activation failed"):
            steps.setup_github_runner(config(ci_registration_payload=True))
        self.assertEqual(json.loads(Path(steps.POLICY).read_text())["registration"], "registered")
        self.assertTrue(Path(steps.APP, ".credentials").exists())
        self.units.side_effect = None
        steps.setup_github_runner(config())
        self.register.assert_called_once()
        self.assertEqual(self.units.call_args.kwargs["activate"], (SERVICE,))

    def test_unusable_token_is_removed_before_host_mutations(self) -> None:
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):
            steps.setup_github_runner(config(ci_registration_payload=True))
        self.assertFalse(self.token.exists())
        self.assertFalse(Path(steps.DATA).exists())
        self.run.assert_not_called()
        self.units.assert_not_called()

    def test_failed_policy_publication_leaves_stopped_runner_for_repair(self) -> None:
        steps.setup_github_runner(config(ci_registration_payload=True))
        self.units.reset_mock()
        with patch.object(steps, "write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                steps.setup_github_runner(config(ci_allowed_users=["bob"]))
        self.assertEqual(self.run.call_args.args[0], ["systemctl", "stop", SERVICE])
        self.assertEqual(json.loads(Path(steps.POLICY).read_text()), policy())
        self.units.assert_not_called()
