"""Optional Debian-native hosting step; plugin composition is owned elsewhere."""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import pwd
import tempfile

from lib.atomic_io import write_bytes_atomic, write_json_atomic
from lib.config import SetupConfig
from lib.headscale_config import validate_headscale_settings
from lib.headscale_server import (
    SHA256, VERSION, Release, ServerPaths, configtest, health, install_deb,
    load_manifest, manifest_for, preflight, private_directory, publish_config,
    regular_bytes, render_config, render_nginx, require_capabilities,
    runtime_operation, stopped,
)
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from web.ssl_steps import obtain_letsencrypt_certificate, setup_certificate_renewal


def _nginx_publish(content: str, paths: ServerPaths) -> None:
    """Called under the shared Nginx lock, preserving every unrelated file."""
    old = regular_bytes(paths.nginx) if paths.nginx.exists() else None
    try:
        write_bytes_atomic(str(paths.nginx), content.encode(), mode=0o644)
        run(["nginx", "-t"], capture_output=True)
        run(["systemctl", "reload", "nginx"])
    except BaseException:
        if old is None:
            paths.nginx.unlink(missing_ok=True)
        else:
            write_bytes_atomic(str(paths.nginx), old, mode=0o644)
        run(["nginx", "-t"], capture_output=True)
        run(["systemctl", "reload", "nginx"])
        raise


def configure_headscale(config: SetupConfig) -> None:
    """None is inert; False stops/disables preserving identity and binding."""
    if config.headscale is None:
        return
    validate_headscale_settings(config, complete=True)
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would reconcile native Headscale hosting")
        return
    require_capabilities(firewall=config.headscale)
    paths = ServerPaths()
    if config.headscale is False:
        if load_manifest(paths) is None:
            raise ValueError("Refusing to disable unmanaged Headscale; explicit adoption required")
        with runtime_operation(paths, "headscale-disable"):
            run(["systemctl", "stop", "headscale"])
            stopped()
            run(["systemctl", "disable", "headscale"])
        return
    # Existing server setup owns Nginx and Certbot installation. Check before
    # package mutation; do not replace an unrelated web server or its defaults.
    run(["systemctl", "is-active", "--quiet", "nginx"], capture_output=True)
    firewall = run(["ufw", "status"], capture_output=True)
    if "Status: active" not in firewall.stdout:
        raise RuntimeError("Activate the project firewall before Headscale hosting")
    preflight(config, paths)
    nginx_store = OperationStateStore(str(paths.nginx.parent.parent / ".basaltwater-nginx-operation.json"))
    with nginx_store.ownership():
        if nginx_store.load() is not None:
            raise RuntimeError("Unfinished Nginx operation requires recovery")
        with runtime_operation(paths, "headscale-configure"):
            existing = preflight(config, paths)
            if existing:
                # Preserve policy, keys and verified DNS settings on ordinary reruns.
                configtest(paths.config)
                run(["systemctl", "enable", "headscale"])
                run(["systemctl", "start", "headscale"])
                health(paths)
                return
            architecture = run(["dpkg", "--print-architecture"], capture_output=True).stdout.strip()
            if architecture not in SHA256:
                raise ValueError("Headscale official native packages support amd64 and arm64")
            release = Release(VERSION, architecture, SHA256[architecture])
            with tempfile.TemporaryDirectory(prefix="headscale-package-") as temporary:
                package = Path(temporary) / "headscale.deb"
                run(["curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2",
                     "--output", str(package), release.url], capture_output=True, timeout=300)
                install_deb(package, release)
            account = pwd.getpwnam("headscale")
            private_directory(paths.etc, gid=account.pw_gid, mode=0o750)
            private_directory(paths.data, uid=account.pw_uid, gid=account.pw_gid)
            write_json_atomic(str(paths.etc / "policy.hujson"), {
                "acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}],
            }, mode=0o640, uid=0, gid=account.pw_gid)
            content = render_config(config, paths)
            # Remove the package's unactivated default, so failed first setup
            # cannot restart it as a purported previous working configuration.
            paths.config.unlink(missing_ok=True)
            with tempfile.TemporaryDirectory(prefix=".candidate-", dir=paths.etc) as temporary:
                candidate = Path(temporary) / "config.yaml"
                write_bytes_atomic(str(candidate), content.encode())
                configtest(candidate)
            for key in ("noise_private.key", "derp_server_private.key"):
                path = paths.data / key
                if path.exists():
                    regular_bytes(path)
                    os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
                    os.chmod(path, 0o600, follow_symlinks=False)
            old_nginx = regular_bytes(paths.nginx) if paths.nginx.exists() else None
            try:
                _nginx_publish(render_nginx(str(config.headscale_domain), tls=False), paths)
                for port in ("80/tcp", "443/tcp", "3478/udp"):
                    run(["ufw", "allow", port])
                if not obtain_letsencrypt_certificate([str(config.headscale_domain)], config.ssl_email):
                    raise RuntimeError("Headscale requires a valid trusted certificate")
                setup_certificate_renewal()
                _nginx_publish(render_nginx(str(config.headscale_domain)), paths)
                publish_config(content, paths, gid=account.pw_gid)
                write_json_atomic(str(paths.manifest), asdict(manifest_for(config)))
            except BaseException:
                run(["systemctl", "stop", "headscale"])
                stopped()
                if old_nginx is None:
                    paths.nginx.unlink(missing_ok=True)
                    run(["nginx", "-t"], capture_output=True)
                    run(["systemctl", "reload", "nginx"])
                else:
                    _nginx_publish(old_nginx.decode(), paths)
                raise
