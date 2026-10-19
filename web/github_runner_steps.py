"""Install the official GitHub runner on a dedicated Debian 13 x86-64 host."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
import fcntl
import grp
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

from lib.atomic_io import (
    fsync_tree, read_json_file, rename_path_durable, write_bytes_atomic, write_json_atomic, write_text_atomic,
)
from lib.config import SetupConfig
from lib.github_runner_config import (
    ACCOUNT, APP, CREDENTIAL_FILES, DATA, GATE, HOME, HOOK, HOOK_DIR, POLICY, SERVICE, SHA256, TOKEN_FILE, VERSION,
    runner_target, validate_policy,
    RunnerPaths, installed_instances, repository_instance, runner_paths, runner_repositories,
)
from lib.machine_state import can_manage_system_services
from lib.github_runner_registration import RunnerRegistrationError, registration_error_message
from lib.remote_utils import is_dry_run, read_os_release, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import replace_units
from lib.validation import validate_filesystem_path, validate_github_runner_settings, validate_runner_registration_token


def check_runner_platform(config: SetupConfig) -> None:
    validate_github_runner_settings(config)
    release = read_os_release()
    if (release.get("ID") != "debian" or release.get("VERSION_ID") != "13"
            or platform.machine() != "x86_64" or not can_manage_system_services(config.machine_type)):
        raise ValueError("GitHub runner phase 1 requires native Debian 13/Trixie x86-64 with systemd")


def _private_bytes(path: str, limit: int) -> bytes:
    validate_filesystem_path(path, must_exist=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077 or info.st_size > limit):
            raise ValueError("Runner token must be a private, owned regular file (chmod 600)")
        return stream.read(limit + 1)


def _private_token(path: str) -> bytes:
    return validate_runner_registration_token(_private_bytes(path, 4097).strip())


def _paths(instance: str = "") -> RunnerPaths:
    return runner_paths(instance, app=APP, data=DATA, policy=POLICY, hook_dir=HOOK_DIR, hook=HOOK, service=SERVICE)


def stage_registration_token(source: str, destination: str) -> None:
    """Copy only a bounded one-hour token to the private expiring setup lease."""
    token = _private_token(source)
    os.makedirs(destination, mode=0o700)
    write_bytes_atomic(os.path.join(destination, "token"), token, mode=0o600)


def render_hook(instance: str = "") -> str:
    # A failed pre-job step alone can leave `if: always()` steps runnable.
    # Kill the shell's parent Runner.Worker on *any* error, including Python
    # startup/import errors. The Listener survives to accept the next job.
    return f'''#!/bin/bash
worker=$PPID
trap '/bin/kill -KILL "$worker"' EXIT
trap 'exit 1' HUP INT TERM
set -eu
/usr/bin/python3 -I {GATE}{' --instance ' + instance if instance else ''} --worker "$worker"
trap - EXIT HUP INT TERM
'''


def render_unit(paths: RunnerPaths | None = None) -> str:
    paths = paths or _paths()
    queue = _paths().data + "/queue.lock"
    APP, DATA, HOME, HOOK = paths.app, paths.data, paths.home, paths.hook
    return f'''# Managed by Basaltwater GitHub runner
[Unit]
Description=GitHub Actions runner
Wants=network-online.target
After=network-online.target

[Service]
User={ACCOUNT}
Group={ACCOUNT}
WorkingDirectory={APP}
Environment=HOME={HOME}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED={HOOK}
ExecStartPre=/usr/bin/python3 -I {GATE} --check{' --instance ' + paths.instance if paths.instance else ''}
ExecStart={APP}/bin/Runner.Listener run --startuptype service
Restart=always
RestartSec=10
TimeoutStopSec=120
KillMode=control-group
OOMPolicy=stop
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictSUIDSGID=yes
LimitCORE=0
ReadWritePaths={DATA} {queue}

[Install]
WantedBy=multi-user.target
'''


def _directory(path: str, *, uid: int = 0, gid: int = 0, mode: int = 0o755) -> None:
    with directory_fd(path, create=True, mode=mode) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != uid or info.st_mode & 0o022:
            # Newly created directories start root-owned. Never seize an
            # unrelated existing directory from an unprivileged owner.
            if info.st_uid != 0:
                raise ValueError(f"Unsafe runner directory: {path}")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)


def _account() -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--home-dir", HOME,
             "--shell", "/usr/sbin/nologin", ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
    groups = os.getgrouplist(ACCOUNT, account.pw_gid)
    if (account.pw_uid == 0 or account.pw_gid == 0 or grp.getgrgid(account.pw_gid).gr_name != ACCOUNT
            or account.pw_dir != HOME or account.pw_shell != "/usr/sbin/nologin"
            or groups != [account.pw_gid]):
        raise ValueError("Runner account must be dedicated, unprivileged and have no supplementary groups")
    return account


def _prepare_queue(account: pwd.struct_passwd) -> None:
    # Never replace this inode: another repository may currently hold its lock.
    with directory_fd(DATA) as directory:
        descriptor = os.open("queue.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in {0, account.pw_uid}:
            raise ValueError("Unsafe runner queue lock")
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _download_release(destination: Path) -> None:
    url = f"https://github.com/actions/runner/releases/download/v{VERSION}/actions-runner-linux-x64-{VERSION}.tar.gz"
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("xb") as stream:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ValueError("Runner release exceeds 512 MiB")
            digest.update(chunk)
            stream.write(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError("Official GitHub runner release checksum mismatch")


def _extract_release(archive: Path, destination: Path) -> None:
    total = 0
    links: list[tarfile.TarInfo] = []
    with tarfile.open(archive, "r:gz") as source:
        for index, member in enumerate(source):
            if member.isdir() and member.name in {".", "./"}:
                continue
            parts = Path(member.name).parts
            total += member.size
            if (not parts or member.name.startswith("/") or ".." in parts
                    or not (member.isfile() or member.isdir() or member.issym())
                    or total > 1536 * 1024 * 1024 or index >= 50000):
                raise ValueError("Unsafe runner release archive")
            target = destination.joinpath(*parts)
            if member.issym():
                links.append(member)
            elif member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    # Publish links last so archive extraction can never traverse one. Only
    # links to existing regular release content are needed by Node's tools.
    root = destination.resolve()
    for member in links:
        target = destination / member.name
        source = (target.parent / member.linkname).resolve()
        if (Path(member.linkname).is_absolute() or not source.is_relative_to(root)
                or not source.exists() or target.exists() or target.is_symlink()
                or not target.parent.resolve().is_relative_to(root)):
            raise ValueError("Unsafe runner release symlink")
        target.symlink_to(member.linkname)


def _register(application: Path, policy: dict, token: bytes, account: pwd.struct_passwd,
              paths: RunnerPaths | None = None) -> None:
    """Run configuration unprivileged in a disposable tree; keep the release immutable."""
    paths = paths or _paths()
    DATA, HOME = paths.data, paths.home
    with (tempfile.TemporaryDirectory(prefix="registration-", dir=DATA) as temporary,
          tempfile.TemporaryDirectory(prefix="basaltwater-runner-diag-", dir="/run") as diagnostics):
        registration = Path(temporary)
        shutil.copytree(application, registration, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns(*CREDENTIAL_FILES, "_diag", ".env", ".path", "instances"))
        # Vendor diagnostics must also remain volatile, even on failure.
        os.chown(diagnostics, account.pw_uid, account.pw_gid)
        (registration / "_diag").symlink_to(diagnostics)
        # Chown only our pristine tree, before executing any account-owned code.
        for current, directories, files in os.walk(registration):
            for name in (*directories, *files):
                os.chown(Path(current) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.chown(registration, account.pw_uid, account.pw_gid)
        # CommandSettings in the pinned runner consumes and clears this input:
        # actions/runner v2.338.0 src/Runner.Listener/CommandSettings.cs.
        environment = {"PATH": "/usr/bin:/bin", "HOME": HOME, "ACTIONS_RUNNER_INPUT_TOKEN": token.decode("ascii")}
        # Token travels only in this short-lived process's environment, never
        # argv, source, logs, saved setup or a systemd EnvironmentFile.
        try:
            # Debian installs runuser outside the restricted child PATH.
            command = ["/usr/sbin/runuser", "-u", ACCOUNT, "--", str(registration / "config.sh"),
                 "--unattended", "--url", "https://github.com/" + runner_target(policy),
                 "--name", "basaltwater-" + os.uname().nodename,
                 "--work", DATA + "/work", "--disableupdate", "--replace",
                 "--labels", ",".join(policy["labels"])]
            if policy.get("runner_group"):
                command.extend(["--runnergroup", policy["runner_group"]])
            result = run(command, cwd=temporary, env=environment, check=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
            if result.returncode != 0:
                raise RunnerRegistrationError("configure")
        except (subprocess.TimeoutExpired, TimeoutError):
            raise RunnerRegistrationError("timeout") from None
        except (OSError, subprocess.SubprocessError):
            raise RunnerRegistrationError("start") from None
        generated = {}
        try:
            for name in CREDENTIAL_FILES:
                descriptor = os.open(registration / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    content = stream.read(64 * 1024 + 1)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not content or len(content) > 64 * 1024:
                    raise ValueError("Invalid generated runner credential file")
                generated[name] = content
        except (OSError, ValueError):
            raise RunnerRegistrationError("credentials") from None
        for name, content in generated.items():
            write_bytes_atomic(str(application / name), content, uid=0, gid=account.pw_gid, mode=0o640)


@contextmanager
def runner_lock():
    """Serialize setup and panel registration; never queue a secret request."""
    _directory(HOOK_DIR)
    with directory_fd(HOOK_DIR) as directory:
        descriptor = os.open("registration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError("Unsafe runner registration lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Runner setup or registration is already running") from None
        yield
    finally:
        os.close(descriptor)


def _register_pending(policy: dict, token: bytes, account: pwd.struct_passwd,
                      paths: RunnerPaths | None = None) -> None:
    resolved = paths or _paths()
    APP, POLICY, SERVICE = resolved.app, resolved.policy, resolved.service
    if policy["registration"] != "pending":
        raise ValueError("Runner is already registered")
    if any(os.path.lexists(os.path.join(APP, name)) for name in CREDENTIAL_FILES):
        raise ValueError("Partial runner registration requires recovery; see runner guide")
    if paths:
        _register(Path(APP), policy, token, account, paths)
    else:
        _register(Path(APP), policy, token, account)
    policy = policy | {"registration": "registered"}
    write_json_atomic(POLICY, policy, mode=0o644)
    try:
        replace_units({SERVICE: render_unit(paths)}, activate=(SERVICE,))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise RunnerRegistrationError("activate") from None


def register_pending_runner(token: bytes, instance: str = "") -> None:
    """Fixed privileged operation: register only the installed pending policy."""
    from lib.github_runner_gate import check_installation

    validate_runner_registration_token(token)
    with runner_lock():
        try:
            policy = check_installation(require_registration=False, **({"instance": instance} if instance else {}))
        except (OSError, ValueError):
            raise RunnerRegistrationError("installation") from None
        if not instance and (policy["registration"] != "pending" or not policy.get("enabled", True)):
            for candidate in installed_instances(HOOK_DIR):
                candidate_policy = check_installation(require_registration=False, instance=candidate)
                if candidate_policy["registration"] == "pending" and candidate_policy.get("enabled", True):
                    policy, instance = candidate_policy, candidate
                    break
        if not policy.get("enabled", True):
            raise ValueError("Runner was removed from the configured repositories")
        account = _account()
        if instance:
            _register_pending(policy, token, account, _paths(instance))
        else:
            _register_pending(policy, token, account)


def _setup_runner(policy: dict, token: bytes | None, paths: RunnerPaths | None = None,
                  *, release: Path | None = None, dependencies: bool = True) -> None:
    resolved = paths or _paths()
    APP, DATA, HOME = resolved.app, resolved.data, resolved.home
    HOOK_DIR, HOOK, POLICY, SERVICE = resolved.hook_dir, resolved.hook, resolved.policy, resolved.service
    for path in (APP, DATA, HOOK_DIR, os.path.dirname(POLICY)):
        check_directory_path(path)
    try:
        previous = validate_policy(read_json_file(POLICY, max_bytes=16 * 1024))
    except FileNotFoundError:
        previous = None
    replacement = bool(previous and (runner_target(previous).lower() != runner_target(policy).lower()
                     or bool(previous.get("organization")) != bool(policy.get("organization"))
                     or previous.get("runner_group", "") != policy.get("runner_group", "")
                     or previous["labels"] != policy["labels"]))
    if previous is None and os.path.lexists(APP):
        raise ValueError("Unmanaged or interrupted runner installation exists; see runner recovery")
    policy["registration"] = previous["registration"] if previous else "pending"
    if previous:
        if replacement or previous["registration"] == "pending":
            credentials = [Path(APP, name) for name in CREDENTIAL_FILES if os.path.lexists(Path(APP, name))]
            if credentials:
                run(["systemctl", "stop", SERVICE], timeout=120)
                recovery = tempfile.mkdtemp(prefix="retired-registration-", dir=DATA)
                os.chmod(recovery, 0o700)
                write_json_atomic(os.path.join(recovery, "policy.json"), previous, mode=0o600)
                for path in credentials:
                    os.replace(path, Path(recovery, path.name))
                print("  Retired previous runner credentials; remove its old entry in GitHub Settings → Actions → Runners.")
            policy["registration"] = "pending"
            # Validate the unchanged application against its credential-free state.
            write_json_atomic(POLICY, previous | {"registration": "pending"}, mode=0o644)
        from lib.github_runner_gate import check_installation

        check_installation(require_registration=False, **({"instance": resolved.instance} if resolved.instance else {}))
    account = _account()
    _directory(DATA)
    _directory(HOME, uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    _directory(DATA + "/work", uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    _directory(DATA + "/diag", uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    # Only this telemetry subdirectory is readable by the panel account.
    _directory(DATA + "/telemetry", uid=account.pw_uid, gid=account.pw_gid)
    _directory(HOOK_DIR)
    _prepare_queue(account)
    if dependencies:
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "libicu76",
             "libssl3t64", "libkrb5-3", "zlib1g", "liblttng-ust1t64", "libcurl4t64", "libunwind8"])
    if previous is None:
        with tempfile.TemporaryDirectory(prefix=".github-runner-", dir="/opt") as temporary:
            staging = Path(temporary)
            staging.chmod(0o755)
            archive = release or staging / "runner.tar.gz"
            if release is None:
                _download_release(archive)
            application = staging / "app"
            application.mkdir(mode=0o755)
            _extract_release(archive, application)
            os.symlink(DATA + "/diag", application / "_diag")
            write_text_atomic(str(application / ".env"), "", mode=0o644)
            write_text_atomic(str(application / ".path"), "/usr/local/bin:/usr/bin:/bin", mode=0o644)
            fsync_tree(str(application))
            rename_path_durable(str(application), APP)
    else:
        run(["systemctl", "stop", SERVICE], timeout=120)
    write_text_atomic(HOOK, render_hook(resolved.instance), mode=0o755)
    write_json_atomic(POLICY, policy, mode=0o644)
    registered = policy["registration"] == "registered"
    replace_units({SERVICE: render_unit(paths)}, activate=(SERVICE,) if registered else ())
    if not registered:
        run(["systemctl", "disable", "--now", SERVICE], timeout=120)
        if token is not None:
            try:
                _register_pending(policy, token, account, paths)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                print(f"  Warning: {registration_error_message(exc)} Setup will continue.")
        else:
            print("  ✓ Runner installed: Pending registration (service disabled)")


def setup_github_runner(config: SetupConfig) -> None:
    check_runner_platform(config)
    policies = [validate_policy(dict(schema=1, repository=repository, organization=config.ci_organization,
                                  runner_group=config.ci_runner_group,
                                  allowed_users=list(dict.fromkeys(user.lower() for user in config.ci_allowed_users or [])),
                                  labels=list(dict.fromkeys(config.ci_labels or [])), runner_version=VERSION))
                for repository in runner_repositories(config) or [""]]
    if is_dry_run():
        print(f"  [DRY-RUN] Official runner {VERSION} for {', '.join(map(runner_target, policies))}; one job at a time")
        return
    tokens = {}
    if config.ci_registration_payload:
        payload = TOKEN_FILE if len(policies) == 1 else str(Path(TOKEN_FILE).with_name("tokens.json"))
        try:
            if len(policies) == 1:
                tokens[runner_target(policies[0]).lower()] = _private_token(payload)
            else:
                bundle = json.loads(_private_bytes(payload, 100 * 1024))
                targets = {runner_target(policy).lower() for policy in policies}
                if not isinstance(bundle, dict) or not set(bundle) <= targets or any(not isinstance(v, str) for v in bundle.values()):
                    raise ValueError("Invalid token bundle")
                tokens = {key: validate_runner_registration_token(value.encode("ascii")) for key, value in bundle.items()}
        except (OSError, ValueError):
            print("  Warning: Unusable runner registration token; continuing setup with registration deferred.")
        finally:
            Path(payload).unlink(missing_ok=True)
    with runner_lock(), ExitStack() as lease:
        release = None
        dependencies = True

        def install(policy: dict, paths: RunnerPaths | None = None) -> None:
            nonlocal release, dependencies
            new = primary is None if paths is None else not os.path.lexists(paths.policy)
            if new and release is None:
                staging = lease.enter_context(tempfile.TemporaryDirectory(prefix=".github-runner-release-", dir="/opt"))
                release = Path(staging) / "runner.tar.gz"
                _download_release(release)
            _setup_runner(policy, tokens.get(runner_target(policy).lower()), paths,
                          release=release, dependencies=dependencies)
            dependencies = False

        try:
            primary = validate_policy(read_json_file(POLICY, max_bytes=16 * 1024))
        except FileNotFoundError:
            primary = None
        primary_target = runner_target(primary).lower() if primary else runner_target(policies[0]).lower()
        existing = installed_instances(HOOK_DIR)
        selected = next((policy for policy in policies if runner_target(policy).lower() == primary_target), None)
        if selected is None and (not policies[0]["repository"] or repository_instance(policies[0]["repository"]) not in existing):
            selected = policies[0]
        if selected is not None:
            install(selected)
        elif primary:
            run(["systemctl", "disable", "--now", SERVICE], timeout=120)
            write_json_atomic(POLICY, primary | {"enabled": False}, mode=0o644)
        desired = set()
        for policy in policies:
            if policy is selected:
                continue
            instance = repository_instance(policy["repository"])
            desired.add(instance)
            paths = _paths(instance)
            for parent in (APP + "/instances", DATA + "/instances", HOOK_DIR + "/instances"):
                _directory(parent)
            install(policy, paths)
        for instance in existing:
            if instance not in desired:
                paths = _paths(instance)
                run(["systemctl", "disable", "--now", paths.service], timeout=120)
                previous = validate_policy(read_json_file(paths.policy, max_bytes=16 * 1024))
                write_json_atomic(paths.policy, previous | {"enabled": False}, mode=0o644)
