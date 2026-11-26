#!/usr/bin/env python3
"""Build and atomically publish sites to the internal HTTPS host."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import html
from itertools import count
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import sys
import tempfile
import time
import webbrowser

from common.service_tools import godot_web_publish
from lib.atomic_io import read_json_file, write_json_atomic, write_text_atomic
from lib.godot_deploy import validate_godot_export, validate_godot_web_entrypoint
from lib.node_toolchain import select_node
from lib.node_project import select_package_manager
from lib.remote_utils import CommandTimeoutError, run
from lib.static_publication import (
    GODOT_PROFILE_FILE,
    RECEIPT_FILE,
    RESERVED_FILES,
    ReceiptLimitError,
    read_receipt,
    source_state,
    verify_receipt,
    write_receipt,
)
from lib.validation import validate_filesystem_path, validate_positive_integer
from lib.validators import validate_username


SITES_ROOT = "/srv/basaltwater/web/sites"
LEGACY_GAMES_ROOT = "/srv/basaltwater/web/games"
BASE_URL_FILE = "/etc/basaltwater/internal-web/base-url"
METADATA_FILE = ".basaltwater.json"
CATALOG_FILE = "index.html"
_SITE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_OUTPUT_CANDIDATES = ("dist", "build", "out", "public")


def add_publish_arguments(parser: argparse.ArgumentParser) -> None:
    """Add site publication arguments to an existing parser."""

    parser.add_argument(
        "site",
        nargs="?",
        help="URL-safe site name; defaults to the package, Godot project, or directory name",
    )
    parser.add_argument(
        "project_positional",
        nargs="?",
        help="Project directory after an explicit site name",
    )
    parser.add_argument(
        "--project",
        dest="project_option",
        help="Project directory (default: current directory)",
    )
    parser.add_argument(
        "--output",
        help="Build output relative to the project (auto-detected by default)",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Publish existing output without running a project build",
    )
    parser.add_argument("--profile", choices=("static", "godot"), default="static")
    parser.add_argument("--build-script", help="Package script to build a static site (default: build)")
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not install missing JavaScript dependencies before building",
    )
    parser.add_argument("--preset", help="Godot export preset (default: Web)")
    parser.add_argument("--debug", action="store_true", help="Create a Godot debug export")
    parser.add_argument("--no-precompress", action="store_true", help="Skip gzip copies of Godot WASM and PCK files")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--build-timeout", type=validate_positive_integer, default=3600,
                        help="Total install/build deadline in seconds (default: 3600)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify the published snapshot, checkout and served HTTPS artifacts after activation")
    parser.add_argument("--verify-timeout", type=validate_positive_integer, default=60,
                        help="HTTPS verification deadline in seconds (1–300, default: 60)")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:63].rstrip("-_")
    if not slug or not _SITE_NAME_PATTERN.fullmatch(slug):
        raise ValueError("Could not derive a URL-safe site name")
    return slug


def _current_account() -> pwd.struct_passwd:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise RuntimeError("Run site publishing as the configured non-root user")
    if not validate_username(account.pw_name):
        raise ValueError("Invalid site publishing account name")
    return account


def _read_package(project_dir: str) -> dict[str, object] | None:
    path = os.path.join(project_dir, "package.json")
    if not os.path.isfile(path):
        return None
    if os.path.islink(path):
        raise ValueError("Refusing symlinked package.json")
    try:
        with open(path, encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, ValueError) as exc:
        raise ValueError("Could not read package.json") from exc
    if not isinstance(value, dict):
        raise ValueError("package.json must contain an object")
    return value


def _project_name(project_dir: str, package: dict[str, object] | None) -> str:
    if package is not None:
        package_name = package.get("name")
        if isinstance(package_name, str) and package_name.strip():
            return package_name.rsplit("/", 1)[-1]
    return os.path.basename(project_dir.rstrip(os.sep)) or "static-site"


def _run_project_build(
    project_dir: str,
    package: dict[str, object] | None,
    *,
    install: bool,
    json_output: bool = False,
    timeout: int = 3600,
    script: str = "build",
) -> None:
    if package is None:
        raise ValueError("No package.json found; use --no-build with an existing output")
    manager = select_package_manager(project_dir, package)
    install_command = manager.install_command(corepack=True)
    build_command = manager.script_command(package, script, corepack=True)
    environment = select_node(project_dir).environment()
    deadline = time.monotonic() + timeout
    if install and not os.path.isdir(os.path.join(project_dir, "node_modules")):
        install_result = run(
            install_command, cwd=project_dir, check=False,
            env=environment,
            stdout=sys.stderr if json_output else None,
            timeout=timeout,
        )
        if install_result.returncode != 0:
            raise RuntimeError(
                f"Dependency installation failed with exit code {install_result.returncode}"
            )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CommandTimeoutError("Static-site dependency installation/build", timeout)
    build_result = run(
        build_command, cwd=project_dir, check=False,
        env=environment,
        stdout=sys.stderr if json_output else None,
        timeout=remaining,
    )
    if build_result.returncode != 0:
        raise RuntimeError(f"Static-site build failed with exit code {build_result.returncode}")


def _resolve_output(project_dir: str, value: str | None) -> str:
    if value:
        if os.path.isabs(value):
            raise ValueError("--output must be relative to the project")
        unresolved = os.path.abspath(os.path.join(project_dir, value))
        if os.path.islink(unresolved):
            raise ValueError("Static output may not be a symlink")
        candidate = os.path.realpath(unresolved)
        if os.path.commonpath((project_dir, candidate)) != project_dir:
            raise ValueError("--output must remain inside the project")
        output_dir = candidate
    else:
        output_dir = ""
        for name in _OUTPUT_CANDIDATES:
            candidate = os.path.join(project_dir, name)
            if os.path.isdir(candidate) and os.path.isfile(
                os.path.join(candidate, "index.html")
            ):
                output_dir = candidate
                break
        if not output_dir:
            raise ValueError(
                "Could not detect static output; use --output with a directory containing index.html"
            )
    validate_filesystem_path(output_dir, must_exist=True)
    if os.path.islink(output_dir) or not os.path.isdir(output_dir):
        raise ValueError("Static output must be a real directory")
    if not os.path.isfile(os.path.join(output_dir, "index.html")):
        raise ValueError("Static output must contain index.html")
    return output_dir


def _validate_output_tree(output_dir: str) -> None:
    def scan_failed(error: OSError) -> None:
        raise RuntimeError(f"Could not validate static output: {error.filename}") from error

    for current_dir, directory_names, file_names in os.walk(output_dir, onerror=scan_failed):
        if os.path.islink(current_dir):
            raise ValueError(f"Static output contains a symlink: {current_dir}")
        for name in [*directory_names, *file_names]:
            path = os.path.join(current_dir, name)
            if os.path.islink(path):
                raise ValueError(f"Static output contains a symlink: {path}")
            if not os.path.isdir(path) and not os.path.isfile(path):
                raise ValueError(f"Static output contains an unsupported file: {path}")


def _make_tree_readable(root: str) -> None:
    for current_dir, directory_names, file_names in os.walk(root):
        os.chmod(current_dir, 0o755)
        for name in directory_names:
            os.chmod(os.path.join(current_dir, name), 0o755)
        for name in file_names:
            mode = 0o600 if current_dir == root and name == RECEIPT_FILE else 0o644
            os.chmod(os.path.join(current_dir, name), mode)


def _replace_site(staging_dir: str, destination_dir: str) -> None:
    parent_dir = os.path.dirname(destination_dir)
    if os.path.lexists(destination_dir) and (
        os.path.islink(destination_dir) or not os.path.isdir(destination_dir)
    ):
        raise RuntimeError(f"Refusing unsafe site destination: {destination_dir}")
    backup_dir = tempfile.mkdtemp(prefix=".previous-", dir=parent_dir)
    os.rmdir(backup_dir)
    previous_moved = False
    activated = False
    try:
        if os.path.isdir(destination_dir):
            os.replace(destination_dir, backup_dir)
            previous_moved = True
        os.replace(staging_dir, destination_dir)
        staging_dir = ""
        activated = True
    except Exception:
        if previous_moved and not os.path.exists(destination_dir):
            try:
                os.replace(backup_dir, destination_dir)
            except OSError as rollback_error:
                raise RuntimeError(
                    f"Could not restore the previous site; it remains at {backup_dir}"
                ) from rollback_error
        raise
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if activated and os.path.isdir(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)


def _base_url() -> str | None:
    try:
        with open(BASE_URL_FILE, encoding="utf-8") as file_obj:
            value = file_obj.read().strip().rstrip("/")
    except OSError:
        return None
    return value or None


def published_url(username: str, site: str) -> str | None:
    base_url = _base_url()
    return f"{base_url}/sites/{username}/{site}/" if base_url else None


def _metadata(site_dir: str) -> dict[str, object]:
    try:
        value = read_json_file(os.path.join(site_dir, METADATA_FILE))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def site_profile(site_dir: str, metadata: dict[str, object] | None = None) -> str:
    profile = (_metadata(site_dir) if metadata is None else metadata).get("profile")
    if profile in ("static", "godot"):
        return profile
    return "godot" if os.path.isfile(os.path.join(site_dir, GODOT_PROFILE_FILE)) else "static"


def _write_user_catalog(user_root: str, username: str) -> None:
    items: list[str] = []
    for name in sorted(os.listdir(user_root)):
        if name.startswith(".") or name == CATALOG_FILE:
            continue
        path = os.path.join(user_root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        metadata = _metadata(path)
        title = str(metadata.get("title") or name)
        published_at = metadata.get("published_at")
        detail = (
            f" <small>published {html.escape(published_at)}</small>"
            if isinstance(published_at, str) and published_at
            else ""
        )
        items.append(
            f'<li><a href="{html.escape(name)}/">{html.escape(title)}</a>{detail}</li>'
        )
    body = "\n".join(items) if items else "<li>No sites published yet.</li>"
    base_url = _base_url()
    canonical = (
        f"<p><code>{html.escape(base_url)}/sites/{html.escape(username)}/</code></p>"
        if base_url
        else ""
    )
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(username)} sites</title>
<style>body{{font:16px system-ui,sans-serif;max-width:52rem;margin:3rem auto;padding:0 1rem;line-height:1.5}}small{{color:#586069}}code{{background:#eef1f4;padding:.15rem .3rem;border-radius:.2rem}}</style>
</head><body><main><h1>{html.escape(username)} sites</h1>
{canonical}<ul>{body}</ul></main></body></html>
"""
    descriptor, temporary_path = tempfile.mkstemp(
        dir=user_root, prefix=".catalog-", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, os.path.join(user_root, CATALOG_FILE))
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def write_user_catalog(user_root: str, username: str) -> None:
    with _site_lock(user_root, ".catalog"):
        _write_user_catalog(user_root, username)


@contextmanager
def _site_lock(user_root: str, site: str) -> Iterator[None]:
    """Serialize publication and removal without following unsafe lock files."""
    lock_path = os.path.join(user_root, f".basaltwater-{site}.lock")
    descriptor = os.open(
        lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
    )
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise RuntimeError(f"Refusing unsafe site lock: {lock_path}")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _owned_user_root(root: str, account: pwd.struct_passwd) -> str:
    user_root = os.path.join(root, account.pw_name)
    if os.path.islink(user_root) or not os.path.isdir(user_root):
        raise RuntimeError(f"Managed publication directory is unavailable: {user_root}")
    if os.stat(user_root).st_uid != account.pw_uid:
        raise RuntimeError(f"Publication directory is not owned by {account.pw_name}: {user_root}")
    return user_root


def _validate_publication_files(path: str, godot: bool) -> None:
    if godot:
        godot_web_publish.validate_export_tree(path)
    else:
        _validate_output_tree(path)
    if any(os.path.lexists(os.path.join(path, name)) for name in RESERVED_FILES):
        raise ValueError("Output contains reserved Basaltwater publication metadata")


def _write_godot_profile(path: str) -> None:
    write_text_atomic(os.path.join(path, GODOT_PROFILE_FILE), "godot\n", mode=0o644)


def _publication_time() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def publish(args: argparse.Namespace) -> dict[str, object]:
    """Build and publish one site, returning its structured publication record."""

    verify = getattr(args, "verify", False)
    verify_timeout = getattr(args, "verify_timeout", 60)
    if verify and not 1 <= verify_timeout <= 300:
        raise ValueError("Verification timeout must be from 1 through 300 seconds")
    if args.site is not None and not _SITE_NAME_PATTERN.fullmatch(args.site):
        raise ValueError(
            "site must use lowercase letters, digits, '-' or '_' and start "
            "with a letter or digit"
        )
    project_value = args.project_option or args.project_positional or "."
    unresolved_project = os.path.abspath(os.path.expanduser(project_value))
    if os.path.islink(unresolved_project):
        raise ValueError("Project may not be a symlink")
    project_dir = os.path.realpath(unresolved_project)
    validate_filesystem_path(project_dir, must_exist=True)
    if os.path.islink(project_dir) or not os.path.isdir(project_dir):
        raise ValueError("Project must be a real directory")
    godot = args.profile == "godot"
    if godot:
        if args.build_script or args.no_install:
            raise ValueError("--build-script and --no-install apply to static builds")
        if args.output and not args.no_build:
            raise ValueError("--output requires --no-build for the Godot profile")
        if args.no_build and (args.preset is not None or args.debug):
            raise ValueError("--preset and --debug require a Godot build")
        package = None
        title = godot_web_publish.project_display_name(project_dir)
    else:
        if args.preset is not None or args.debug or args.no_precompress:
            raise ValueError("--preset, --debug and --no-precompress require --profile godot")
        package = _read_package(project_dir)
        title = _project_name(project_dir, package)
    site = args.site or _slugify(title)
    account = _current_account()
    user_root = _owned_user_root(SITES_ROOT, account)
    destination_dir = os.path.join(user_root, site)
    started_at = time.monotonic()
    verification = None
    catalog_error = None

    with _site_lock(user_root, site):
        replaced = os.path.lexists(destination_dir)
        if replaced:
            if os.path.islink(destination_dir) or not os.path.isdir(destination_dir):
                raise RuntimeError(f"Refusing unsafe site destination: {destination_dir}")
            if site_profile(destination_dir) != args.profile:
                raise ValueError(f"Site {site} already exists with a different profile")
        source_before_build = None if args.no_build else source_state(project_dir)
        if not godot and not args.no_build:
            _run_project_build(
                project_dir, package, install=not args.no_install, json_output=args.json,
                timeout=args.build_timeout,
                script=args.build_script or "build",
            )
        output_dir = None
        if not godot or args.no_build:
            output_dir = _resolve_output(project_dir, args.output)
            _validate_publication_files(output_dir, godot)
        staging_dir = tempfile.mkdtemp(prefix=f".{site}-", dir=user_root)
        try:
            output = None
            if output_dir is None:
                godot_web_publish.export_site(args, project_dir, staging_dir)
            else:
                shutil.copytree(output_dir, staging_dir, dirs_exist_ok=True, symlinks=True)
                output = os.path.relpath(output_dir, project_dir)
            _validate_publication_files(staging_dir, godot)
            published_at = _publication_time()
            url = published_url(account.pw_name, site)
            metadata: dict[str, object] = {
                "output": output,
                "profile": args.profile,
                "published_at": published_at,
                "site": site,
                "title": title,
                "url": url,
            }
            godot_result = {}
            if godot:
                if args.no_build:
                    validate_godot_export(staging_dir, staging_dir)
                else:
                    validate_godot_web_entrypoint(Path(staging_dir))
                godot_result = godot_web_publish.prepare_site(
                    staging_dir, precompress=not args.no_precompress,
                )
                metadata.update(godot_result, preset=None if args.no_build else args.preset or "Web",
                                debug=None if args.no_build else args.debug, replaced=replaced)
                _write_godot_profile(staging_dir)
            write_json_atomic(os.path.join(staging_dir, METADATA_FILE), metadata, mode=0o644)
            receipt = write_receipt(
                staging_dir, project_dir, output=output, published_at=published_at,
                built=not args.no_build, source_before_build=source_before_build,
            )
            _make_tree_readable(staging_dir)
            _replace_site(staging_dir, destination_dir)
            try:
                write_user_catalog(user_root, account.pw_name)
            except (OSError, RuntimeError, ValueError) as exc:
                if not verify:
                    raise
                catalog_error = str(exc)
            if verify:
                if not url:
                    verification = {
                        "ok": False, "issue": "publication_url_unavailable",
                        "remediation": "Configure the internal-web base URL, then run site verify.",
                    }
                else:
                    try:
                        # Hold the same lock so the verifier reads the snapshot just activated.
                        verification = verify_receipt(
                            destination_dir, url=url, local_output=output_dir, project=project_dir,
                            timeout=verify_timeout, generated_gzip=tuple(godot_result.get("precompressed", [])),
                        )
                    except (OSError, ValueError, RuntimeError) as exc:
                        verification = {
                            "ok": False, "issue": "verification_failed", "error": str(exc),
                            "remediation": "Inspect the publication and run site verify.",
                        }
                verification.setdefault("source", receipt["source"])
                verification.setdefault("build_performed", receipt["build_performed"])
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
    result = {
        "metadata": metadata,
        "ok": True,
        "site": site,
        "url": url or destination_dir,
        "replaced": replaced,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        **godot_result,
    }
    if verification is not None:
        result.update(published=True, ok=verification["ok"] and catalog_error is None, verification=verification)
    if catalog_error is not None:
        result["catalog_error"] = catalog_error
    return result


def print_publish_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True))
        return
    print(f"Published {result['site']} to {result['url']}")
    if "catalog_error" in result:
        print(f"Catalog update failed ({result['catalog_error']})")
    verification = result.get("verification")
    if verification is None:
        return
    if verification["ok"]:
        print(f"Verified {verification['served_artifacts_checked']} served HTTPS artifacts")
    else:
        print(f"Verification failed ({verification['issue']})")
    if source := verification.get("source"):
        print(f"Recorded source commit {source['commit'] or 'unavailable'}")
    for warning in verification.get("warnings", []):
        print(f"Verification warning ({warning})")
    if verification.get("remediation"):
        print(verification["remediation"])


def migrate_legacy_sites() -> list[str]:
    """Move existing game snapshots into the current user's site namespace."""
    account = _current_account()
    legacy_root = os.path.join(LEGACY_GAMES_ROOT, account.pw_name)
    if not os.path.lexists(legacy_root):
        return []
    _owned_user_root(LEGACY_GAMES_ROOT, account)
    user_root = _owned_user_root(SITES_ROOT, account)
    migrated = []
    original_names = {name for name in os.listdir(legacy_root) if _SITE_NAME_PATTERN.fullmatch(name)}
    try:
        for name in sorted(original_names):
            source = os.path.join(legacy_root, name)
            with _site_lock(legacy_root, name):
                if not os.path.lexists(source):
                    continue
                godot_web_publish.validate_export_tree(source)
                if os.stat(source).st_uid != account.pw_uid or not os.path.isfile(os.path.join(source, "index.html")):
                    raise RuntimeError(f"Invalid legacy game snapshot: {source}")
                metadata = _metadata(source)
                published_at = metadata.get("published_at")
                if not isinstance(published_at, str):
                    published_at = _publication_time()
                for suffix_index in count():
                    site = name
                    if suffix_index:
                        suffix = "-godot" if suffix_index == 1 else f"-godot-{suffix_index}"
                        site = name[:63 - len(suffix)] + suffix
                        if site in original_names:
                            continue
                    with _site_lock(user_root, site):
                        destination = os.path.join(user_root, site)
                        if os.path.lexists(destination):
                            continue
                        # Keep the legacy path until all new metadata is ready.
                        url = published_url(account.pw_name, site)
                        metadata.pop("project", None)
                        metadata.pop("game", None)
                        metadata.update(profile="godot", site=site, url=url, published_at=published_at)
                        has_receipt = os.path.lexists(os.path.join(source, RECEIPT_FILE))
                        if has_receipt:
                            # The original receipt binds the recorded source to the original hashes.
                            read_receipt(source)
                        write_json_atomic(os.path.join(source, METADATA_FILE), metadata, mode=0o644)
                        _write_godot_profile(source)
                        if not has_receipt:
                            try:
                                write_receipt(source, None, output=None, published_at=published_at, built=False)
                            except ReceiptLimitError as exc:
                                print(f"Warning: No verification receipt for {site}. {exc}", file=sys.stderr)
                        os.rename(source, destination)
                        migrated.append(url or destination)
                        break
    finally:
        write_user_catalog(user_root, account.pw_name)
    return migrated


def verify_site(args: argparse.Namespace) -> dict[str, object]:
    """Verify an owned publication without changing source or gateway state."""
    if not _SITE_NAME_PATTERN.fullmatch(args.name):
        raise ValueError("Invalid site name")
    account = _current_account()
    user_root = _owned_user_root(SITES_ROOT, account)
    site = os.path.join(user_root, args.name)
    project = os.path.realpath(os.path.abspath(os.path.expanduser(args.project))) if args.project else None
    if project and not os.path.isdir(project):
        raise ValueError("--project must be an existing directory")
    if args.output and project is None:
        raise ValueError("--output requires --project")
    url = None if args.local_only else published_url(account.pw_name, args.name)
    if not args.local_only and not url:
        raise RuntimeError("Internal-web base URL is unavailable")
    with _site_lock(user_root, args.name):
        if os.path.islink(site) or not os.path.isdir(site) or os.stat(site).st_uid != account.pw_uid:
            raise ValueError("Publication directory must be real and owned by the current account")
        try:
            receipt = read_receipt(site)
        except FileNotFoundError:
            if os.path.lexists(os.path.join(site, RECEIPT_FILE)):
                raise
            return {
                "ok": False,
                "issue": "receipt_missing",
                "remediation": "Publish a build within the receipt limits to create a private verification receipt.",
            }
        metadata = _metadata(site)
        profile = site_profile(site, metadata)
        selected_output = args.output or receipt.get("output")
        output = None
        if project and (selected_output or profile == "static"):
            output = _resolve_output(project, selected_output)
        precompressed = metadata.get("precompressed")
        generated = (
            tuple(path for path in precompressed if isinstance(path, str))
            if profile == "godot" and isinstance(precompressed, list) else ()
        )
        result = verify_receipt(
            site, url=url, local_output=output, project=project, timeout=args.timeout,
            generated_gzip=generated, receipt=receipt,
        )
    return {**result, "site": args.name, "url": url}


def list_sites(account: pwd.struct_passwd) -> list[dict[str, object]]:
    user_root = _owned_user_root(SITES_ROOT, account)
    records: list[dict[str, object]] = []
    for name in sorted(os.listdir(user_root)):
        path = os.path.join(user_root, name)
        if name.startswith(".") or name == CATALOG_FILE or os.path.islink(path):
            continue
        if os.path.isdir(path):
            metadata = _metadata(path)
            records.append(
                {
                    **metadata,
                    "profile": site_profile(path, metadata),
                    "site": name,
                    "url": published_url(account.pw_name, name),
                }
            )
    return records


def remove_site(
    account: pwd.struct_passwd, site: str, confirmed: bool, *, profile: str | None = None,
) -> None:
    if not _SITE_NAME_PATTERN.fullmatch(site):
        raise ValueError("Invalid site name")
    if not confirmed:
        raise ValueError("Removing a published site requires --yes")
    user_root = _owned_user_root(SITES_ROOT, account)
    with _site_lock(user_root, site):
        path = os.path.join(user_root, site)
        if os.path.islink(path) or not os.path.isdir(path):
            raise RuntimeError(f"Published site does not exist: {site}")
        if os.stat(path).st_uid != account.pw_uid:
            raise RuntimeError(f"Published site is not owned by {account.pw_name}: {site}")
        if profile is not None and site_profile(path) != profile:
            raise RuntimeError(f"No published {profile} site named {site}")
        shutil.rmtree(path)
        write_user_catalog(user_root, account.pw_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_publish_arguments(parser)
    args = parser.parse_args(argv)
    try:
        result = publish(args)
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True))
        else:
            print(f"Error: {exc}")
        return 2 if isinstance(exc, ValueError) else 1
    print_publish_result(result, as_json=args.json)
    if args.open:
        webbrowser.open(str(result["url"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
