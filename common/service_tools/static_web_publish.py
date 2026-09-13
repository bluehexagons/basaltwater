#!/usr/bin/env python3
"""Build and atomically publish a static site to the internal HTTPS host."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import html
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import webbrowser

from lib.validation import validate_filesystem_path


SITES_ROOT = "/srv/infra-tools/web/sites"
BASE_URL_FILE = "/etc/infra-tools/internal-web/base-url"
METADATA_FILE = ".infra-tools.json"
CATALOG_FILE = "index.html"
_SITE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_OUTPUT_CANDIDATES = ("dist", "build", "out", "public")


def add_publish_arguments(parser: argparse.ArgumentParser) -> None:
    """Add generic static-site publication arguments to an existing parser."""

    parser.add_argument(
        "site",
        nargs="?",
        help="URL-safe site name; defaults to package name or project directory",
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
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Do not install missing JavaScript dependencies before building",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--open", action="store_true")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:63].rstrip("-_")
    if not slug or not _SITE_NAME_PATTERN.fullmatch(slug):
        raise ValueError("Could not derive a URL-safe site name")
    return slug


def _current_account() -> pwd.struct_passwd:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise RuntimeError("Run static-site publishing as the configured non-root user")
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


def _package_commands(project_dir: str) -> tuple[list[str], list[str]]:
    if os.path.isfile(os.path.join(project_dir, "pnpm-lock.yaml")):
        return (
            ["corepack", "pnpm", "install", "--frozen-lockfile"],
            ["corepack", "pnpm", "run", "build"],
        )
    if os.path.isfile(os.path.join(project_dir, "yarn.lock")):
        return (
            ["corepack", "yarn", "install", "--immutable"],
            ["corepack", "yarn", "run", "build"],
        )
    if os.path.isfile(os.path.join(project_dir, "package-lock.json")):
        return (["npm", "ci"], ["npm", "run", "build"])
    return (["npm", "install"], ["npm", "run", "build"])


def _run_project_build(
    project_dir: str,
    package: dict[str, object] | None,
    *,
    install: bool,
    json_output: bool = False,
) -> None:
    if package is None:
        raise ValueError("No package.json found; use --no-build with an existing output")
    scripts = package.get("scripts")
    if not isinstance(scripts, dict) or not isinstance(scripts.get("build"), str):
        raise ValueError("package.json has no build script; use --no-build")
    install_command, build_command = _package_commands(project_dir)
    if install and not os.path.isdir(os.path.join(project_dir, "node_modules")):
        install_result = subprocess.run(
            install_command, cwd=project_dir, check=False,
            stdout=sys.stderr if json_output else None,
        )
        if install_result.returncode != 0:
            raise RuntimeError(
                f"Dependency installation failed with exit code {install_result.returncode}"
            )
    build_result = subprocess.run(
        build_command, cwd=project_dir, check=False,
        stdout=sys.stderr if json_output else None,
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
            os.chmod(os.path.join(current_dir, name), 0o644)


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
        with open(os.path.join(site_dir, METADATA_FILE), encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_user_catalog(user_root: str, username: str) -> None:
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


def _write_metadata(path: str, value: dict[str, object]) -> None:
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(value, file_obj, indent=2, sort_keys=True)
        file_obj.write("\n")
        file_obj.flush()
        os.fsync(file_obj.fileno())
    os.chmod(path, 0o644)


@contextmanager
def _site_lock(user_root: str, site: str) -> Iterator[None]:
    """Serialize publication and removal without following unsafe lock files."""
    lock_path = os.path.join(user_root, f".infra-tools-{site}.lock")
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


def publish(args: argparse.Namespace) -> dict[str, object]:
    """Build and publish one site, returning its structured publication record."""

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
    package = _read_package(project_dir)
    site = args.site or _slugify(_project_name(project_dir, package))
    account = _current_account()
    user_root = os.path.join(SITES_ROOT, account.pw_name)
    if os.path.islink(user_root) or not os.path.isdir(user_root):
        raise RuntimeError(f"Managed site directory is unavailable: {user_root}")
    if os.stat(user_root).st_uid != account.pw_uid:
        raise RuntimeError(f"Site directory is not owned by {account.pw_name}")

    with _site_lock(user_root, site):
        if not args.no_build:
            _run_project_build(
                project_dir, package, install=not args.no_install, json_output=args.json,
            )
        output_dir = _resolve_output(project_dir, args.output)
        _validate_output_tree(output_dir)
        staging_dir = tempfile.mkdtemp(prefix=f".{site}-", dir=user_root)
        try:
            shutil.copytree(output_dir, staging_dir, dirs_exist_ok=True, symlinks=True)
            _validate_output_tree(staging_dir)
            published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            url = published_url(account.pw_name, site)
            metadata: dict[str, object] = {
                "output": os.path.relpath(output_dir, project_dir),
                "published_at": published_at,
                "site": site,
                "title": _project_name(project_dir, package),
                "url": url,
            }
            _write_metadata(os.path.join(staging_dir, METADATA_FILE), metadata)
            _make_tree_readable(staging_dir)
            destination_dir = os.path.join(user_root, site)
            _replace_site(staging_dir, destination_dir)
            write_user_catalog(user_root, account.pw_name)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
    return {
        "metadata": metadata,
        "ok": True,
        "site": site,
        "url": url or destination_dir,
    }


def list_sites(account: pwd.struct_passwd) -> list[dict[str, object]]:
    user_root = os.path.join(SITES_ROOT, account.pw_name)
    if os.path.islink(user_root) or not os.path.isdir(user_root):
        raise RuntimeError(f"Managed site directory is unavailable: {user_root}")
    records: list[dict[str, object]] = []
    for name in sorted(os.listdir(user_root)):
        path = os.path.join(user_root, name)
        if name.startswith(".") or name == CATALOG_FILE or os.path.islink(path):
            continue
        if os.path.isdir(path):
            records.append(
                {
                    **_metadata(path),
                    "site": name,
                    "url": published_url(account.pw_name, name),
                }
            )
    return records


def remove_site(account: pwd.struct_passwd, site: str, confirmed: bool) -> None:
    if not _SITE_NAME_PATTERN.fullmatch(site):
        raise ValueError("Invalid site name")
    if not confirmed:
        raise ValueError("Removing a published site requires --yes")
    user_root = os.path.join(SITES_ROOT, account.pw_name)
    if os.path.islink(user_root) or not os.path.isdir(user_root):
        raise RuntimeError(f"Managed site directory is unavailable: {user_root}")
    if os.stat(user_root).st_uid != account.pw_uid:
        raise RuntimeError(f"Site directory is not owned by {account.pw_name}")
    with _site_lock(user_root, site):
        path = os.path.join(user_root, site)
        if os.path.islink(path) or not os.path.isdir(path):
            raise RuntimeError(f"Published site does not exist: {site}")
        if os.stat(path).st_uid != account.pw_uid:
            raise RuntimeError(f"Published site is not owned by {account.pw_name}: {site}")
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
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Published {result['site']} to {result['url']}")
    if args.open:
        webbrowser.open(str(result["url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
