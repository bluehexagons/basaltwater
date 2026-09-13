"""Deployment orchestration - shared between local and remote environments."""

from __future__ import annotations
import hashlib
import fcntl
import json
import os
import shlex
import shutil
import re
import socket
import sqlite3
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable, Optional

from lib.remote_utils import run
from lib.local_http import open_loopback
from lib.operation_state import OperationRecord, OperationStateStore
from lib.update_policy import npm_freshness_args
from lib.deploy_utils import (
    create_safe_directory_name,
    detect_project_type,
    get_project_root,
    save_deployment_metadata,
    should_redeploy
)
from lib.systemd_service import cleanup_service, create_managed_service
from lib.project_manifest import Component, Manifest, has_placeholder, load_manifest, render_template
from lib.validation import validate_filesystem_path


class DeploymentOrchestrator:
    
    def __init__(self, base_dir: str = "/var/www", deploy_user: str = "web-deploy", deploy_group: str = "web-deploy"):
        self.base_dir = base_dir
        self.deploy_user = deploy_user
        self.deploy_group = deploy_group

    def _get_persistent_root(self, app_name: str) -> str:
        return os.path.join(self.base_dir, ".infra_tools_shared", app_name)
    
    def _ensure_dir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def _get_command_error(self, result: Any, fallback: str) -> str:
        """Extract a concise error message from a command result."""
        stderr = getattr(result, 'stderr', '') or ''
        stdout = getattr(result, 'stdout', '') or ''
        output = stderr.strip() or stdout.strip() or fallback
        return output[:500]

    def _get_used_ports(self, exclude_services: Optional[set[str]] = None) -> set[int]:
        """Get set of ports currently used by infra_tools services."""
        used_ports: set[int] = set()
        excluded = exclude_services or set()
        try:
            if not os.path.exists("/etc/systemd/system"):
                return used_ports
                
            files = os.listdir("/etc/systemd/system")
            for f in files:
                service_name = f.removesuffix(".service")
                if (
                    service_name not in excluded
                    and (f.startswith("node-") or f.startswith("app-"))
                    and f.endswith(".service")
                ):
                    path = os.path.join("/etc/systemd/system", f)
                    try:
                        with open(path, 'r') as service_file:
                            content = service_file.read()
                            match = re.search(r'-p (\d+)', content)
                            if match:
                                used_ports.add(int(match.group(1)))
                            match = re.search(r'--port (\d+)', content)
                            if match:
                                used_ports.add(int(match.group(1)))
                            for match in re.finditer(r'(?:PORT=|LISTEN_ADDR=[^\n]*:|GOCLICK_ADDR=[^\n]*:)(\d+)', content):
                                used_ports.add(int(match.group(1)))
                    except OSError:
                        pass
        except OSError:
            pass
        return used_ports

    def _find_free_port(self, start_port: int, reserved: Optional[set[int]] = None) -> int:
        """Find the first free port starting from start_port."""
        used_ports = self._get_used_ports()
        used_ports.update(reserved or set())
        port = start_port
        while port < 65535:
            if port in used_ports:
                port += 1
                continue
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(('127.0.0.1', port))
                    return port
                except OSError:
                    port += 1
        raise RuntimeError("No free ports available")

    def _capped_identity(self, prefix: str, raw_name: str) -> str:
        base = f"{prefix}-{self._sanitize_user_part(raw_name)}"
        if len(base) <= 31:
            return base
        digest = hashlib.sha1(base.encode()).hexdigest()[:8]
        return f"{base[:22].rstrip('-_')}-{digest}"

    def get_deployment_path(self, domain: Optional[str], path: str, git_url: str) -> str:
        dir_name = create_safe_directory_name(domain, path)
        
        repo_name = git_url.rstrip('/').split('/')[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        
        if dir_name:
            return os.path.join(self.base_dir, dir_name)
        else:
            return os.path.join(self.base_dir, repo_name)
    
    def deploy_from_archive(self, source_path: str, domain: Optional[str], path: str,
                           git_url: str, commit_hash: Optional[str],
                           full_deploy: bool = True, keep_source: bool = False) -> dict[str, Any]:
        """Deploy a non-manifest static/Node tree through an atomic release swap."""
        del keep_source
        from lib.deploy_utils import is_ruby_project

        dest_path = self.get_deployment_path(domain, path, git_url)
        if is_ruby_project(source_path) or (
            os.path.isdir(dest_path) and is_ruby_project(dest_path)
        ):
            raise RuntimeError(
                "Refusing to modify a Ruby/Rails deployment with this infra-tools "
                "version; use its pinned legacy release"
            )

        if not should_redeploy(dest_path, git_url, commit_hash, full_deploy):
            project_type = detect_project_type(dest_path)
            print(f"Skipping {dest_path} (already at commit {commit_hash})...")
            return {
                'dest_path': dest_path,
                'domain': domain,
                'path': path,
                'project_type': project_type,
                'serve_path': get_project_root(dest_path, project_type),
                'needs_proxy': False,
                'backend_port': None,
                'skipped': True,
            }

        parent_dir = os.path.dirname(dest_path)
        os.makedirs(parent_dir, exist_ok=True)
        staging_path = tempfile.mkdtemp(
            prefix=f".{os.path.basename(dest_path)}.build-",
            dir=parent_dir,
        )
        backup_path: Optional[str] = None
        try:
            shutil.copytree(source_path, staging_path, dirs_exist_ok=True)
            project_type = detect_project_type(staging_path)
            print(f"Deploying {project_type} project to {dest_path}...")

            site_root = path or "/"
            if not site_root.startswith("/"):
                site_root = f"/{site_root}"
            if not site_root.endswith("/"):
                site_root = f"{site_root}/"
            self.build_project(staging_path, project_type, site_root=site_root)

            result = run(
                f"chown -R {shlex.quote(self.deploy_user)}:{shlex.quote(self.deploy_group)} "
                f"{shlex.quote(staging_path)}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Could not set release ownership to {self.deploy_user}:{self.deploy_group}"
                )
            result = run(
                f"chmod -R u=rwX,go=rX {shlex.quote(staging_path)}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not set release permissions for {dest_path}")

            if os.path.exists(dest_path):
                backup_path = tempfile.mkdtemp(
                    prefix=f".{os.path.basename(dest_path)}.previous-",
                    dir=parent_dir,
                )
                os.rmdir(backup_path)
                os.rename(dest_path, backup_path)
            try:
                os.rename(staging_path, dest_path)
            except Exception:
                if backup_path and os.path.exists(backup_path) and not os.path.exists(dest_path):
                    os.rename(backup_path, dest_path)
                raise
            staging_path = ""

            save_deployment_metadata(dest_path, git_url, commit_hash)
            if backup_path:
                shutil.rmtree(backup_path)
                backup_path = None
            print(f"  ✓ Repository deployed to {dest_path}")
            return {
                'dest_path': dest_path,
                'domain': domain,
                'path': path,
                'project_type': project_type,
                'serve_path': get_project_root(dest_path, project_type),
                'needs_proxy': False,
                'backend_port': None,
            }
        finally:
            if staging_path and os.path.exists(staging_path):
                shutil.rmtree(staging_path)
    
    # ------------------------------------------------------------------
    # infra.json manifest deploys
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_user_part(text: str) -> str:
        """Reduce arbitrary text to a Linux user/unit-name-safe fragment."""
        safe = re.sub(r"[^a-z0-9_-]", "-", text.lower())
        return safe.strip("-_") or "x"

    def _service_identity(self, dest_path: str, component: Component) -> tuple[str, str]:
        """Return (systemd_unit_name, service_username) for a service component.

        Keyed by both the release dir name and the component name so it is unique
        across repos on one host (two repos can each have a component named
        'api'). The systemd unit name has no practical length limit; the Linux
        username is capped to a valid length, falling back to a short hash suffix
        when truncation is needed so it stays deterministic and collision-free.
        """
        app_name = os.path.basename(dest_path.rstrip("/"))
        base = f"app-{self._sanitize_user_part(app_name)}-{component.name}"
        unit_name = base
        if len(base) <= 31:
            username = base
        else:
            digest = hashlib.sha1(base.encode()).hexdigest()[:8]
            username = f"{base[:22].rstrip('-_')}-{digest}"
        return unit_name, username

    def _component_shared_dir(self, dest_path: str, component: Component) -> str:
        """infra_tools-managed persistent dir for a service component.

        Lives outside the release dir (under the same .infra_tools_shared root as
        application state) so it survives the release being replaced on each deploy.
        """
        app_name = os.path.basename(dest_path.rstrip("/"))
        return os.path.join(self._get_persistent_root(app_name), component.name)

    def _component_data_dir(self, dest_path: str, component: Component) -> str:
        return os.path.join(self._component_shared_dir(dest_path, component), "data")

    def _secure_component_shared_dir(self, component: Component, shared_dir: str) -> None:
        """Make a stable component boundary root-controlled before using children."""
        if os.path.islink(shared_dir):
            raise RuntimeError(
                f"Component '{component.name}': refusing symlinked shared directory: "
                f"{shared_dir}"
            )
        result = run(
            f"chown root:root {shlex.quote(shared_dir)} && chmod 0711 {shlex.quote(shared_dir)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Component '{component.name}': could not secure managed shared directory"
            )

    def _ensure_service_user(self, username: str) -> None:
        """Create a locked-down --system, nologin user for a service (idempotent)."""
        result = run(f"id {shlex.quote(username)}", check=False)
        if result.returncode == 0:
            return
        print(f"  Creating service user: {username}")
        result = run(
            f"useradd --system --no-create-home --shell /usr/sbin/nologin {shlex.quote(username)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not create service user {username}")

    def _build_identity(self, dest_path: str) -> str:
        return self._capped_identity("build", os.path.basename(dest_path.rstrip("/")))

    def _ensure_build_user(self, username: str) -> None:
        result = run(f"id {shlex.quote(username)}", check=False)
        if result.returncode == 0:
            return
        home_dir = os.path.join("/var/lib/infra_tools/build-users", username)
        result = run(
            "useradd --system --user-group --create-home "
            f"--home-dir {shlex.quote(home_dir)} --shell /usr/sbin/nologin {shlex.quote(username)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not create build user {username}")

    @staticmethod
    def _build_home(username: str) -> str:
        return os.path.join("/var/lib/infra_tools/build-users", username)

    def _port_state_path(self, dest_path: str) -> str:
        app_name = os.path.basename(dest_path.rstrip("/"))
        return os.path.join(self._get_persistent_root(app_name), "manifest-ports.json")

    def _load_manifest_ports(self, dest_path: str) -> dict[str, int]:
        try:
            with open(self._port_state_path(dest_path), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, int) and 1024 <= value <= 65535
        }

    def _save_manifest_ports(self, dest_path: str, manifest: Manifest) -> None:
        assignments = {
            component.name: component.port
            for component in manifest.components
            if component.is_service and component.port is not None
        }
        state_path = self._port_state_path(dest_path)
        self._ensure_dir(os.path.dirname(state_path))
        temporary_path = f"{state_path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(assignments, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, state_path)

    def _resolve_manifest_ports(self, manifest: Manifest, dest_path: str) -> Manifest:
        stored = self._load_manifest_ports(dest_path)
        own_services = {
            self._service_identity(dest_path, component)[0]
            for component in manifest.components
            if component.is_service
        }
        unavailable = self._get_used_ports(exclude_services=own_services)
        reserved: set[int] = set()
        resolved: list[Component] = []
        for component in manifest.components:
            if not component.is_service:
                resolved.append(component)
                continue
            port = component.port
            if port is None:
                candidate = stored.get(component.name)
                if candidate is not None and candidate not in unavailable and candidate not in reserved:
                    port = candidate
                else:
                    port = self._find_free_port(8000, unavailable | reserved)
            elif port in unavailable or port in reserved:
                raise RuntimeError(
                    f"Component '{component.name}' cannot use port {port}; it is already assigned"
                )
            reserved.add(port)
            resolved.append(replace(component, port=port))
        return Manifest(version=manifest.version, components=resolved)

    def _app_unit_snapshots(self, dest_path: str) -> dict[str, str]:
        app_fragment = self._sanitize_user_part(os.path.basename(dest_path.rstrip("/")))
        prefix = f"app-{app_fragment}-"
        snapshots: dict[str, str] = {}
        systemd_dir = "/etc/systemd/system"
        if not os.path.isdir(systemd_dir):
            return snapshots
        for filename in os.listdir(systemd_dir):
            if not filename.startswith(prefix) or not filename.endswith(".service"):
                continue
            try:
                with open(os.path.join(systemd_dir, filename), "r", encoding="utf-8") as handle:
                    snapshots[filename.removesuffix(".service")] = handle.read()
            except OSError as exc:
                raise RuntimeError(
                    f"Could not snapshot managed service unit {filename}: {exc}"
                ) from exc
        return snapshots

    def _restore_app_units(self, dest_path: str, snapshots: dict[str, str]) -> None:
        current = self._app_unit_snapshots(dest_path)
        for service_name in set(current) - set(snapshots):
            cleanup_service(service_name)
        for service_name, content in snapshots.items():
            with open(f"/etc/systemd/system/{service_name}.service", "w", encoding="utf-8") as handle:
                handle.write(content)
        run("systemctl daemon-reload")
        for service_name in snapshots:
            run(f"systemctl enable {shlex.quote(service_name)}.service")
        self._restart_app_units(snapshots)

    @staticmethod
    def _unit_command(unit_name: str, action: str) -> str:
        return f"systemctl {action} {shlex.quote(unit_name)}.service"

    def _stop_app_unit(self, unit_name: str) -> bool:
        """Stop one active unit and verify it became inactive."""

        status = run(
            self._unit_command(unit_name, "is-active --quiet"),
            check=False,
            capture_output=True,
        )
        if status.returncode in {3, 4}:
            return False
        if status.returncode != 0:
            raise RuntimeError(f"Could not determine whether {unit_name}.service is active")

        result = run(
            self._unit_command(unit_name, "stop"),
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            error = self._get_command_error(result, "systemctl stop failed")
            raise RuntimeError(f"Could not stop {unit_name}.service: {error}")
        status = run(
            self._unit_command(unit_name, "is-active --quiet"),
            check=False,
            capture_output=True,
        )
        if status.returncode == 0:
            raise RuntimeError(f"Unit remained active after stop: {unit_name}.service")
        if status.returncode not in {3, 4}:
            raise RuntimeError(f"Could not verify that {unit_name}.service stopped")
        return True

    def _restart_app_units(self, unit_names: Iterable[str]) -> None:
        """Restart units and verify every restored process is active."""

        errors: list[str] = []
        for unit_name in sorted(unit_names):
            result = run(
                self._unit_command(unit_name, "restart"),
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                error = self._get_command_error(result, "systemctl restart failed")
                errors.append(f"{unit_name}.service restart failed: {error}")
                continue
            status = run(
                self._unit_command(unit_name, "is-active --quiet"),
                check=False,
                capture_output=True,
            )
            if status.returncode != 0:
                errors.append(f"{unit_name}.service is not active after restart")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _backup_component_sqlite(
        self,
        component: Component,
        dest_path: str,
        deploy_domain: Optional[str],
    ) -> None:
        if not component.sqlite_backup:
            return
        context = self._service_context(component, dest_path, deploy_domain)
        shared_dir = context["shared_dir"]
        if not os.path.isdir(shared_dir):
            return
        self._secure_component_shared_dir(component, shared_dir)
        if os.path.islink(context["data_dir"]):
            raise RuntimeError(
                f"Component '{component.name}': refusing symlinked data directory: "
                f"{context['data_dir']}"
            )
        rendered_database_path = render_template(component.sqlite_backup, context)
        validate_filesystem_path(rendered_database_path, must_exist=False)
        database_path = os.path.realpath(rendered_database_path)
        managed_root = os.path.realpath(context["data_dir"])
        try:
            inside_managed_root = os.path.commonpath((database_path, managed_root)) == managed_root
        except ValueError:
            inside_managed_root = False
        if not inside_managed_root:
            raise RuntimeError(
                f"Component '{component.name}': sqlite_backup must resolve inside "
                f"its managed data directory: {managed_root}"
            )
        if not os.path.isfile(database_path):
            return
        backup_dir = os.path.join(shared_dir, "backups")
        if os.path.islink(backup_dir):
            raise RuntimeError(
                f"Component '{component.name}': refusing symlinked backup directory: "
                f"{backup_dir}"
            )
        self._ensure_dir(backup_dir)
        result = run(
            f"chown root:root {shlex.quote(backup_dir)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Component '{component.name}': could not secure SQLite backup ownership"
            )
        result = run(
            f"chmod 0700 {shlex.quote(backup_dir)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Component '{component.name}': could not secure SQLite backup permissions"
            )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = os.path.join(backup_dir, f"{component.name}_{stamp}.sqlite3")
        temporary_path = f"{backup_path}.tmp"
        temporary_created = False
        try:
            # Reserve the unpredictable temporary name without following a
            # pre-existing link left by a service from an older, less-isolated
            # deployment. SQLite can then safely reopen this regular file.
            temporary_fd = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            temporary_created = True
            os.close(temporary_fd)
            source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
            try:
                target = sqlite3.connect(temporary_path)
                try:
                    source.backup(target)
                    integrity = target.execute("PRAGMA quick_check").fetchone()
                    if integrity != ("ok",):
                        detail = integrity[0] if integrity else "no result"
                        raise sqlite3.DatabaseError(
                            f"SQLite backup integrity check failed: {detail}"
                        )
                finally:
                    target.close()
            finally:
                source.close()
        except (OSError, sqlite3.Error):
            if temporary_created:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
            raise
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, backup_path)
        backups = sorted(
            (
                os.path.join(backup_dir, name)
                for name in os.listdir(backup_dir)
                if name.startswith(f"{component.name}_") and name.endswith(".sqlite3")
            ),
            key=os.path.getmtime,
            reverse=True,
        )
        for old_backup in backups[component.backup_retention:]:
            os.remove(old_backup)
        print(f"  ✓ Backed up SQLite database to {backup_path}")

    def deploy_manifest(self, manifest: Manifest, source_path: str, domain: Optional[str],
                        path: str, git_url: str, commit_hash: Optional[str],
                        keep_source: bool = False) -> list[dict[str, Any]]:
        """Deploy every component of an infra.json manifest.

        Returns one nginx "dep" descriptor per component. Components declare
        their own domains, so a single repo can serve a static apex site and a
        reverse-proxied API subdomain from one deploy. This always performs a
        full build; incremental skip (mirroring should_redeploy) is a future
        optimization, kept out for a small, auditable surface.
        """
        dest_path = self.get_deployment_path(domain, path, git_url)
        parent_dir = os.path.dirname(dest_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        shared_root = os.path.join(self.base_dir, ".infra_tools_shared")
        self._ensure_dir(shared_root)
        lock_handle = open(
            os.path.join(shared_root, ".manifest-deploy.lock"),
            "a+",
            encoding="utf-8",
        )
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        staging_path = ""
        backup_path: Optional[str] = None
        activated = False
        stopped_units: list[str] = []
        unit_snapshots: dict[str, str] = {}
        desired_units: set[str] = set()
        operation_store = OperationStateStore(
            os.path.join(
                self._get_persistent_root(os.path.basename(dest_path)),
                "manifest-operation.json",
            )
        )
        operation: Optional[OperationRecord] = None
        try:
            operation = operation_store.begin(
                "manifest_deploy",
                dest_path,
                "preparing",
                context={"commit": commit_hash or "unknown"},
            )
            manifest = self._resolve_manifest_ports(manifest, dest_path)
            self._validate_manifest_routes(manifest, domain)
            print(f"Deploying manifest ({len(manifest.components)} component(s)) to {dest_path}...")
            build_user = self._build_identity(dest_path)
            self._ensure_build_user(build_user)
            unit_snapshots = self._app_unit_snapshots(dest_path)
            desired_units = {
                self._service_identity(dest_path, component)[0]
                for component in manifest.components
                if component.is_service
            }

            # Build beside the active release. A failed build therefore leaves
            # the currently served release in place and does not interrupt it.
            staging_path = tempfile.mkdtemp(
                prefix=f".{os.path.basename(dest_path)}.build-",
                dir=parent_dir or None,
            )
            operation = operation_store.transition(
                operation.operation_id,
                "building",
                context={
                    "commit": commit_hash or "unknown",
                    "staging_path": staging_path,
                },
            )
            if keep_source:
                shutil.copytree(source_path, staging_path, dirs_exist_ok=True)
                print(f"  ✓ Copied source to staging path {staging_path}")
            else:
                shutil.copytree(source_path, staging_path, dirs_exist_ok=True)
                print(f"  ✓ Copied source to staging path {staging_path}")

            # Build as an application-specific non-root identity.
            result = run(
                f"chown -R {shlex.quote(build_user)}:{shlex.quote(build_user)} {shlex.quote(staging_path)}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not assign staging tree to build user {build_user}")

            self._prepare_build_toolchain(staging_path, build_user)

            # Build every component before touching the active release.
            for component in manifest.components:
                component_domain = self._component_domain(component, domain)
                print(f"  Component '{component.name}' ({component.type}) → {component_domain}{component.path}")
                self._run_component_build(component, staging_path, build_user)

            self._validate_manifest_artifacts(manifest, staging_path)

            if os.path.exists(dest_path):
                backup_path = tempfile.mkdtemp(
                    prefix=f".{os.path.basename(dest_path)}.previous-",
                    dir=parent_dir or None,
                )
                os.rmdir(backup_path)
            operation = operation_store.transition(
                operation.operation_id,
                "activating",
                context={
                    "commit": commit_hash or "unknown",
                    "staging_path": staging_path,
                    "backup_path": backup_path or "",
                    "units": sorted(unit_snapshots),
                },
            )

            # Quiesce the old application before a privileged database open so
            # it cannot race path validation by replacing a database entry.
            for unit_name in sorted(unit_snapshots):
                try:
                    if self._stop_app_unit(unit_name):
                        stopped_units.append(unit_name)
                except RuntimeError:
                    # A failed stop or status check can leave the unit's state
                    # uncertain. Reconcile it against the unchanged release.
                    stopped_units.append(unit_name)
                    raise

            for component in manifest.components:
                if component.is_service:
                    self._backup_component_sqlite(component, dest_path, domain)

            if os.path.exists(dest_path):
                assert backup_path is not None
                os.rename(dest_path, backup_path)
            try:
                os.rename(staging_path, dest_path)
            except Exception:
                if backup_path and os.path.exists(backup_path) and not os.path.exists(dest_path):
                    os.rename(backup_path, dest_path)
                raise
            staging_path = ""
            activated = True
            operation = operation_store.transition(
                operation.operation_id,
                "verifying",
                context={
                    "commit": commit_hash or "unknown",
                    "backup_path": backup_path or "",
                    "units": sorted(desired_units),
                },
            )

            print(f"  ✓ Activated release at {dest_path}")

            # Describe the activated release for nginx.
            deps: list[dict[str, Any]] = [
                self._component_dep(component, dest_path, domain)
                for component in manifest.components
                if component.is_static or component.reverse_proxy
            ]

            # The release tree is deploy-owned and world-readable so nginx can
            # serve static files and each service user can read and exec its
            # binary. Service writable state lives outside the release.
            result = run(
                f"chown -R {shlex.quote(self.deploy_user)}:{shlex.quote(self.deploy_group)} {shlex.quote(dest_path)}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Could not set release ownership to {self.deploy_user}:{self.deploy_group}"
                )
            result = run(
                f"chmod -R u=rwX,go=rX {shlex.quote(dest_path)}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not set release permissions for {dest_path}")

            # Start services after the release tree is readable so they can exec.
            for component in manifest.components:
                if component.is_service:
                    self._install_service_component(component, dest_path, domain)

            for stale_unit in sorted(set(unit_snapshots) - desired_units):
                cleanup_service(stale_unit)

            self._save_manifest_ports(dest_path, manifest)
            save_deployment_metadata(dest_path, git_url, commit_hash)
            if not keep_source and os.path.exists(source_path):
                shutil.rmtree(source_path)
            operation_store.complete(operation.operation_id)
            operation = None
            if backup_path:
                shutil.rmtree(backup_path)
                backup_path = None
            print(f"  ✓ Manifest deployed to {dest_path}")
            return deps
        except Exception as deployment_error:
            rollback_errors: list[str] = []
            if activated:
                for unit_name in sorted(desired_units):
                    try:
                        self._stop_app_unit(unit_name)
                    except RuntimeError as exc:
                        rollback_errors.append(str(exc))
                failed_path = tempfile.mkdtemp(
                    prefix=f".{os.path.basename(dest_path)}.failed-",
                    dir=parent_dir or None,
                )
                os.rmdir(failed_path)
                if os.path.exists(dest_path):
                    os.rename(dest_path, failed_path)
                if backup_path and os.path.exists(backup_path):
                    os.rename(backup_path, dest_path)
                    backup_path = None
                shutil.rmtree(failed_path, ignore_errors=True)
                try:
                    self._restore_app_units(dest_path, unit_snapshots)
                except RuntimeError as exc:
                    rollback_errors.append(str(exc))
                if not rollback_errors:
                    print("  ✓ Restored previous release after failed activation")
            elif stopped_units:
                try:
                    self._restart_app_units(stopped_units)
                except RuntimeError as exc:
                    rollback_errors.append(str(exc))
            if rollback_errors:
                if operation is not None:
                    operation_store.transition(
                        operation.operation_id,
                        "recovery",
                        status="recovery_required",
                        context={
                            "backup_path": backup_path or "",
                            "errors": rollback_errors,
                            "units": sorted(set(unit_snapshots) | desired_units),
                        },
                    )
                raise RuntimeError(
                    "Deployment failed and service recovery was incomplete: "
                    + "; ".join(rollback_errors)
                ) from deployment_error
            if operation is not None:
                operation_store.complete(operation.operation_id)
                operation = None
            raise
        finally:
            if staging_path and os.path.exists(staging_path):
                shutil.rmtree(staging_path)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    def _run_component_build(
        self,
        component: Component,
        dest_path: str,
        build_user: Optional[str] = None,
    ) -> None:
        """Run build commands as the application-specific build account."""
        if not component.build:
            return
        env_prefix = "".join(f"{key}={shlex.quote(value)} " for key, value in component.env.items())
        for command in component.build:
            print(f"    build: {command}")
            build_home = self._build_home(build_user or self.deploy_user)
            path_entries = [os.path.join(build_home, ".local", "bin"), "/usr/local/go/bin"]
            shell_parts = [
                f"export PATH={shlex.quote(':'.join(path_entries))}:$PATH",
            ]
            nvm_dir = os.path.join(build_home, ".nvm")
            nvm_script = os.path.join(nvm_dir, "nvm.sh")
            if os.path.isfile(nvm_script):
                shell_parts.extend((
                    f"export NVM_DIR={shlex.quote(nvm_dir)}",
                    f". {shlex.quote(nvm_script)}",
                ))
            shell_parts.extend((
                f"cd {shlex.quote(dest_path)}",
                f"{env_prefix}{command}",
            ))
            build_shell = " && ".join(shell_parts)
            result = run(
                f"runuser -u {shlex.quote(build_user or self.deploy_user)} -- "
                f"env HOME={shlex.quote(build_home)} /bin/bash -lc {shlex.quote(build_shell)}",
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                error = self._get_command_error(result, "build command failed")
                raise RuntimeError(f"Component '{component.name}' build failed: {error}")

    def _prepare_build_toolchain(self, source_path: str, build_user: str) -> None:
        """Provision repository-declared user-scoped build tools once per app."""
        build_home = self._build_home(build_user)
        if self._source_has_marker(source_path, {"package.json"}):
            nvm_script = os.path.join(build_home, ".nvm", "nvm.sh")
            if not os.path.isfile(nvm_script):
                from common.common_steps import install_node_for_user

                install_node_for_user(build_user, build_home)
            if not os.path.isfile(nvm_script):
                raise RuntimeError(f"Node.js toolchain setup failed for {build_user}")
            if os.path.isfile(os.path.join(source_path, ".nvmrc")):
                nvm_dir = os.path.join(build_home, ".nvm")
                script = " && ".join((
                    f"export NVM_DIR={shlex.quote(nvm_dir)}",
                    f". {shlex.quote(nvm_script)}",
                    f"cd {shlex.quote(source_path)}",
                    "nvm install",
                ))
                result = run(
                    f"runuser -u {shlex.quote(build_user)} -- "
                    f"env HOME={shlex.quote(build_home)} /bin/bash -lc {shlex.quote(script)}",
                    check=False,
                    capture_output=True,
                )
                if result.returncode != 0:
                    error = self._get_command_error(result, "nvm install failed")
                    raise RuntimeError(f"Node.js version setup failed for {build_user}: {error}")

        if self._source_has_marker(
            source_path,
            {"pyproject.toml", "requirements.txt", "uv.lock"},
        ):
            uv_path = os.path.join(build_home, ".local", "bin", "uv")
            if not os.path.isfile(uv_path):
                from common.common_steps import install_or_update_uv

                if not install_or_update_uv(build_home, username=build_user):
                    raise RuntimeError(f"uv toolchain setup failed for {build_user}")

    @staticmethod
    def _source_has_marker(source_path: str, markers: set[str]) -> bool:
        ignored = {".git", ".infra_tools", ".venv", "node_modules", "vendor"}
        for _current_dir, directories, filenames in os.walk(source_path):
            directories[:] = [name for name in directories if name not in ignored]
            if markers.intersection(filenames):
                return True
        return False

    @staticmethod
    def _validate_manifest_artifacts(manifest: Manifest, staging_path: str) -> None:
        """Reject incomplete builds before stopping the active application."""
        for component in manifest.components:
            if component.is_static:
                output_path = os.path.join(staging_path, component.output or "")
                if not os.path.isdir(output_path):
                    raise RuntimeError(
                        f"Component '{component.name}': static output not found after build: "
                        f"{output_path}"
                    )
                if component.type == "godot-web":
                    from lib.godot_deploy import validate_godot_export
                    validate_godot_export(output_path, staging_path)
                continue
            if not component.binary:
                continue
            binary_path = os.path.join(staging_path, component.binary)
            if not os.path.isfile(binary_path):
                raise RuntimeError(
                    f"Component '{component.name}': binary not found after build: {binary_path}"
                )
            if not os.access(binary_path, os.X_OK):
                raise RuntimeError(
                    f"Component '{component.name}': binary is not executable: {binary_path}"
                )

    def _component_domain(self, component: Component, deploy_domain: Optional[str]) -> str:
        if not has_placeholder(component.domain):
            return component.domain
        if not deploy_domain:
            raise RuntimeError(
                f"Component '{component.name}' domain uses {{domain}}, but no deploy domain was provided"
            )
        return render_template(component.domain, {'domain': deploy_domain})

    def _validate_manifest_routes(
        self,
        manifest: Manifest,
        deploy_domain: Optional[str],
    ) -> None:
        """Reject components that would generate the same Nginx location."""
        routes: dict[tuple[str, str], str] = {}
        for component in manifest.components:
            if component.is_service and not component.reverse_proxy:
                continue
            route = (self._component_domain(component, deploy_domain), component.path.rstrip("/") or "/")
            previous = routes.get(route)
            if previous:
                raise RuntimeError(
                    f"Components '{previous}' and '{component.name}' both declare "
                    f"{route[0]}{route[1]}"
                )
            routes[route] = component.name

    def _component_dep(self, component: Component, dest_path: str,
                       deploy_domain: Optional[str] = None) -> dict[str, Any]:
        """Translate a component into an nginx deployment descriptor."""
        component_domain = self._component_domain(component, deploy_domain)
        dep: dict[str, Any] = {
            'dest_path': dest_path,
            'domain': component_domain,
            'path': component.path,
        }
        if component.is_static:
            serve_path = os.path.normpath(os.path.join(dest_path, component.output or ""))
            dep.update(
                project_type=component.type,
                needs_proxy=False,
                serve_path=serve_path,
                backend_port=None,
            )
            return dep
        # service: nginx reverse-proxies the component's domain to its loopback port
        dep.update(
            project_type='service',
            needs_proxy=True,
            serve_path=dest_path,
            backend_port=component.port,
            proxy_port=component.port,
            preserve_path=True,
        )
        return dep

    def _service_context(self, component: Component, dest_path: str,
                         deploy_domain: Optional[str] = None) -> dict[str, str]:
        """Build the deploy-time {{...}} substitution context for a service.

        Variables are resolved in dependency order (binary, then working_dir,
        then env_file) so later fields can reference earlier values.
        """
        unit_name, username = self._service_identity(dest_path, component)
        context: dict[str, str] = {
            'release_dir': dest_path,
            'base_dir': self.base_dir,
            'name': component.name,
            'service_name': unit_name,
            'domain': self._component_domain(component, deploy_domain),
            'path': component.path,
            # For a service, the user is its own dedicated, isolated identity.
            'web_user': username,
            'web_group': username,
            'port': str(component.port) if component.port is not None else '',
            'shared_dir': self._component_shared_dir(dest_path, component),
            'data_dir': self._component_data_dir(dest_path, component),
        }
        if component.binary:
            resolved = render_template(component.binary, context)
            context['binary'] = os.path.normpath(os.path.join(dest_path, resolved))
        context['working_dir'] = self._resolve_working_dir(component, dest_path, context)
        if component.env_file:
            context['env_file'] = render_template(component.env_file, context)
        return context

    def _resolve_working_dir(self, component: Component, dest_path: str,
                             context: dict[str, str]) -> str:
        working_dir = component.working_dir
        if not working_dir:
            return dest_path
        working_dir = render_template(working_dir, context)
        if os.path.isabs(working_dir):
            return working_dir
        return os.path.normpath(os.path.join(dest_path, working_dir))

    def _install_service_component(self, component: Component, dest_path: str,
                                   deploy_domain: Optional[str] = None) -> None:
        context = self._service_context(component, dest_path, deploy_domain)
        runtime_env = {
            key: render_template(value, context)
            for key, value in component.runtime_env.items()
        }
        username = context['web_user']
        service_name = context['service_name']

        # Verify the built artifact exists before wiring a unit around it.
        if component.binary:
            binary_path = context['binary']
            if not os.path.exists(binary_path):
                raise RuntimeError(
                    f"Component '{component.name}': binary not found after build: {binary_path}"
                )

        # The service owns only data_dir. shared_dir remains root-controlled so
        # sibling infrastructure state such as backups cannot be changed by the
        # application process.
        self._ensure_service_user(username)
        data_dir = context['data_dir']
        shared_dir = context['shared_dir']
        self._ensure_dir(shared_dir)
        self._secure_component_shared_dir(component, shared_dir)
        if os.path.islink(data_dir):
            raise RuntimeError(
                f"Component '{component.name}': refusing symlinked data directory: {data_dir}"
            )
        self._ensure_dir(data_dir)
        result = run(
            f"chown {shlex.quote(username)}:{shlex.quote(username)} {shlex.quote(data_dir)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not set managed data ownership for {component.name}")
        result = run(
            f"chmod 0750 {shlex.quote(data_dir)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not set managed data permissions for {component.name}")

        env_file = context.get('env_file')
        if env_file:
            validate_filesystem_path(env_file, must_exist=False)
            env_file = os.path.realpath(env_file)
            env_parent = os.path.dirname(env_file)
            safe_env_file = run(
                f"runuser -u {shlex.quote(username)} -- test -r {shlex.quote(env_file)} && "
                f"! runuser -u {shlex.quote(username)} -- test -w {shlex.quote(env_file)} && "
                f"! runuser -u {shlex.quote(username)} -- test -w {shlex.quote(env_parent)}",
                check=False,
            )
            if safe_env_file.returncode != 0:
                raise RuntimeError(
                    f"Component '{component.name}': env_file must be readable but not "
                    f"writable by service user {username}, and its directory must not "
                    f"be writable by that user: {env_file}"
                )

        exec_start = render_template(component.exec, context) if component.exec else context['binary']
        create_managed_service(
            service_name,
            exec_start,
            context['working_dir'],
            username,
            username,
            env_file=env_file,
            description=f"infra.json service: {component.name}",
            runtime_env=runtime_env,
            writable_paths=[data_dir],
        )

        if component.health:
            self._poll_health(component)

    def _poll_health(
        self,
        component: Component,
        timeout: float = 30.0,
        delay: float = 1.0,
    ) -> None:
        """Poll a service's health endpoint until it answers, then return.

        Only a 2xx response accepts the release. A persistent failure aborts
        activation so the caller can restore the previous release. A monotonic
        deadline bounds slow connection attempts as well as retry delays.
        """
        import time
        import urllib.error

        url = f"http://127.0.0.1:{component.port}{component.health}"
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                with open_loopback(url, timeout=min(3.0, remaining)) as resp:
                    if 200 <= resp.status < 300:
                        print(f"  ✓ Health check passed for '{component.name}' ({url} → {resp.status})")
                        return
            except urllib.error.HTTPError:
                pass
            except (urllib.error.URLError, OSError):
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
        raise RuntimeError(
            f"Health check for '{component.name}' did not pass within {timeout:g} seconds ({url})"
        )

    def build_project(self, project_path: str, project_type: str,
                      site_root: Optional[str] = None) -> None:
        if project_type == "node":
            self._build_node_project(project_path, site_root=site_root)
        elif project_type == "static":
            self._build_static_project(project_path)
        else:
            print(f"  ⚠ Unknown project type, no build performed")
    
    def _build_node_project(self, project_path: str,
                            site_root: Optional[str] = None) -> bool:
        print(f"  Building Node.js project at {project_path}")

        # Check if a build script is defined in package.json before running it
        package_json = os.path.join(project_path, "package.json")
        has_build_script = False
        if os.path.exists(package_json):
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                has_build_script = "build" in pkg.get("scripts", {})
            except Exception:
                pass
        
        if not has_build_script:
            message = "No build script in package.json"
            print(f"  ℹ {message}, skipping build step")
            return False

        package_lock = os.path.join(project_path, "package-lock.json")
        npm_install_command = "npm ci" if os.path.exists(package_lock) else "npm install"
        freshness_args = shlex.join(npm_freshness_args())
        freshness_suffix = f" {freshness_args}" if freshness_args else ""
        install_result = run(
            f"cd {shlex.quote(project_path)} && TMPDIR=/var/tmp {npm_install_command}{freshness_suffix}",
            check=False,
            capture_output=True,
        )
        if install_result.returncode != 0:
            error = self._get_command_error(install_result, f"{npm_install_command} failed")
            raise RuntimeError(f"Node dependency install failed: {error}")

        build_cmd = "npm run build"
        env_prefix = ["TMPDIR=/var/tmp"]

        if site_root:
            normalized_root = site_root if site_root.startswith('/') else f"/{site_root}"
            if not normalized_root.endswith('/'):
                normalized_root = f"{normalized_root}/"
            env_prefix.append(f"VITE_SITE_ROOT={shlex.quote(normalized_root)}")
            build_cmd = f"{build_cmd} -- --base {shlex.quote(normalized_root)}"

        if env_prefix:
            build_cmd = f"{' '.join(env_prefix)} {build_cmd}"
        
        result = run(
            f"cd {shlex.quote(project_path)} && {build_cmd}",
            check=False,
            capture_output=True,
        )
        
        if result.returncode != 0:
            error = self._get_command_error(result, "npm run build failed")
            raise RuntimeError(f"Node build failed: {error}")

        print("  ✓ Node.js project built")

        return True
    
    def _build_static_project(self, project_path: str):
        print(f"  Static website at {project_path} - no build required")
        print("  ✓ Static files ready")
