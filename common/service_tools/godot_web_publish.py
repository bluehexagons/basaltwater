#!/usr/bin/env python3
"""Godot export support and compatibility launcher for site publishing."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import stat
import sys
import time
import webbrowser

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from lib.remote_utils import run
from lib.validation import validate_positive_integer


_PROJECT_NAME_PATTERN = re.compile(
    r'^\s*config/name\s*=\s*(?:"(?P<quoted>.*)"|(?P<plain>[^;#]+))\s*$'
)
_COMPRESS_SUFFIXES = (".pck", ".wasm")


def add_legacy_arguments(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--build-timeout", type=validate_positive_integer, default=3600,
                        help="Export deadline in seconds (default: 3600)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a Godot project to its managed HTTPS site",
    )
    add_legacy_arguments(parser)
    return parser


def _validate_preset(value: str) -> str:
    if not value or len(value) > 128 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError("The export preset must be a short printable name")
    return value


def project_display_name(project_dir: str) -> str:
    project_file = os.path.join(project_dir, "project.godot")
    fallback = os.path.basename(project_dir.rstrip(os.sep)) or "godot-game"
    if not os.path.isfile(project_file):
        return fallback
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
    return fallback


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


def validate_export_tree(export_dir: str) -> None:
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


def export_site(args: argparse.Namespace, project_dir: str, staging_dir: str) -> None:
    """Export into the site publisher's private staging directory."""
    preset = _validate_preset("Web" if args.preset is None else args.preset)
    if not os.path.isfile(os.path.join(project_dir, "project.godot")):
        raise ValueError(f"no project.godot found in {project_dir}")
    export_path = os.path.join(staging_dir, "index.html")
    command = [
        "godot", "--headless", "--path", project_dir,
        "--export-debug" if args.debug else "--export-release",
        preset, export_path,
    ]
    result = run(
        command, check=False, stdout=sys.stderr if args.json else None,
        timeout=args.build_timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Godot export failed with exit code {result.returncode}")
    if not os.path.isfile(export_path):
        raise RuntimeError("Godot did not create index.html")


def prepare_site(staging_dir: str, *, precompress: bool) -> dict[str, object]:
    """Compress a validated export and summarize its artifacts."""
    compressed = _precompress_export(staging_dir) if precompress else []
    return {**_artifact_summary(staging_dir, compressed), "precompressed": compressed}


def _publish(args: argparse.Namespace) -> tuple[str, str, dict[str, object]]:
    from common.service_tools import static_web_publish

    result = static_web_publish.publish(argparse.Namespace(
        site=args.game, project_option=args.project_option,
        project_positional=args.project_positional, profile="godot",
        preset=args.preset, debug=args.debug, no_precompress=not args.precompress,
        no_build=False, output=None, json=args.json, build_timeout=args.build_timeout,
        build_script=None, no_install=False,
    ))
    site = result["site"]
    return site, result["url"], {**result["metadata"], "game": site}


def publish_legacy(args: argparse.Namespace) -> int:
    started_at = time.monotonic()
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


def main(argv: list[str] | None = None) -> int:
    return publish_legacy(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
