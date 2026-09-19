"""One-time cutover from recent infra-tools installations, without aliases."""

from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import pwd
import grp
import re
import shlex
import shutil
import stat
import subprocess

from lib.atomic_io import write_json_atomic
from lib.validation import validate_filesystem_path


SYSTEM_DIRS = (
    "opt", "etc", "var/lib", "var/log", "var/cache", "srv", "run/lock",
)
USER_DIRS = (".config", ".cache", ".local/share", ".local/state", "Pictures")
DATA_NAMES = {"infra_tools", "infra-tools", "infra-tools-playwright", "infra-tools-privilege-broker"}
RESOURCE_DIRS = (
    "etc/systemd/system", "etc/nginx/sites-available", "etc/nginx/sites-enabled",
    "etc/sudoers.d", "etc/apt/sources.list.d", "etc/apt/apt.conf.d",
    "etc/ssh/sshd_config.d", "etc/sysctl.d", "etc/audit/rules.d",
    "etc/fail2ban/filter.d", "etc/fail2ban/jail.d", "etc/modules-load.d",
    "etc/kernel/postinst.d", "etc/letsencrypt/renewal-hooks/deploy",
    "usr/local/bin", "usr/local/sbin", "usr/local/share/ca-certificates",
    "etc/xrdp", "etc/pve/firewall", "etc/ufw", "etc/nginx/conf.d", "etc/fail2ban",
    "etc/tmpfiles.d", "etc/sysusers.d", "etc/polkit-1/rules.d",
    "etc/systemd/network", "etc/network/interfaces.d", "etc/cloud/cloud.cfg.d",
    "etc/apt/preferences.d", "usr/share/pam-configs", "usr/share/keyrings",
    "usr/local/libexec", "usr/share/applications",
)
ACCOUNTS = (("infra-web-panel", "basaltwater-web-panel"), ("infra-approval", "basaltwater-approval"))
PREFIXES = ("infra_tools", "infra-tools", "infra-web", "infra-approval", "infra-syncthing", "infra-desktop", "infra-management", "infra-control-plane", "infra-guests", "infra-cluster-management", "infra-deny-control-plane")
_UFW_OWNED_COMMENT = re.compile(
    r"^(infra-tools|infra_tools) "
    r"(?:T3 Code|HTTPS forward|Gogs|SSH|RDP|web TCP|mDNS UDP|access source|Samba 445/tcp source)(?= |$)"
)


def rename_text(value: str) -> str:
    """Translate owned configuration, excluding the current repository URLs."""
    value = value.replace("bluehexagons/infra_tools", "REPOSITORY_LOCATION")
    value = value.replace("INFRA_TOOLS_", "BASALTWATER_")
    for prefix in PREFIXES:
        value = value.replace(prefix, prefix.replace("infra_tools", "basaltwater").replace("infra-tools", "basaltwater").replace("infra-", "basaltwater-"))
    value = value.replace("infra_web.py", "basaltwater_web.py")
    return value.replace("REPOSITORY_LOCATION", "bluehexagons/infra_tools")


def _configuration_text(content: str, translate=rename_text) -> str:
    """Rename configuration references without rewriting inline secret values."""
    lines = []
    for line in content.splitlines(keepends=True):
        secret = re.search(r"(?i)(?:password|secret|token|api[_-]?key)\s*=", line)
        if secret:
            lines.append(translate(line[:secret.end()]) + line[secret.end():])
        else:
            lines.append(translate(line))
    return "".join(lines)


def _managed_marker_edits(root: Path) -> list[dict]:
    """Repair encoded UFW ownership and managed Codex policy markers only."""
    edits = []
    for relative in ("etc/ufw/user.rules", "etc/ufw/user6.rules",
                     "etc/codex/config.toml", "etc/codex/requirements.toml"):
        path = root / relative
        _safe(path)
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
            raise ValueError(f"Unsafe managed marker resource: {path}")
        content = path.read_text()
        if relative.startswith("etc/ufw/"):
            def rewrite(match):
                try:
                    comment = bytes.fromhex(match[2]).decode("utf-8")
                except (ValueError, UnicodeError):
                    return match[0]
                owned = _UFW_OWNED_COMMENT.match(comment)
                if owned is None:
                    return match[0]
                # Rename only ownership, preserving route names and other
                # operator-selected text even when it contains the old brand.
                updated_comment = "basaltwater" + comment[owned.end(1):]
                return match[1] + updated_comment.encode().hex()
            updated = re.sub(r"(?m)^(### tuple ###[^\n]* comment=)([0-9a-fA-F]+)(?=\s*$)", rewrite, content)
        else:
            updated = content
            for brand in ("infra-tools", "infra_tools"):
                marker = f"# Managed by {brand} coding-agent security policy."
                if marker in content.splitlines():
                    updated = updated.replace(marker, rename_text(marker))
        if updated != content:
            edits.append(_edit_action(path, updated.encode(), path))
    return edits


def repair_managed_markers(root: Path) -> None:
    """Idempotent follow-up for hosts whose original cutover already finished.

    UFW reads these comments from its rule files on each invocation; packet
    rules are unchanged, so no firewall reload or deletion is necessary.
    """
    for action in _managed_marker_edits(root):
        _write_bytes(action, "after")


def _safe(path: Path) -> None:
    validate_filesystem_path(str(path))
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Migration path must be absolute and normalized: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(f"Refusing symlinked migration parent: {parent}")


def _move_plan(old: Path, new: Path, actions: list[dict], occupied: dict[Path, Path]) -> None:
    """Merge disjoint directory trees; never overwrite duplicate entries."""
    _safe(old)
    _safe(new)
    existing = occupied.get(new, new)
    if os.path.lexists(existing):
        if old.is_dir() and existing.is_dir() and not old.is_symlink() and not existing.is_symlink():
            source_info, target_info = old.stat(), existing.stat()
            if (source_info.st_uid, source_info.st_gid) != (target_info.st_uid, target_info.st_gid):
                raise ValueError(f"Conflicting directory ownership: {old} and {new}")
            for child in sorted(old.iterdir()):
                target = new / child.name
                if (existing / child.name).exists():
                    occupied.setdefault(target, existing / child.name)
                _move_plan(child, target, actions, occupied)
            info = old.stat()
            actions.append({"kind": "rmdir", "old": str(old), "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid})
            actions.append({"kind": "chmod", "old": str(new), "before": stat.S_IMODE(target_info.st_mode), "after": stat.S_IMODE(target_info.st_mode) & stat.S_IMODE(info.st_mode)})
            return
        raise ValueError(f"Conflicting migration paths: {old} and {new}")
    occupied[new] = old
    actions.append({"kind": "move", "old": str(old), "new": str(new)})


def _edit_action(path: Path, content: bytes, original: Path) -> dict:
    info = original.stat()
    if os.geteuid() != 0 and info.st_uid != os.geteuid():
        raise ValueError(f"Configuration belongs to another user: {original}")
    return {"kind": "edit", "old": str(path), "before": base64.b64encode(original.read_bytes()).decode(),
            "after": base64.b64encode(content).decode(), "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def _state_edits(old: Path, new: Path) -> list[dict]:
    """Update path-bearing JSON fields, keeping secrets and opaque state intact."""
    edits = []
    for path in old.rglob("*.json"):
        if path.is_symlink() or any(part.startswith(".git") for part in path.relative_to(old).parts):
            continue
        if any((parent / ".git").exists() for parent in path.parents if parent.is_relative_to(old)):
            continue
        if any(word in path.name.lower() for word in ("credential", "secret", "auth", "token")):
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            continue
        def rewrite(value, key=""):
            if isinstance(value, dict):
                return {k: rewrite(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v, key) for v in value]
            if isinstance(value, str) and not any(word in key.lower() for word in ("password", "secret", "token", "credential", "api_key", "apikey", "api-key")):
                if value.startswith(("/opt/infra", "/etc/infra", "/var/lib/infra", "/var/log/infra", "/srv/infra", str(old))):
                    return rename_text(value)
                if key in ("command", "script") and value.startswith("infra-tools"):
                    return "basaltw" + value[len("infra-tools"):]
                if key in ("url", "ca_url", "certificate_url", "readiness_url"):
                    return value.replace("/infra-tools-ca.crt", "/basaltwater-ca.crt").replace("/infra-tools-homebox-readiness", "/basaltwater-homebox-readiness")
            return value
        updated = rewrite(data)
        if updated != data:
            edits.append(_edit_action(new / path.relative_to(old), (json.dumps(updated, indent=2) + "\n").encode(), path))
    return edits


def _repository_content(path: Path, root: Path) -> bool:
    """Keep repository files and Git metadata opaque during data migration."""
    return ".git" in path.relative_to(root).parts or any(
        os.path.lexists(parent / ".git")
        for parent in path.parents if parent.is_relative_to(root)
    )


def _worktree_edits(old: Path, new: Path, root: Path) -> list[dict]:
    """Relocate linked worktrees whose common repository stays in place.

    Only the reciprocal Git pointers change. Use ordinary journaled edits so
    interrupted cutovers can restore the original registration and gitfile.
    """
    edits = []
    for gitfile in old.rglob(".git"):
        if gitfile.is_dir() and not gitfile.is_symlink():
            continue
        _safe(gitfile)
        if gitfile.is_symlink() or not gitfile.is_file():
            raise ValueError(f"Unsafe worktree Git file: {gitfile}")
        content = gitfile.read_text().strip()
        if not content.startswith("gitdir: "):
            raise ValueError(f"Invalid worktree Git file: {gitfile}")
        gitdir = Path(os.path.abspath(gitfile.parent / content[len("gitdir: "):]))
        backlink = gitdir / "gitdir"
        common_file = gitdir / "commondir"
        for path in (backlink, common_file):
            _safe(path)
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
                raise ValueError(f"Unsafe linked worktree metadata: {path}")
        common = Path(os.path.abspath(gitdir / common_file.read_text().strip()))
        _safe(common)
        if common.is_symlink() or not common.is_dir() or gitdir.parent != common / "worktrees":
            raise ValueError(f"Unrecognized linked worktree layout: {gitfile}")
        # Moving a primary repository needs a separate plan for all its other
        # worktrees. Recent managed worktrees use a stable external checkout.
        if any(common.is_relative_to(root / parent / name)
               for parent in (*USER_DIRS, *SYSTEM_DIRS) for name in DATA_NAMES):
            raise ValueError(f"Worktree common repository must be outside migrating data: {common}")
        registered = Path(os.path.abspath(gitdir / backlink.read_text().strip()))
        if registered != gitfile:
            raise ValueError(f"Worktree registration does not match: {gitfile}")
        destination = new / gitfile.relative_to(old)
        edits.append(_edit_action(destination, f"gitdir: {gitdir}\n".encode(), gitfile))
        edits.append(_edit_action(backlink, f"{destination}\n".encode(), backlink))
    return edits


def _owned_tree_edits(old: Path, new: Path) -> list[dict]:
    """Refresh generated environment files and owned data-tree links."""
    actions = []
    for path in old.rglob("*"):
        if _repository_content(path, old):
            continue
        destination = new / path.relative_to(old)
        if path.is_symlink():
            before = os.readlink(path)
            after = rename_text(before)
            if after != before:
                actions.append({"kind": "link", "old": str(destination), "new": str(destination), "before": before, "after": after})
        elif path.is_file() and path.name == "shell-env.sh":
            content = path.read_text()
            if "Managed by infra_tools" in content:
                actions.append(_edit_action(destination, _configuration_text(content).encode(), path))
        elif path.is_file() and path.suffix == ".env":
            content = path.read_text()
            updated = "".join(_configuration_text(line) if line.startswith("INFRA_TOOLS_") else line for line in content.splitlines(keepends=True))
            if updated != content:
                actions.append(_edit_action(destination, updated.encode(), path))
    certificate = old / "web/infra-tools-ca.crt"
    if certificate.is_file() and not certificate.is_symlink():
        actions.append({"kind": "move", "old": str(new / "web/infra-tools-ca.crt"), "new": str(new / "web/basaltwater-ca.crt")})
    return actions


def build_plan(root: Path, *, system: bool, runtime_source: Path | None = None, installation: Path | None = None) -> dict:
    """Inspect a recent installation; the preview performs no system changes."""
    root = root.absolute()
    _safe(root / "placeholder")
    actions: list[dict] = []
    edits: list[dict] = []
    occupied: dict[Path, Path] = {}
    recovery = root / ("var/lib/basaltwater-migration" if system else ".local/state/basaltwater-migration")
    legacy_runtime = installation if installation is not None else (root / "opt/infra_tools" if system else root / ".local/share/infra_tools")
    _safe(legacy_runtime)
    if not system and not legacy_runtime.is_relative_to(root):
        raise ValueError("User installation must be inside the current home; use --system for a system source tree")
    has_runtime = (legacy_runtime / "infra_tools.py").is_file()
    if installation is not None and not has_runtime:
        raise ValueError("Explicit installation is not a recent infra-tools source tree")
    def translate(value: str) -> str:
        return rename_text(value.replace(str(legacy_runtime) + "/", str(legacy_runtime.with_name("basaltwater")) + "/"))
    if has_runtime:
        if legacy_runtime.is_symlink():
            raise ValueError(f"Unexpected runtime symlink: {legacy_runtime}")
        if any(path.is_file() for path in legacy_runtime.rglob(".git")):
            raise ValueError(f"Remove managed Git worktrees before migration: {legacy_runtime}")
        if not (legacy_runtime / "lib/installation_info.py").is_file():
            raise ValueError("Only recent infra-tools source with installation provenance is supported")
        if runtime_source is None or not (runtime_source / "basaltwater.py").is_file():
            raise ValueError("A complete Basaltwater source tree is required for runtime cutover")
        destination = legacy_runtime.with_name("basaltwater")
        if os.path.lexists(destination):
            raise ValueError(f"Runtime destination already exists: {destination}")
    for parent in SYSTEM_DIRS if system else USER_DIRS:
        directory = root / parent
        if not directory.is_dir():
            continue
        _safe(directory / "placeholder")
        for old in sorted(directory.iterdir()):
            if old == legacy_runtime and has_runtime:
                continue
            if not (old.name.startswith(PREFIXES) if parent == "run/lock" else old.name in DATA_NAMES):
                continue
            if old.is_symlink() or (not old.is_dir() and parent != "run/lock"):
                raise ValueError(f"Unexpected legacy data path: {old}")
            new = old.with_name(rename_text(old.name))
            if old.is_dir():
                edits.extend(_worktree_edits(old, new, root))
            _move_plan(old, new, actions, occupied)
            if old.is_dir():
                edits.extend(_state_edits(old, new))
                edits.extend(_owned_tree_edits(old, new))
    if has_runtime:
        state = legacy_runtime / "state"
        if system and state.is_dir() and not state.is_symlink():
            destination_state = root / "var/lib/basaltwater"
            _move_plan(state, destination_state, actions, occupied)
            edits.extend(_state_edits(state, destination_state))
        elif state.is_symlink() and system and state.resolve() != root / "var/lib/infra_tools":
            raise ValueError(f"Unexpected runtime state link: {state}")
        elif not system and state.is_dir() and not state.is_symlink():
            edits.extend(_state_edits(state, legacy_runtime.with_name("basaltwater") / "state"))
        actions.append({"kind": "runtime", "old": str(legacy_runtime), "new": str(legacy_runtime.with_name("basaltwater")), "source": str(runtime_source)})
    resources = list(RESOURCE_DIRS if system else (".config/systemd/user", ".local/bin"))
    if system and (root / "etc/pve/nodes").is_dir():
        resources.extend(str(p.relative_to(root)) for p in (root / "etc/pve/nodes").iterdir() if p.is_dir() and not p.is_symlink())
    units = []
    launcher_targets: set[Path] = set()
    for relative in resources:
        directory = root / relative
        if not directory.is_dir():
            continue
        _safe(directory / "placeholder")
        entries = directory.rglob("*") if "systemd" in directory.parts else directory.iterdir()
        for old in sorted(entries):
            if relative == "etc/ufw" and old.name in ("user.rules", "user6.rules"):
                continue  # Encoded ownership comments are handled separately.
            if any(part.endswith((".wants", ".requires")) for part in old.relative_to(directory).parts):
                continue
            if old.is_dir() and not old.is_symlink():
                continue
            if old.name.endswith((".bak", ".backup")) or ".backup." in old.name:
                continue
            is_link = old.is_symlink()
            if relative == "usr/share/keyrings":
                if any(prefix in old.name for prefix in PREFIXES):
                    _move_plan(old, old.with_name(rename_text(old.name)), actions, occupied)
                continue
            try:
                content = os.readlink(old) if is_link else old.read_text()
            except UnicodeError:
                continue
            managed = old.name.startswith(PREFIXES) or any(marker in content for marker in PREFIXES)
            if not managed:
                continue
            if not is_link and old.stat().st_uid != os.geteuid():
                raise ValueError(f"Unexpected resource owner: {old}")
            name = "basaltw" if old.name in ("infra-tools", "infra_tools") else rename_text(old.name)
            new = old.with_name(name)
            if new != old and os.path.lexists(new):
                raise ValueError(f"Resource destination already exists: {new}")
            if name == "basaltw":
                if not any(marker in content for marker in ("# Auto-generated by infra-tools", "# Auto-generated by infra_tools", "# Managed by infra_tools agent setup", str(legacy_runtime / "infra_tools.py"))):
                    raise ValueError(f"Unmanaged or package-owned old launcher; remove it through its owner: {old}")
                if new in launcher_targets:
                    actions.append({"kind": "retire", "old": str(old)})
                    continue
                launcher_targets.add(new)
                if has_runtime:
                    script = str(legacy_runtime.with_name("basaltwater") / "basaltwater.py")
                else:
                    invocation = next((shlex.split(line) for line in content.splitlines() if line.startswith("exec ")), [])
                    if len(invocation) < 3:
                        raise ValueError(f"Unrecognized legacy launcher: {old}")
                    script = translate(invocation[2])
                    if not Path(script).is_file():
                        raise ValueError(f"Migrate this launcher's recent system source first: {old}")
                from lib.orchestrator_bootstrap import _launcher_contents
                content = _launcher_contents(script)
                if is_link:
                    raise ValueError(f"Recent managed launchers must be regular files: {old}")
            else:
                content = _configuration_text(content, translate) if not is_link else translate(content)
            if new == old and content == (os.readlink(old) if is_link else old.read_text()):
                continue
            if is_link:
                actions.append({"kind": "link", "old": str(old), "new": str(new), "before": os.readlink(old), "after": content})
            else:
                if old != new:
                    _move_plan(old, new, actions, occupied)
                edits.append(_edit_action(new, content.encode(), old))
            if "systemd" in old.parts and not is_link and old.suffix in (".service", ".timer", ".path", ".socket"):
                unit = {"old": old.name, "new": new.name}
                if unit not in units:
                    units.append(unit)
            elif "systemd" in old.parts and old.suffix == ".conf" and old.parent.name.endswith((".service.d", ".timer.d", ".path.d", ".socket.d")):
                # Drop-ins affect their owning unit even when the unit itself
                # comes from an upstream package and keeps its original name.
                name = old.parent.name[:-2]
                unit = {"old": name, "new": name}
                if unit not in units:
                    units.append(unit)
    if system:
        edits.extend(_managed_marker_edits(root))
        for relative in ("etc/fstab", "etc/crontab", "etc/environment"):
            path = root / relative
            if path.is_file():
                _safe(path)
                if path.is_symlink():
                    raise ValueError(f"Unsafe host configuration: {path}")
                content = path.read_text()
                updated = _configuration_text(content, translate)
                if updated != content:
                    edits.append(_edit_action(path, updated.encode(), path))
    completion_dirs = ("etc/bash_completion.d", "usr/share/zsh/site-functions", "usr/local/share/zsh/site-functions") if system else (".config/fish/completions",)
    for relative in completion_dirs:
        directory = root / relative
        if not directory.is_dir():
            continue
        for old in sorted(directory.iterdir()):
            if old.name not in {prefix + name + suffix for prefix in ("", "_") for name in ("infra-tools", "infra_tools", "infra_tools.py") for suffix in ("", ".fish")}:
                continue
            _safe(old)
            if old.is_symlink() or not old.is_file():
                raise ValueError(f"Unsafe completion: {old}")
            name = ("_" if old.name.startswith("_") else "") + "basaltw" + (".fish" if old.suffix == ".fish" else "")
            new = old.with_name(name)
            _move_plan(old, new, actions, occupied)
            content = old.read_text()
            updated = re.sub(r"infra_tools(?:\.py)?|infra-tools", "basaltw", content)
            edits.append(_edit_action(new, updated.encode(), old))
    if not system:
        skills = root / ".agents/skills"
        if skills.is_dir():
            _safe(skills / "placeholder")
            for old in sorted(skills.glob("infra-tools-*")):
                skill = old / "SKILL.md"
                _safe(skill)
                if old.is_symlink() or skill.is_symlink() or not skill.is_file():
                    raise ValueError(f"Unsafe legacy skill: {old}")
                content = skill.read_text()
                if "managed-by: infra_tools" not in content:
                    raise ValueError(f"Unmanaged legacy skill: {old}")
                new = old.with_name(rename_text(old.name))
                _move_plan(old, new, actions, occupied)
                canonical = Path(__file__).resolve().parents[1] / "common/agent_skills" / new.name / "SKILL.md"
                if not canonical.is_file():
                    raise ValueError(f"No replacement skill for {old}")
                edits.append(_edit_action(new / "SKILL.md", canonical.read_bytes(), skill))
        for name in (".bashrc", ".bash_profile", ".zshrc", ".profile", ".infra_toolsrc"):
            old = root / name
            if not old.is_file():
                continue
            if old.is_symlink():
                raise ValueError(f"Unsafe shell configuration: {old}")
            content = old.read_text()
            updated = _configuration_text(content, translate).replace("register-python-argcomplete basaltwater", "register-python-argcomplete basaltw")
            new = root / ".basaltwaterrc" if name == ".infra_toolsrc" else old
            if old != new:
                _move_plan(old, new, actions, occupied)
            if updated != content:
                edits.append(_edit_action(new, updated.encode(), old))
        for relative in (".codex/config.toml", ".config/opencode/opencode.json", ".config/opencode/opencode.jsonc"):
            path = root / relative
            if not path.is_file():
                continue
            _safe(path)
            if path.is_symlink():
                raise ValueError(f"Unsafe agent configuration: {path}")
            content = path.read_text()
            if path.suffix in (".json", ".jsonc"):
                from common.browser_automation_steps import _load_opencode_config
                data = _load_opencode_config(str(path))
                servers = data.get("mcp", {})
                if not isinstance(servers, dict):
                    raise ValueError(f"Invalid MCP configuration: {path}")
                changed = False
                for name in list(servers):
                    new_name = rename_text(name)
                    if new_name == name:
                        continue
                    if new_name in servers:
                        raise ValueError(f"Conflicting MCP registrations: {name}, {new_name}")
                    server = servers.pop(name)
                    if isinstance(server, dict):
                        command = server.get("command")
                        if isinstance(command, list):
                            server["command"] = [translate(value) if isinstance(value, str) else value for value in command]
                        elif isinstance(command, str):
                            server["command"] = translate(command)
                    servers[new_name] = server
                    changed = True
                if changed:
                    edits.append(_edit_action(path, (json.dumps(data, indent=2) + "\n").encode(), path))
                continue
            lines = []
            for line in content.splitlines(keepends=True):
                if not re.search(r"(?i)(password|secret|token|api[_-]?key)", line):
                    line = translate(line)
                lines.append(line)
            updated = "".join(lines)
            tables = re.findall(r'^\s*\[([^\[\]\n]+)\]\s*(?:#.*)?$', updated, re.MULTILINE)
            normalized_tables = [table.replace('"', '').replace("'", '') for table in tables]
            if len(normalized_tables) != len(set(normalized_tables)):
                raise ValueError(f"Conflicting agent configuration tables: {path}")
            if updated != content:
                edits.append(_edit_action(path, updated.encode(), path))
    actions.extend(edits)
    return {"schema": 1, "root": str(root), "system": system, "recovery": str(recovery), "actions": actions, "units": units, "completed": 0, "status": "planned"}


def _write_bytes(action: dict, field: str) -> None:
    path = Path(action["old"])
    _safe(path)
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked configuration: {path}")
    from lib.atomic_io import write_text_atomic

    write_text_atomic(str(path), base64.b64decode(action[field]).decode(), mode=action["mode"], uid=action["uid"], gid=action["gid"])


def _save(plan: dict) -> None:
    write_json_atomic(str(Path(plan["recovery"]) / "journal.json"), plan, mode=0o600)


def _systemctl(plan: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", *([] if plan["system"] else ["--user"]), *args], check=check, capture_output=True, text=True)


def _reload_integrations(plan: dict) -> None:
    affected = {part for action in plan["actions"] for part in Path(action["old"]).parts}
    if "nginx" in affected:
        subprocess.run(["nginx", "-t"], check=True)
        _systemctl(plan, "reload", "nginx")
    if "ufw" in affected:
        subprocess.run(["ufw", "reload"], check=True)
    if "fail2ban" in affected:
        if _systemctl(plan, "is-active", "--quiet", "fail2ban", check=False).returncode == 0:
            _systemctl(plan, "reload", "fail2ban")
    if "ca-certificates" in affected:
        subprocess.run(["update-ca-certificates"], check=True)


def apply_plan(plan: dict) -> None:
    handles = []
    try:
        _apply_plan(plan, handles)
    finally:
        for handle in handles:
            handle.close()


def _apply_plan(plan: dict, lock_handles: list) -> None:
    """Apply once, retaining a private journal for explicit failure recovery."""
    recovery = Path(plan["recovery"])
    _safe(recovery)
    if os.path.lexists(recovery):
        raise ValueError(f"Migration journal already exists; inspect or recover it first: {recovery}")
    for unit in plan["units"]:
        unit["active"] = _systemctl(plan, "is-active", "--quiet", unit["old"], check=False).returncode == 0
        unit["enabled"] = _systemctl(plan, "is-enabled", "--quiet", unit["old"], check=False).returncode == 0
    plan["accounts"] = []
    plan["groups"] = []
    plan["homes"] = []
    if plan["system"]:
        root = Path(plan["root"])
        for account in pwd.getpwall():
            home = Path(account.pw_dir)
            if any(home.is_relative_to(root / parent / name) for parent in SYSTEM_DIRS for name in DATA_NAMES):
                renamed = rename_text(account.pw_dir)
                if renamed != account.pw_dir:
                    plan["homes"].append({"user": dict(ACCOUNTS).get(account.pw_name, account.pw_name), "before": account.pw_dir, "after": renamed})
        for old, new in ACCOUNTS:
            try:
                pwd.getpwnam(old)
            except KeyError:
                continue
            try:
                pwd.getpwnam(new)
            except KeyError:
                pass
            else:
                raise ValueError(f"Conflicting service accounts: {old}, {new}")
            try:
                grp.getgrnam(new)
            except KeyError:
                pass
            else:
                raise ValueError(f"Conflicting service group: {new}")
            plan["accounts"].append([old, new])
        try:
            grp.getgrnam("infra-desktop")
        except KeyError:
            pass
        else:
            try:
                grp.getgrnam("basaltwater-desktop")
            except KeyError:
                plan["groups"].append(["infra-desktop", "basaltwater-desktop"])
            else:
                raise ValueError("Conflicting desktop groups")
    # Check the recent setup lock before moving it to the new namespace.
    # Active controllers must finish before a one-time host cutover.
    for action in plan["actions"]:
        old = Path(action["old"])
        if "run/lock/" not in str(old):
            continue
        candidates = list(old.rglob("*")) if old.is_dir() else [old]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            handle = candidate.open("r+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                for previous in lock_handles:
                    previous.close()
                raise ValueError(f"Active operation holds {candidate}; wait for it to finish")
            lock_handles.append(handle)
    recovery.mkdir(mode=0o700, parents=True)
    _save(plan)
    if plan["units"]:
        _systemctl(plan, "stop", *(unit["old"] for unit in plan["units"]))
    for unit in plan["units"]:
        if unit["enabled"] and unit["old"] != unit["new"]:
            _systemctl(plan, "disable", unit["old"])
    for old, new in plan["accounts"]:
        subprocess.run(["usermod", "--login", new, old], check=True)
        try:
            grp.getgrnam(old)
        except KeyError:
            pass
        else:
            subprocess.run(["groupmod", "--new-name", new, old], check=True)
    for old, new in plan["groups"]:
        subprocess.run(["groupmod", "--new-name", new, old], check=True)
    for home in plan["homes"]:
        subprocess.run(["usermod", "--home", home["after"], home["user"]], check=True)
    for index, action in enumerate(plan["actions"]):
        old = Path(action["old"])
        _safe(old)
        kind = action["kind"]
        plan["pending"] = index
        _save(plan)
        if kind == "move":
            new = Path(action["new"])
            _safe(new)
            if os.path.lexists(new):
                raise ValueError(f"Migration destination appeared: {new}")
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        elif kind == "rmdir":
            old.rmdir()
        elif kind == "edit":
            _write_bytes(action, "after")
        elif kind == "chmod":
            old.chmod(action["after"])
        elif kind == "link":
            old.unlink()
            Path(action["new"]).symlink_to(action["after"])
        elif kind == "retire":
            old.rename(recovery / f"retired-{index}")
        elif kind == "runtime":
            from lib.setup_common import copy_project_files
            stage = recovery / "runtime-stage"
            stage.mkdir()
            # The recovery parent stays private, but this directory becomes
            # the shared runtime used by unprivileged services and user passes.
            stage.chmod(0o755)
            copy_project_files(str(stage))
            for name in ("state", "deployments"):
                existing = old / name
                if existing.is_dir() and not existing.is_symlink() and not (name == "state" and plan["system"]):
                    shutil.copytree(existing, stage / name, symlinks=True, copy_function=shutil.copy2)
            (stage / ".basaltwater").mkdir(mode=0o700)
            (stage / ".basaltwater/managed-install").write_text("basaltwater-v1\n")
            old.rename(recovery / "previous-runtime")
            new = Path(action["new"])
            stage.rename(new)
            if plan["system"]:
                (new / "state").symlink_to(Path(plan["root"]) / "var/lib/basaltwater")
        plan["completed"] = index + 1
        plan.pop("pending", None)
        _save(plan)
    if plan["units"]:
        _systemctl(plan, "daemon-reload")
    _reload_integrations(plan)
    for unit in plan["units"]:
        if unit["enabled"] and unit["old"] != unit["new"]:
            _systemctl(plan, "enable", unit["new"])
        if unit["active"]:
            _systemctl(plan, "start", unit["new"])
    plan["status"] = "complete"
    _save(plan)


def recover(root: Path, *, system: bool) -> None:
    """Reverse an interrupted cutover using its private, owner-only journal."""
    recovery = root / ("var/lib/basaltwater-migration" if system else ".local/state/basaltwater-migration")
    journal = recovery / "journal.json"
    _safe(journal)
    for path in (journal, recovery):
        if path.is_symlink() or path.stat().st_uid != os.geteuid() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError(f"Unsafe recovery journal: {path}")
    plan = json.loads(journal.read_text())
    if plan["root"] != str(root) or plan["system"] != system or plan["recovery"] != str(recovery):
        raise ValueError("Recovery journal does not match this migration root")
    if plan["status"] in ("complete", "recovered"):
        raise ValueError("Only an interrupted migration can be recovered")
    for unit in plan["units"]:
        _systemctl(plan, "stop", unit["new"], check=False)
        if unit["old"] != unit["new"]:
            _systemctl(plan, "disable", unit["new"], check=False)
    count = max(plan["completed"], plan.get("pending", -1) + 1)
    for index in reversed(range(count)):
        action = plan["actions"][index]
        old = Path(action["old"])
        _safe(old)
        kind = action["kind"]
        if kind == "move":
            new = Path(action["new"])
            _safe(new)
            if not os.path.lexists(old) and os.path.lexists(new):
                old.parent.mkdir(parents=True, exist_ok=True)
                new.rename(old)
        elif kind == "rmdir" and not old.exists():
            old.mkdir(mode=action["mode"], parents=True)
            os.chown(old, action["uid"], action["gid"])
        elif kind == "edit":
            _write_bytes(action, "before")
        elif kind == "chmod":
            old.chmod(action["before"])
        elif kind == "link":
            new = Path(action["new"])
            if new.is_symlink():
                new.unlink()
            if not os.path.lexists(old):
                old.symlink_to(action["before"])
        elif kind == "retire":
            saved = recovery / f"retired-{index}"
            if os.path.lexists(saved) and not os.path.lexists(old):
                saved.rename(old)
        elif kind == "runtime":
            previous = recovery / "previous-runtime"
            new = Path(action["new"])
            if previous.exists():
                if new.exists():
                    new.rename(recovery / "failed-runtime")
                previous.rename(old)
        plan["completed"] = index
        plan.pop("pending", None)
        _save(plan)
    for home in reversed(plan.get("homes", [])):
        try:
            account = pwd.getpwnam(home["user"])
        except KeyError:
            continue
        if account.pw_dir == home["after"]:
            subprocess.run(["usermod", "--home", home["before"], home["user"]], check=True)
    for old, new in reversed(plan.get("accounts", [])):
        try:
            pwd.getpwnam(new)
        except KeyError:
            continue
        subprocess.run(["usermod", "--login", old, new], check=True)
        try:
            grp.getgrnam(new)
        except KeyError:
            pass
        else:
            subprocess.run(["groupmod", "--new-name", old, new], check=True)
    for old, new in reversed(plan.get("groups", [])):
        try:
            grp.getgrnam(new)
        except KeyError:
            continue
        subprocess.run(["groupmod", "--new-name", old, new], check=True)
    if plan["units"]:
        _systemctl(plan, "daemon-reload")
    for unit in plan["units"]:
        if unit.get("enabled") and unit["old"] != unit["new"]:
            _systemctl(plan, "enable", unit["old"])
        if unit.get("active"):
            _systemctl(plan, "start", unit["old"])
    _reload_integrations(plan)
    plan["status"] = "recovered"
    _save(plan)


def migrate(*, system: bool, apply: bool, recovery: bool = False, installation: str | None = None) -> int:
    """CLI boundary: preview unless applying was explicitly requested."""
    root = Path("/") if system else Path.home()
    if system and (apply or recovery) and os.geteuid() != 0:
        print("System migration requires root")
        return 1
    try:
        if recovery:
            recover(root, system=system)
            print("Interrupted migration recovered; archive the journal before retrying")
            return 0
        if installation is not None:
            validate_filesystem_path(installation, must_exist=True)
        plan = build_plan(root, system=system, runtime_source=Path(__file__).resolve().parents[1], installation=Path(installation) if installation else None)
        for action in plan["actions"]:
            print(f"{action['kind']}: {action['old']}" + (f" -> {action['new']}" if "new" in action else ""))
        if apply and plan["actions"]:
            apply_plan(plan)
            print(f"Migration complete; private recovery journal: {plan['recovery']}")
        elif not apply:
            print("Preview only; add --apply to perform the cutover")
        else:
            print("No legacy installation data found")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Migration stopped: {exc}; preserve the migration journal before retrying")
        return 1
