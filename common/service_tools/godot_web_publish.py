#!/usr/bin/env python3
"""Export one Godot project into the infra_tools HTTPS publishing root."""

from __future__ import annotations

import argparse
import fcntl
import gzip
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


GAMES_ROOT = "/srv/infra-tools/web/games"
BASE_URL_FILE = "/etc/infra-tools/internal-web/base-url"
METADATA_FILE = ".infra-tools.json"
CATALOG_FILE = "index.html"
_GAME_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_PROJECT_NAME_PATTERN = re.compile(
    r'^\s*config/name\s*=\s*(?:"(?P<quoted>.*)"|(?P<plain>[^;#]+))\s*$'
)
_COMPRESS_SUFFIXES = (".pck", ".wasm")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a Godot project to its managed HTTPS web host",
    )
    parser.add_argument(
        "game",
        nargs="?",
        help=(
            "URL-safe game name; defaults to application/config/name or the "
            "project directory"
        ),
    )
    parser.add_argument(
        "project_positional",
        nargs="?",
        help="Godot project directory after an explicit game name",
    )
    parser.add_argument(
        "--project",
        dest="project_option",
        help="Godot project directory (default: current directory)",
    )
    parser.add_argument(
        "--preset",
        default="Web",
        help="Godot export preset name (default: Web)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Create a debug export instead of a release export",
    )
    parser.add_argument(
        "--no-precompress",
        dest="precompress",
        action="store_false",
        default=True,
        help="Do not create deterministic gzip copies of .wasm and .pck files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only a machine-readable publication result",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the published URL in the default browser",
    )
    return parser


def _validate_preset(value: str) -> str:
    if not value or len(value) > 128 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("The export preset must be a short printable name")
    return value


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)[:63].rstrip("-_")
    if not slug or not _GAME_NAME_PATTERN.fullmatch(slug):
        raise ValueError("Could not derive a URL-safe game name")
    return slug


def _project_display_name(project_file: str, project_dir: str) -> str:
    in_application_section = False
    try:
        with open(project_file, encoding="utf-8") as file_obj:
            for line in file_obj:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_application_section = stripped == "[application]"
                    continue
                if not in_application_section:
                    continue
                match = _PROJECT_NAME_PATTERN.match(line)
                if match:
                    value = match.group("quoted") or match.group("plain") or ""
                    if value.strip():
                        return value.strip()
    except OSError:
        pass
    return os.path.basename(project_dir.rstrip(os.sep)) or "godot-game"


def _current_account() -> pwd.struct_passwd:
    account = pwd.getpwuid(os.getuid())
    if account.pw_uid == 0:
        raise RuntimeError("Run godot-web-publish as the configured non-root user")
    return account


def _replace_export(staging_dir: str, destination_dir: str) -> None:
    """Activate a complete export while retaining the old one on failure."""

    parent_dir = os.path.dirname(destination_dir)
    if os.path.lexists(destination_dir) and (
        os.path.islink(destination_dir) or not os.path.isdir(destination_dir)
    ):
        raise RuntimeError(f"Refusing unsafe game destination: {destination_dir}")
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
                    "Could not restore the previous export; it remains at "
                    f"{backup_dir}"
                ) from rollback_error
        raise
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if activated and os.path.isdir(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)


def _precompress_export(export_dir: str) -> list[str]:
    """Create reproducible gzip assets for large Godot web payloads."""

    compressed: list[str] = []
    for current_dir, _directory_names, file_names in os.walk(export_dir):
        for name in file_names:
            if not name.endswith(_COMPRESS_SUFFIXES):
                continue
            source_path = os.path.join(current_dir, name)
            destination_path = source_path + ".gz"
            with open(source_path, "rb") as source, open(
                destination_path, "wb"
            ) as destination:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=destination,
                    mtime=0,
                ) as compressed_file:
                    shutil.copyfileobj(source, compressed_file)
                destination.flush()
                os.fsync(destination.fileno())
            compressed.append(os.path.relpath(destination_path, export_dir))
    return sorted(compressed)


def _validate_export_tree(export_dir: str) -> None:
    """Reject links and special files before processing any generated output."""
    def scan_failed(error: OSError) -> None:
        raise RuntimeError(f"Could not validate export directory: {error.filename}") from error

    for current_dir, directory_names, file_names in os.walk(export_dir, onerror=scan_failed):
        if not stat.S_ISDIR(os.lstat(current_dir).st_mode):
            raise RuntimeError(f"Export contains an unsafe directory: {current_dir}")
        for name in directory_names:
            path = os.path.join(current_dir, name)
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise RuntimeError(f"Export contains an unsafe directory: {path}")
        for name in file_names:
            path = os.path.join(current_dir, name)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError(f"Export contains an unsafe file: {path}")


def _make_export_readable(export_dir: str) -> None:
    """Give Nginx read access only after validating the entire export."""

    _validate_export_tree(export_dir)

    for current_dir, directory_names, file_names in os.walk(export_dir):
        if os.path.islink(current_dir):
            raise RuntimeError(f"Export contains an unsafe symlink: {current_dir}")
        os.chmod(current_dir, 0o755)
        for name in directory_names:
            path = os.path.join(current_dir, name)
            if os.path.islink(path):
                raise RuntimeError(f"Export contains an unsafe symlink: {path}")
            os.chmod(path, 0o755)
        for name in file_names:
            path = os.path.join(current_dir, name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise RuntimeError(f"Export contains an unsafe file: {path}")
            os.chmod(path, 0o644)


def _artifact_summary(export_dir: str, compressed: list[str]) -> dict[str, int]:
    """Return bounded publication artifact counts and byte totals."""
    compressed_paths = set(compressed)
    artifact_count = 0
    artifact_bytes = 0
    compressed_bytes = 0
    for current_dir, _directory_names, file_names in os.walk(export_dir):
        for name in file_names:
            path = os.path.join(current_dir, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            size = os.stat(path, follow_symlinks=False).st_size
            artifact_count += 1
            artifact_bytes += size
            if os.path.relpath(path, export_dir) in compressed_paths:
                compressed_bytes += size
    return {
        "artifact_count": artifact_count,
        "artifact_bytes": artifact_bytes,
        "compressed_artifact_count": len(compressed),
        "compressed_artifact_bytes": compressed_bytes,
    }


def _base_url() -> str | None:
    try:
        with open(BASE_URL_FILE, encoding="utf-8") as url_file:
            base_url = url_file.read().strip().rstrip("/")
    except OSError:
        return None
    return base_url or None


def _published_url(username: str, game: str) -> str | None:
    base_url = _base_url()
    if not base_url:
        return None
    return f"{base_url}/games/{username}/{game}/"


def _metadata_for_game(game_dir: str) -> dict[str, object]:
    path = os.path.join(game_dir, METADATA_FILE)
    try:
        with open(path, encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_user_catalog(user_root: str, username: str) -> None:
    """Render a user-owned game catalog without exposing metadata files."""

    games: list[tuple[str, str, dict[str, object]]] = []
    for name in sorted(os.listdir(user_root)):
        if name.startswith(".") or name == CATALOG_FILE:
            continue
        path = os.path.join(user_root, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        metadata = _metadata_for_game(path)
        title = str(metadata.get("title") or name)
        games.append((name, title, metadata))

    base_url = _base_url()
    items: list[str] = []
    for name, title, metadata in games:
        published_at = metadata.get("published_at")
        detail = ""
        if isinstance(published_at, str) and published_at:
            detail = f" <small>published {html.escape(published_at)}</small>"
        items.append(
            f'<li><a href="{html.escape(name)}/">{html.escape(title)}</a>{detail}</li>'
        )
    body = "\n".join(items) if items else "<li>No games published yet.</li>"
    canonical = (
        f"<p><code>{html.escape(base_url)}/games/{html.escape(username)}/</code></p>"
        if base_url
        else ""
    )
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(username)} games</title>
<style>body{{font:16px system-ui,sans-serif;max-width:52rem;margin:3rem auto;padding:0 1rem;line-height:1.5}}small{{color:#586069}}code{{background:#eef1f4;padding:.15rem .3rem;border-radius:.2rem}}</style>
</head><body><main><h1>{html.escape(username)} games</h1>
{canonical}<ul>{body}</ul></main></body></html>
"""
    destination = os.path.join(user_root, CATALOG_FILE)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=user_root,
        prefix=".catalog-",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, destination)
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


def _publish(args: argparse.Namespace) -> tuple[str, str, dict[str, object]]:
    preset = _validate_preset(args.preset)
    if args.game is not None and not _GAME_NAME_PATTERN.fullmatch(args.game):
        raise ValueError(
            "game must use lowercase letters, digits, '-' or '_' and start "
            "with a letter or digit"
        )
    project_value = args.project_option or args.project_positional or "."
    project_dir = os.path.realpath(os.path.abspath(os.path.expanduser(project_value)))
    project_file = os.path.join(project_dir, "project.godot")
    if not os.path.isfile(project_file):
        raise ValueError(f"no project.godot found in {project_dir}")

    game = args.game or _slugify(_project_display_name(project_file, project_dir))
    account = _current_account()
    user_root = os.path.join(GAMES_ROOT, account.pw_name)
    if os.path.islink(user_root) or not os.path.isdir(user_root):
        raise RuntimeError(f"managed publishing directory is unavailable: {user_root}")
    if os.stat(user_root).st_uid != account.pw_uid:
        raise RuntimeError(f"publishing directory is not owned by {account.pw_name}")

    lock_path = os.path.join(user_root, f".infra-tools-{game}.lock")
    descriptor = os.open(
        lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
        file_stat = os.fstat(lock_file.fileno())
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise RuntimeError(f"Refusing unsafe game lock: {lock_path}")
        os.fchmod(lock_file.fileno(), 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        staging_dir = tempfile.mkdtemp(prefix=f".{game}-", dir=user_root)
        try:
            export_path = os.path.join(staging_dir, "index.html")
            command = [
                "godot",
                "--headless",
                "--path",
                project_dir,
                "--export-debug" if args.debug else "--export-release",
                preset,
                export_path,
            ]
            result = subprocess.run(
                command,
                check=False,
                stdout=sys.stderr if args.json else None,
            )
            if result.returncode != 0 or not os.path.isfile(export_path):
                if result.returncode == 0:
                    raise RuntimeError("Godot did not create index.html")
                raise RuntimeError(f"Godot export failed with exit code {result.returncode}")

            # Compression and metadata writes can follow existing output links.
            # Validate before either operation, including with --no-precompress.
            _validate_export_tree(staging_dir)
            compressed = _precompress_export(staging_dir) if args.precompress else []
            published_url = _published_url(account.pw_name, game)
            destination_dir = os.path.join(user_root, game)
            replaced = bool(
                os.path.isdir(destination_dir)
                and not os.path.islink(destination_dir)
            )
            artifact_summary = _artifact_summary(staging_dir, compressed)
            metadata: dict[str, object] = {
                **artifact_summary,
                "debug": bool(args.debug),
                "game": game,
                "precompressed": compressed,
                "preset": preset,
                "project": project_dir,
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "replaced": replaced,
                "title": _project_display_name(project_file, project_dir),
                "url": published_url,
            }
            _write_metadata(os.path.join(staging_dir, METADATA_FILE), metadata)
            _make_export_readable(staging_dir)
            _replace_export(staging_dir, destination_dir)
            write_user_catalog(user_root, account.pw_name)
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
    return game, published_url or destination_dir, metadata


def main(argv: list[str] | None = None) -> int:
    started_at = time.monotonic()
    args = _parser().parse_args(argv)
    try:
        game, published_url, metadata = _publish(args)
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "elapsed_seconds": round(time.monotonic() - started_at, 3),
                        "error": str(exc),
                        "ok": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1

    if args.json:
        print(
            json.dumps(
                {
                    "artifact_bytes": metadata["artifact_bytes"],
                    "artifact_count": metadata["artifact_count"],
                    "compressed_artifact_bytes": metadata[
                        "compressed_artifact_bytes"
                    ],
                    "compressed_artifact_count": metadata[
                        "compressed_artifact_count"
                    ],
                    "elapsed_seconds": round(time.monotonic() - started_at, 3),
                    "game": game,
                    "metadata": metadata,
                    "ok": True,
                    "replaced": metadata["replaced"],
                    "url": published_url,
                },
                sort_keys=True,
            )
        )
    else:
        print(f"Published {game} to {published_url}")
    if args.open:
        webbrowser.open(published_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
