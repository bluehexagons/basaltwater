"""Install and maintain the official Godot Engine Linux release."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import stat
import struct
import tarfile
import tempfile
import zipfile
from typing import Any, Mapping

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.maintenance_systemd import configure_maintenance_timer
from lib.release_management import (
    detect_release_arch,
    fetch_latest_verified_github_release_asset,
    load_json_state,
    validate_release_tag,
    write_json_state,
)
from lib.remote_utils import get_user_home, is_dry_run, run
from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr
from lib.validators import validate_username

from .common_steps import _run_as_login_user


GODOT_GITHUB_REPO = "godotengine/godot"
GODOT_INSTALL_ROOT = "/opt/godot"
GODOT_RELEASES_DIR = f"{GODOT_INSTALL_ROOT}/releases"
GODOT_CURRENT_DIR = f"{GODOT_INSTALL_ROOT}/current"
GODOT_BINARY_LINK = "/usr/local/bin/godot"
GODOT4_BINARY_LINK = "/usr/local/bin/godot4"
GODOT_DESKTOP_FILE = "/usr/local/share/applications/org.godotengine.Godot.desktop"
GODOT_STATE_FILE = "/opt/basaltwater/state/godot.json"
GODOT_BUNDLE_STATE_FILE = "/opt/basaltwater/state/godot-bundles.json"
GODOT_EXPORT_TEMPLATE_ROOT = f"{GODOT_INSTALL_ROOT}/export_templates"
GODOT_EXPORT_TEMPLATE_RELEASES_DIR = f"{GODOT_EXPORT_TEMPLATE_ROOT}/releases"

BUTLER_GITHUB_REPO = "itchio/butler"
BUTLER_INSTALL_ROOT = "/opt/butler"
BUTLER_RELEASES_DIR = f"{BUTLER_INSTALL_ROOT}/releases"
BUTLER_CURRENT_DIR = f"{BUTLER_INSTALL_ROOT}/current"
BUTLER_BINARY_LINK = "/usr/local/bin/butler"
BUTLER_STATE_FILE = "/opt/basaltwater/state/butler.json"

STEAMCMD_BOOTSTRAP_URL = (
    "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
)
STEAMCMD_BOOTSTRAP_SHA256 = (
    "cebf0046bfd08cf45da6bc094ae47aa39ebf4155e5ede41373b579b8f1071e7c"
)

_GODOT_WEB_TEMPLATE_FILES = (
    "web_debug.zip",
    "web_release.zip",
    "web_dlink_debug.zip",
    "web_dlink_release.zip",
    "web_nothreads_debug.zip",
    "web_nothreads_release.zip",
    "web_dlink_nothreads_debug.zip",
    "web_dlink_nothreads_release.zip",
)
_REMOTE_ZIP_TAIL_SIZE = 128 * 1024
_REMOTE_ZIP_MAX_SIZE = (1 << 32) - 1
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_END_OF_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x05\x06"
_ZIP_LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
_STEAMCMD_BOOTSTRAP_FILES = {
    "steamcmd.sh": 0o755,
    "linux32/steamcmd": 0o755,
    "linux32/steamerrorreporter": 0o755,
    "linux32/libstdc++.so.6": 0o755,
    "linux32/crashhandler.so": 0o755,
}

_GODOT_DESKTOP_ENTRY = f"""[Desktop Entry]
Name=Godot Engine
GenericName=Game Engine
Comment=Create 2D and 3D games
Exec={GODOT_BINARY_LINK} --editor %f
Icon=godot
Terminal=false
Type=Application
MimeType=application/x-godot-project;
Categories=Development;IDE;
StartupWMClass=Godot
"""


def _godot_asset_arch(release_arch: str) -> str:
    """Translate the shared release architecture to Godot's asset suffix."""
    architecture_map = {
        "amd64": "x86_64",
        "arm64": "arm64",
    }
    try:
        return architecture_map[release_arch]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported Godot architecture: {release_arch}") from exc


def _godot_release_dir(tag_name: str, archive_sha256: str) -> str:
    """Return the immutable directory for one verified Godot release."""
    safe_tag = validate_release_tag(tag_name)
    if len(archive_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in archive_sha256
    ):
        raise ValueError("Invalid Godot release SHA-256")
    return f"{GODOT_RELEASES_DIR}/{safe_tag}-{archive_sha256[:12]}"


def fetch_latest_godot_release(asset_arch: str) -> tuple[str, str, str]:
    """Return the latest stable Godot tag, asset URL, and publisher digest."""
    asset_suffix = f"_linux.{asset_arch}.zip"
    return fetch_latest_verified_github_release_asset(
        GODOT_GITHUB_REPO,
        asset_matches=lambda tag_name, asset_name: (
            asset_name == f"Godot_v{tag_name}{asset_suffix}"
        ),
        missing_asset_description=(
            f"No verified stable Godot Linux asset found for architecture '{asset_arch}'"
        ),
    )


def read_godot_state() -> Mapping[str, Any]:
    """Return persisted Godot installation metadata."""
    return load_json_state(
        GODOT_STATE_FILE,
        read_error_label="Godot state file",
        invalid_state_message="Invalid Godot state file contents",
    )


def write_godot_state(tag_name: str, archive_sha256: str) -> None:
    """Persist the active verified Godot release."""
    write_json_state(
        GODOT_STATE_FILE,
        {"tag_name": tag_name, "archive_sha256": archive_sha256},
        mode=0o600,
    )


def read_godot_bundle_state() -> Mapping[str, Any]:
    """Return registered bundle selections and target users."""
    return load_json_state(
        GODOT_BUNDLE_STATE_FILE,
        read_error_label="Godot bundle state file",
        invalid_state_message="Invalid Godot bundle state file contents",
    )


def write_godot_bundle_state(
    bundles: list[str],
    users: list[str],
    web_identities: list[str],
    web_access_sources: list[str] | None = None,
) -> None:
    """Persist bundle selections used by recurring maintenance."""
    write_json_state(
        GODOT_BUNDLE_STATE_FILE,
        {
            "bundles": bundles,
            "users": users,
            "web_access_sources": web_access_sources or [],
            "web_identities": web_identities,
        },
        mode=0o600,
    )


def read_butler_state() -> Mapping[str, Any]:
    """Return persisted Butler installation metadata."""
    return load_json_state(
        BUTLER_STATE_FILE,
        read_error_label="Butler state file",
        invalid_state_message="Invalid Butler state file contents",
    )


def write_butler_state(tag_name: str, archive_sha256: str) -> None:
    """Persist the active verified Butler release."""
    write_json_state(
        BUTLER_STATE_FILE,
        {"tag_name": tag_name, "archive_sha256": archive_sha256},
        mode=0o600,
    )


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        while chunk := file_obj.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_verified_binary(
    archive_path: str,
    *,
    expected_sha256: str,
    binary_member: str,
    release_dir: str,
) -> str:
    """Verify a Godot archive and atomically stage its one expected binary."""
    if _sha256_file(archive_path) != expected_sha256:
        raise RuntimeError("Godot release archive checksum verification failed")

    os.makedirs(release_dir, mode=0o755, exist_ok=True)
    binary_path = os.path.join(release_dir, "godot")
    temporary_path = ""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            try:
                member = archive.getinfo(binary_member)
            except KeyError as exc:
                raise RuntimeError(
                    f"Godot release archive is missing {binary_member}"
                ) from exc
            member_mode = member.external_attr >> 16
            member_type = stat.S_IFMT(member_mode)
            if member.is_dir() or member_type not in (0, stat.S_IFREG):
                raise RuntimeError("Godot release binary is not a regular file")
            with archive.open(member) as source, tempfile.NamedTemporaryFile(
                mode="wb",
                dir=release_dir,
                prefix=".godot-",
                delete=False,
            ) as destination:
                temporary_path = destination.name
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
        os.chmod(temporary_path, 0o755)
        os.replace(temporary_path, binary_path)
        temporary_path = ""
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
    return binary_path


def _validate_archive_digest(archive_path: str, expected_sha256: str, label: str) -> None:
    """Require an archive to match its expected hexadecimal SHA-256."""
    if _sha256_file(archive_path) != expected_sha256:
        raise RuntimeError(f"{label} archive checksum verification failed")


def _godot_template_version(tag_name: str) -> str:
    """Return Godot's version-specific export-template directory name."""
    safe_tag = validate_release_tag(tag_name)
    if not safe_tag.endswith("-stable"):
        raise ValueError(f"Unsupported Godot export-template tag: {tag_name}")
    return f"{safe_tag.removesuffix('-stable')}.stable"


def fetch_godot_export_templates(tag_name: str) -> tuple[str, str, str]:
    """Return the official template package and digest matching an engine tag."""
    safe_tag = validate_release_tag(tag_name)
    return fetch_latest_verified_github_release_asset(
        GODOT_GITHUB_REPO,
        asset_matches=lambda release_tag, asset_name: (
            release_tag == safe_tag
            and asset_name == f"Godot_v{safe_tag}_export_templates.tpz"
        ),
        missing_asset_description=(
            f"No verified Godot export templates found for release '{safe_tag}'"
        ),
    )


def _godot_web_template_release_dir(
    template_version: str,
    archive_sha256: str,
) -> str:
    """Return the immutable cache directory for official web templates."""
    validate_release_tag(template_version)
    if len(archive_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in archive_sha256
    ):
        raise ValueError("Invalid Godot export-template SHA-256")
    return (
        f"{GODOT_EXPORT_TEMPLATE_RELEASES_DIR}/"
        f"{template_version}-{archive_sha256[:12]}"
    )


def _web_template_release_complete(release_dir: str) -> bool:
    """Return whether every managed web-template file is present."""
    expected_files = ("version.txt", *_GODOT_WEB_TEMPLATE_FILES)
    return all(os.path.isfile(os.path.join(release_dir, name)) for name in expected_files)


def _remote_https_content_length(download_url: str) -> int:
    """Return the positive content length reported by an HTTPS download."""
    result = run(
        "curl -fsSIL --proto '=https' --proto-redir '=https' "
        f"{shlex.quote(download_url)}",
        check=True,
        capture_output=True,
        display_cmd=(
            "curl -fsSIL --proto '=https' --proto-redir '=https' "
            "<template URL>"
        ),
    )
    content_lengths: list[int] = []
    for line in (result.stdout or "").splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                continue
            if content_length > 0:
                content_lengths.append(content_length)
    if not content_lengths:
        raise RuntimeError("Godot template server did not report an archive size")
    archive_size = content_lengths[-1]
    if archive_size > _REMOTE_ZIP_MAX_SIZE:
        raise RuntimeError("Godot template archive requires unsupported ZIP64 ranges")
    return archive_size


def _download_https_range(
    download_url: str,
    start: int,
    end: int,
    destination_path: str,
    *,
    label: str,
) -> None:
    """Download exactly one bounded byte range from an HTTPS resource."""
    if start < 0 or end < start:
        raise ValueError("Invalid Godot template download range")
    expected_size = end - start + 1
    run(
        "curl -fLsS --proto '=https' --proto-redir '=https' "
        f"--range {start}-{end} --max-filesize {expected_size} "
        f"-o {shlex.quote(destination_path)} {shlex.quote(download_url)}",
        check=True,
        display_cmd=(
            "curl -fLsS --proto '=https' --proto-redir '=https' "
            f"--range {start}-{end} --max-filesize {expected_size} "
            f"-o {destination_path} <template URL> ({label})"
        ),
    )
    try:
        downloaded_size = os.path.getsize(destination_path)
    except OSError as exc:
        raise RuntimeError(f"Godot template range was not written: {label}") from exc
    if downloaded_size != expected_size:
        raise RuntimeError(
            "Godot template server did not honor the requested byte range "
            f"for {label} (received {downloaded_size}, expected {expected_size})"
        )


def _find_zip_end_record(tail: bytes) -> int:
    """Locate a valid ZIP end record whose comment reaches the archive end."""
    search_end = len(tail)
    while True:
        position = tail.rfind(
            _ZIP_END_OF_CENTRAL_DIRECTORY_SIGNATURE,
            0,
            search_end,
        )
        if position < 0:
            raise RuntimeError("Godot template archive has no ZIP end record")
        if position + 22 <= len(tail):
            comment_length = struct.unpack_from("<H", tail, position + 20)[0]
            if position + 22 + comment_length == len(tail):
                return position
        search_end = position


def _parse_zip_central_directory(
    central_directory: bytes,
    *,
    total_entries: int,
    expected_members: set[str],
) -> dict[str, tuple[int, int, bytes]]:
    """Return local offsets, compressed sizes, and records for selected files."""
    selected: dict[str, tuple[int, int, bytes]] = {}
    position = 0
    for _entry_index in range(total_entries):
        if (
            position + 46 > len(central_directory)
            or central_directory[position : position + 4]
            != _ZIP_CENTRAL_DIRECTORY_SIGNATURE
        ):
            raise RuntimeError("Godot template archive has an invalid ZIP directory")
        flags = struct.unpack_from("<H", central_directory, position + 8)[0]
        compression_method = struct.unpack_from(
            "<H", central_directory, position + 10
        )[0]
        compressed_size = struct.unpack_from(
            "<I", central_directory, position + 20
        )[0]
        uncompressed_size = struct.unpack_from(
            "<I", central_directory, position + 24
        )[0]
        name_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", central_directory, position + 28
        )
        disk_number = struct.unpack_from("<H", central_directory, position + 34)[0]
        local_offset = struct.unpack_from("<I", central_directory, position + 42)[0]
        record_length = 46 + name_length + extra_length + comment_length
        record_end = position + record_length
        if record_end > len(central_directory):
            raise RuntimeError("Godot template ZIP directory record is truncated")
        encoding = "utf-8" if flags & 0x800 else "cp437"
        try:
            member_name = central_directory[
                position + 46 : position + 46 + name_length
            ].decode(encoding)
        except UnicodeDecodeError as exc:
            raise RuntimeError("Godot template ZIP member name is invalid") from exc
        if member_name in expected_members:
            if flags & 0x1:
                raise RuntimeError(
                    f"Godot template member is unexpectedly encrypted: {member_name}"
                )
            if compression_method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise RuntimeError(
                    "Godot template member uses an unsupported ZIP compression "
                    f"method: {member_name}"
                )
            if member_name in selected:
                raise RuntimeError(
                    f"Godot template archive contains a duplicate member: {member_name}"
                )
            if (
                disk_number != 0
                or compressed_size == 0xFFFFFFFF
                or uncompressed_size == 0xFFFFFFFF
                or local_offset == 0xFFFFFFFF
            ):
                raise RuntimeError("Godot template archive uses unsupported ZIP64 data")
            selected[member_name] = (
                local_offset,
                compressed_size,
                central_directory[position:record_end],
            )
        position = record_end

    missing_members = sorted(expected_members - selected.keys())
    if missing_members:
        raise RuntimeError(
            "Godot export-template archive is missing " + ", ".join(missing_members)
        )
    return selected


def _read_remote_zip_directory(
    download_url: str,
    workspace_dir: str,
    expected_members: set[str],
) -> tuple[int, dict[str, tuple[int, int, bytes]]]:
    """Read only a remote ZIP's tail and directory using bounded ranges."""
    archive_size = _remote_https_content_length(download_url)
    tail_start = max(0, archive_size - _REMOTE_ZIP_TAIL_SIZE)
    tail_path = os.path.join(workspace_dir, "archive-tail.bin")
    _download_https_range(
        download_url,
        tail_start,
        archive_size - 1,
        tail_path,
        label="ZIP directory tail",
    )
    with open(tail_path, "rb") as tail_file:
        tail = tail_file.read()
    end_record = _find_zip_end_record(tail)
    disk_number, directory_disk, entries_on_disk, total_entries = struct.unpack_from(
        "<HHHH", tail, end_record + 4
    )
    directory_size, directory_offset = struct.unpack_from(
        "<II", tail, end_record + 12
    )
    if (
        disk_number != 0
        or directory_disk != 0
        or entries_on_disk != total_entries
        or total_entries == 0xFFFF
        or directory_size == 0xFFFFFFFF
        or directory_offset == 0xFFFFFFFF
    ):
        raise RuntimeError(
            "Godot template archive uses unsupported multi-disk or ZIP64 data"
        )
    if (
        directory_size <= 0
        or directory_offset + directory_size > archive_size
    ):
        raise RuntimeError("Godot template archive has an invalid ZIP directory range")

    relative_directory_offset = directory_offset - tail_start
    if (
        relative_directory_offset >= 0
        and relative_directory_offset + directory_size <= len(tail)
    ):
        central_directory = tail[
            relative_directory_offset : relative_directory_offset + directory_size
        ]
    else:
        directory_path = os.path.join(workspace_dir, "central-directory.bin")
        _download_https_range(
            download_url,
            directory_offset,
            directory_offset + directory_size - 1,
            directory_path,
            label="ZIP central directory",
        )
        with open(directory_path, "rb") as directory_file:
            central_directory = directory_file.read()
    return archive_size, _parse_zip_central_directory(
        central_directory,
        total_entries=total_entries,
        expected_members=expected_members,
    )


def _extract_remote_zip_member(
    download_url: str,
    archive_size: int,
    member_name: str,
    member_record: tuple[int, int, bytes],
    workspace_dir: str,
    destination_path: str,
) -> None:
    """Range-download one ZIP member and extract it with CRC verification."""
    local_offset, compressed_size, central_record = member_record
    maximum_local_record_size = 30 + (2 * 0xFFFF) + compressed_size
    range_end = min(
        archive_size - 1,
        local_offset + maximum_local_record_size - 1,
    )
    if local_offset >= archive_size or range_end < local_offset:
        raise RuntimeError(f"Godot template member has an invalid offset: {member_name}")
    mini_zip_path = os.path.join(workspace_dir, "member.zip")
    _download_https_range(
        download_url,
        local_offset,
        range_end,
        mini_zip_path,
        label=member_name,
    )
    with open(mini_zip_path, "r+b") as mini_zip:
        local_header = mini_zip.read(30)
        if (
            len(local_header) != 30
            or local_header[:4] != _ZIP_LOCAL_FILE_SIGNATURE
        ):
            raise RuntimeError(
                f"Godot template member has an invalid ZIP header: {member_name}"
            )
        local_name_length, local_extra_length = struct.unpack_from(
            "<HH", local_header, 26
        )
        local_record_size = (
            30 + local_name_length + local_extra_length + compressed_size
        )
        if local_record_size > range_end - local_offset + 1:
            raise RuntimeError(
                f"Godot template member range is truncated: {member_name}"
            )
        mini_zip.truncate(local_record_size)
        mini_zip.seek(0, os.SEEK_END)
        rewritten_central_record = bytearray(central_record)
        struct.pack_into("<I", rewritten_central_record, 42, 0)
        mini_zip.write(rewritten_central_record)
        mini_zip.write(
            struct.pack(
                "<IHHHHIIH",
                0x06054B50,
                0,
                0,
                1,
                1,
                len(rewritten_central_record),
                local_record_size,
                0,
            )
        )
        mini_zip.flush()
        os.fsync(mini_zip.fileno())

    try:
        with zipfile.ZipFile(mini_zip_path) as archive:
            try:
                member = archive.getinfo(member_name)
            except KeyError as exc:
                raise RuntimeError(
                    f"Godot template range does not contain {member_name}"
                ) from exc
            member_mode = member.external_attr >> 16
            member_type = stat.S_IFMT(member_mode)
            if member.is_dir() or member_type not in (0, stat.S_IFREG):
                raise RuntimeError(
                    f"Godot export-template member is not a regular file: {member_name}"
                )
            with archive.open(member) as source, open(destination_path, "wb") as dest:
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fsync(dest.fileno())
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(
            f"Godot template member failed ZIP CRC validation: {member_name}"
        ) from exc
    finally:
        try:
            os.unlink(mini_zip_path)
        except FileNotFoundError:
            pass
    os.chmod(destination_path, 0o644)


def _download_remote_web_templates(
    download_url: str,
    *,
    expected_version: str,
    release_dir: str,
) -> None:
    """Selectively download and atomically cache official web templates."""
    expected_members = {
        "templates/version.txt": "version.txt",
        **{
            f"templates/{file_name}": file_name
            for file_name in _GODOT_WEB_TEMPLATE_FILES
        },
    }

    os.makedirs(GODOT_EXPORT_TEMPLATE_RELEASES_DIR, mode=0o755, exist_ok=True)
    workspace_dir = tempfile.mkdtemp(
        prefix=".web-template-download-",
        dir=GODOT_EXPORT_TEMPLATE_RELEASES_DIR,
    )
    staging_dir = os.path.join(workspace_dir, "templates")
    os.mkdir(staging_dir, mode=0o755)
    try:
        archive_size, member_records = _read_remote_zip_directory(
            download_url,
            workspace_dir,
            set(expected_members),
        )
        for member_name, destination_name in expected_members.items():
            _extract_remote_zip_member(
                download_url,
                archive_size,
                member_name,
                member_records[member_name],
                workspace_dir,
                os.path.join(staging_dir, destination_name),
            )

        version_path = os.path.join(staging_dir, "version.txt")
        with open(version_path, encoding="utf-8") as version_file:
            installed_version = version_file.read().strip()
        if installed_version != expected_version:
            raise RuntimeError(
                "Godot export-template version does not match the installed engine"
            )
        if os.path.exists(release_dir):
            if not _web_template_release_complete(release_dir):
                raise RuntimeError(
                    f"Existing Godot web-template cache is incomplete: {release_dir}"
                )
            return
        os.replace(staging_dir, release_dir)
        staging_dir = ""
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)


def _install_web_templates_for_user(
    username: str,
    template_version: str,
    release_dir: str,
) -> bool:
    """Copy cached web templates into one user's Godot data directory."""
    if not validate_username(username):
        raise ValueError(f"Invalid Godot bundle username: {username}")
    user_home = get_user_home(username)
    validate_filesystem_path(user_home, must_exist=True)
    destination_dir = os.path.join(
        user_home,
        ".local",
        "share",
        "godot",
        "export_templates",
        template_version,
    )
    was_complete = _web_template_release_complete(destination_dir)
    create_result = _run_as_login_user(
        username,
        user_home,
        f"install -d -m 0755 {shlex.quote(destination_dir)}",
        check=False,
        capture_output=True,
    )
    if create_result.returncode != 0:
        raise RuntimeError(f"Could not create Godot template directory for {username}")
    source_paths = [
        os.path.join(release_dir, name)
        for name in ("version.txt", *_GODOT_WEB_TEMPLATE_FILES)
    ]
    install_result = _run_as_login_user(
        username,
        user_home,
        "install -m 0644 "
        + " ".join(shlex.quote(path) for path in source_paths)
        + f" {shlex.quote(destination_dir)}/",
        check=False,
        capture_output=True,
    )
    if install_result.returncode != 0:
        detail = (
            install_result.stderr
            or install_result.stdout
            or "template installation failed"
        ).strip()
        raise RuntimeError(f"Could not install Godot web templates for {username}: {detail}")
    print(f"  ✓ Godot {template_version} web export templates installed for {username}")
    return not was_complete


def install_or_update_godot_web_bundle(username: str) -> bool:
    """Install official web templates matching the active Godot release."""
    godot_state = read_godot_state()
    tag_name = godot_state.get("tag_name")
    if not isinstance(tag_name, str):
        raise RuntimeError("Godot must be installed before its web bundle")
    template_version = _godot_template_version(tag_name)
    _release_tag, download_url, expected_sha256 = fetch_godot_export_templates(
        tag_name
    )
    release_dir = _godot_web_template_release_dir(
        template_version,
        expected_sha256,
    )
    cache_changed = not _web_template_release_complete(release_dir)
    if cache_changed:
        _download_remote_web_templates(
            download_url,
            expected_version=template_version,
            release_dir=release_dir,
        )
    user_changed = _install_web_templates_for_user(
        username,
        template_version,
        release_dir,
    )
    return cache_changed or user_changed


def _butler_release_dir(tag_name: str, archive_sha256: str) -> str:
    """Return the immutable directory for one verified Butler release."""
    safe_tag = validate_release_tag(tag_name)
    if len(archive_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in archive_sha256
    ):
        raise ValueError("Invalid Butler release SHA-256")
    return f"{BUTLER_RELEASES_DIR}/{safe_tag}-{archive_sha256[:12]}"


def fetch_latest_butler_release(asset_arch: str) -> tuple[str, str, str]:
    """Return the latest verified Butler release for a Linux architecture."""
    return fetch_latest_verified_github_release_asset(
        BUTLER_GITHUB_REPO,
        asset_matches=lambda _tag, asset_name: (
            asset_name == f"butler-linux-{asset_arch}.zip"
        ),
        missing_asset_description=(
            f"No verified Butler Linux asset found for architecture '{asset_arch}'"
        ),
    )


def _extract_verified_butler_archive(
    archive_path: str,
    *,
    expected_sha256: str,
    asset_arch: str,
    release_dir: str,
) -> None:
    """Verify and atomically stage the expected Butler runtime files."""
    _validate_archive_digest(archive_path, expected_sha256, "Butler release")
    expected_files = ("butler", "7z.so", "libc7zip.so")
    os.makedirs(BUTLER_RELEASES_DIR, mode=0o755, exist_ok=True)
    staging_dir = tempfile.mkdtemp(prefix=".butler-", dir=BUTLER_RELEASES_DIR)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for file_name in expected_files:
                member_name = f"linux-{asset_arch}/{file_name}"
                try:
                    member = archive.getinfo(member_name)
                except KeyError as exc:
                    raise RuntimeError(
                        f"Butler release archive is missing {member_name}"
                    ) from exc
                member_mode = member.external_attr >> 16
                member_type = stat.S_IFMT(member_mode)
                if member.is_dir() or member_type not in (0, stat.S_IFREG):
                    raise RuntimeError(
                        f"Butler release member is not a regular file: {member_name}"
                    )
                destination_path = os.path.join(staging_dir, file_name)
                with archive.open(member) as source, open(destination_path, "wb") as dest:
                    shutil.copyfileobj(source, dest)
                    dest.flush()
                    os.fsync(dest.fileno())
                os.chmod(destination_path, 0o755 if file_name == "butler" else 0o644)
        if os.path.exists(release_dir):
            if not all(
                os.path.isfile(os.path.join(release_dir, name))
                for name in expected_files
            ):
                raise RuntimeError(
                    f"Existing Butler release cache is incomplete: {release_dir}"
                )
            return
        os.replace(staging_dir, release_dir)
        staging_dir = ""
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)


def _managed_butler_release() -> str | None:
    """Return the active Butler release only when it is below the managed root."""
    current_binary = os.path.join(BUTLER_CURRENT_DIR, "butler")
    if not os.path.exists(current_binary):
        return None
    current_release = os.path.realpath(BUTLER_CURRENT_DIR)
    releases_root = os.path.realpath(BUTLER_RELEASES_DIR)
    try:
        if os.path.commonpath((current_release, releases_root)) != releases_root:
            return None
    except ValueError:
        return None
    return current_release


def _install_butler_links(release_dir: str) -> None:
    run(
        f"ln -sfn {shlex.quote(release_dir)} {shlex.quote(BUTLER_CURRENT_DIR)}",
        check=True,
    )
    run(
        f"ln -sfn {shlex.quote(BUTLER_CURRENT_DIR)}/butler "
        f"{shlex.quote(BUTLER_BINARY_LINK)}",
        check=True,
    )


def install_or_update_butler_release() -> tuple[str, bool, str]:
    """Install the newest verified Butler release system-wide."""
    asset_arch = detect_release_arch()
    tag_name, download_url, expected_sha256 = fetch_latest_butler_release(asset_arch)
    release_dir = _butler_release_dir(tag_name, expected_sha256)
    installed_state = read_butler_state()
    current_release = _managed_butler_release()
    expected_release = os.path.realpath(release_dir)
    current_binary = os.path.join(BUTLER_CURRENT_DIR, "butler")
    if (
        installed_state.get("tag_name") == tag_name
        and installed_state.get("archive_sha256") == expected_sha256
        and current_release == expected_release
        and os.path.exists(current_binary)
    ):
        _install_butler_links(release_dir)
        print(f"  ✓ Butler already up to date ({tag_name})")
        return tag_name, False, expected_sha256

    with tempfile.TemporaryDirectory(prefix="basaltwater-butler-release-") as temp_dir:
        archive_path = os.path.join(temp_dir, "butler.zip")
        run(
            "curl -fL --proto '=https' --proto-redir '=https' "
            f"-o {shlex.quote(archive_path)} {shlex.quote(download_url)}",
            check=True,
            display_cmd=(
                "curl -fL --proto '=https' --proto-redir '=https' "
                f"-o {archive_path} <release URL>"
            ),
        )
        _extract_verified_butler_archive(
            archive_path,
            expected_sha256=expected_sha256,
            asset_arch=asset_arch,
            release_dir=release_dir,
        )
        run(
            f"{shlex.quote(os.path.join(release_dir, 'butler'))} version",
            check=True,
            capture_output=True,
        )

    try:
        _install_butler_links(release_dir)
        write_butler_state(tag_name, expected_sha256)
    except Exception:
        if current_release and current_release != expected_release:
            _install_butler_links(current_release)
        raise
    print(f"  ✓ Installed Butler {tag_name} for itch.io publishing")
    return tag_name, True, expected_sha256


def _extract_steamcmd_bootstrap(archive_path: str, destination_dir: str) -> None:
    """Extract the pinned Valve bootstrap after strict member validation."""
    _validate_archive_digest(
        archive_path,
        STEAMCMD_BOOTSTRAP_SHA256,
        "SteamCMD bootstrap",
    )
    os.makedirs(destination_dir, mode=0o755, exist_ok=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        archive_members = archive.getmembers()
        members = {member.name: member for member in archive_members}
        if (
            len(archive_members) != len(_STEAMCMD_BOOTSTRAP_FILES)
            or set(members) != set(_STEAMCMD_BOOTSTRAP_FILES)
        ):
            raise RuntimeError("SteamCMD bootstrap archive has unexpected contents")
        for member_name, mode in _STEAMCMD_BOOTSTRAP_FILES.items():
            member = members[member_name]
            if not member.isfile():
                raise RuntimeError(
                    f"SteamCMD bootstrap member is not a regular file: {member_name}"
                )
            destination_path = os.path.join(destination_dir, member_name)
            os.makedirs(os.path.dirname(destination_path), mode=0o755, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Could not read SteamCMD member: {member_name}")
            with source, open(destination_path, "wb") as dest:
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fsync(dest.fileno())
            os.chmod(destination_path, mode)


def install_or_update_steamcmd(username: str) -> bool:
    """Install SteamCMD for one user and let its bootstrap self-update."""
    if detect_release_arch() != "amd64":
        print("  ⚠ SteamCMD is unavailable on Linux ARM64; Butler remains installed")
        return False
    if not validate_username(username):
        raise ValueError(f"Invalid SteamCMD username: {username}")
    user_home = get_user_home(username)
    validate_filesystem_path(user_home, must_exist=True)
    install_dir = os.path.join(
        user_home,
        ".local",
        "share",
        "basaltwater",
        "steamcmd",
    )
    launcher_dir = os.path.join(user_home, ".local", "bin")
    launcher_path = os.path.join(launcher_dir, "steamcmd")
    steamcmd_script = os.path.join(install_dir, "steamcmd.sh")
    changed = not os.path.isfile(steamcmd_script)
    if changed:
        with tempfile.TemporaryDirectory(
            prefix="basaltwater-steamcmd-bootstrap-"
        ) as temporary_dir:
            archive_path = os.path.join(temporary_dir, "steamcmd_linux.tar.gz")
            staging_dir = os.path.join(temporary_dir, "staging")
            run(
                "curl -fL --proto '=https' --proto-redir '=https' "
                f"-o {shlex.quote(archive_path)} {shlex.quote(STEAMCMD_BOOTSTRAP_URL)}",
                check=True,
            )
            _extract_steamcmd_bootstrap(archive_path, staging_dir)
            os.chmod(temporary_dir, 0o755)
            create_result = _run_as_login_user(
                username,
                user_home,
                f"install -d -m 0755 {shlex.quote(install_dir)} "
                f"{shlex.quote(os.path.join(install_dir, 'linux32'))} "
                f"{shlex.quote(launcher_dir)}",
                check=False,
                capture_output=True,
            )
            if create_result.returncode != 0:
                raise RuntimeError(f"Could not create SteamCMD directories for {username}")
            for member_name, mode in _STEAMCMD_BOOTSTRAP_FILES.items():
                source_path = os.path.join(staging_dir, member_name)
                destination_path = os.path.join(install_dir, member_name)
                install_result = _run_as_login_user(
                    username,
                    user_home,
                    f"install -m {mode:o} {shlex.quote(source_path)} "
                    f"{shlex.quote(destination_path)}",
                    check=False,
                    capture_output=True,
                )
                if install_result.returncode != 0:
                    raise RuntimeError(
                        f"Could not install SteamCMD bootstrap for {username}"
                    )

    link_result = _run_as_login_user(
        username,
        user_home,
        f"install -d -m 0755 {shlex.quote(launcher_dir)} && "
        f"ln -sfn {shlex.quote(steamcmd_script)} {shlex.quote(launcher_path)}",
        check=False,
        capture_output=True,
    )
    if link_result.returncode != 0:
        raise RuntimeError(f"Could not install SteamCMD launcher for {username}")
    update_result = _run_as_login_user(
        username,
        user_home,
        f"{shlex.quote(steamcmd_script)} +quit",
        check=False,
        capture_output=True,
    )
    if update_result.returncode != 0:
        detail = (
            update_result.stderr
            or update_result.stdout
            or "SteamCMD self-update failed"
        ).strip()
        raise RuntimeError(f"SteamCMD could not update for {username}: {detail}")
    print(f"  ✓ SteamCMD installed and updated for {username}")
    return changed


def _install_godot_links(release_dir: str) -> None:
    run(
        f"ln -sfn {shlex.quote(release_dir)} {shlex.quote(GODOT_CURRENT_DIR)}",
        check=True,
    )
    for link_path in (GODOT_BINARY_LINK, GODOT4_BINARY_LINK):
        run(
            f"ln -sfn {shlex.quote(GODOT_CURRENT_DIR)}/godot {shlex.quote(link_path)}",
            check=True,
        )


def _install_godot_desktop_entry() -> None:
    write_text_atomic(GODOT_DESKTOP_FILE, _GODOT_DESKTOP_ENTRY, mode=0o644)


def _managed_current_release() -> str | None:
    """Return the active release only when it is below the managed root."""
    current_binary = os.path.join(GODOT_CURRENT_DIR, "godot")
    if not os.path.exists(current_binary):
        return None
    current_release = os.path.realpath(GODOT_CURRENT_DIR)
    releases_root = os.path.realpath(GODOT_RELEASES_DIR)
    try:
        if os.path.commonpath((current_release, releases_root)) != releases_root:
            return None
    except ValueError:
        return None
    return current_release


def install_or_update_godot_release() -> tuple[str, bool, str]:
    """Install the newest verified stable Godot release."""
    release_arch = detect_release_arch()
    asset_arch = _godot_asset_arch(release_arch)
    tag_name, download_url, expected_sha256 = fetch_latest_godot_release(asset_arch)
    release_dir = _godot_release_dir(tag_name, expected_sha256)
    binary_member = f"Godot_v{tag_name}_linux.{asset_arch}"
    installed_state = read_godot_state()
    installed_tag = installed_state.get("tag_name")
    installed_digest = installed_state.get("archive_sha256")
    current_release = _managed_current_release()
    expected_release = os.path.realpath(release_dir)
    current_binary = os.path.join(GODOT_CURRENT_DIR, "godot")

    if (
        installed_tag == tag_name
        and installed_digest == expected_sha256
        and current_release == expected_release
        and os.path.exists(current_binary)
    ):
        _install_godot_links(release_dir)
        _install_godot_desktop_entry()
        print(f"  ✓ Godot already up to date ({tag_name})")
        return tag_name, False, expected_sha256

    if current_release == expected_release and os.path.exists(current_binary):
        print(
            "  ⚠ Godot release metadata is missing or stale; re-verifying the "
            "active release"
        )

    os.makedirs(GODOT_RELEASES_DIR, mode=0o755, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="basaltwater-godot-release-") as temporary_dir:
        archive_path = os.path.join(temporary_dir, "godot.zip")
        run(
            "curl -fL --proto '=https' --proto-redir '=https' "
            f"-o {shlex.quote(archive_path)} {shlex.quote(download_url)}",
            check=True,
            display_cmd=(
                "curl -fL --proto '=https' --proto-redir '=https' "
                f"-o {archive_path} <release URL>"
            ),
        )
        release_binary = _extract_verified_binary(
            archive_path,
            expected_sha256=expected_sha256,
            binary_member=binary_member,
            release_dir=release_dir,
        )
        run(
            f"{shlex.quote(release_binary)} --headless --version",
            check=True,
            capture_output=True,
        )

    try:
        _install_godot_links(release_dir)
        _install_godot_desktop_entry()
        write_godot_state(tag_name, expected_sha256)
    except Exception:
        if current_release and current_release != expected_release:
            try:
                _install_godot_links(current_release)
            except Exception as rollback_exc:
                raise RuntimeError(
                    "Godot activation failed and the previous release could not be restored"
                ) from rollback_exc
        raise
    print(f"  ✓ Installed Godot {tag_name} for graphical and headless use")
    return tag_name, True, expected_sha256


def install_godot(config: SetupConfig) -> None:
    """Install Godot from its verified official release artifact."""
    del config
    if is_dry_run():
        print("  [DRY-RUN] Would install the latest stable Godot release")
        return
    run(
        "apt-get -o DPkg::Lock::Timeout=60 install -y -qq curl ca-certificates",
        check=True,
    )
    install_or_update_godot_release()


def _validated_registered_bundles() -> tuple[list[str], list[str], list[str]]:
    """Return validated bundle and user lists from root-owned state."""
    from lib.config import GODOT_BUNDLES

    state = read_godot_bundle_state()
    raw_bundles = state.get("bundles", [])
    raw_users = state.get("users", [])
    raw_web_identities = state.get("web_identities", [])
    if not isinstance(raw_bundles, list) or not all(
        isinstance(bundle, str) and bundle in GODOT_BUNDLES
        for bundle in raw_bundles
    ):
        raise RuntimeError("Invalid Godot bundle selections in saved state")
    if not isinstance(raw_users, list) or not all(
        isinstance(username, str) and validate_username(username)
        for username in raw_users
    ):
        raise RuntimeError("Invalid Godot bundle users in saved state")
    if not isinstance(raw_web_identities, list) or not all(
        isinstance(identity, str) for identity in raw_web_identities
    ):
        raise RuntimeError("Invalid Godot web identities in saved state")
    from common.godot_web_steps import validate_web_identities

    bundles = [
        bundle for bundle in GODOT_BUNDLES if bundle in set(raw_bundles)
    ]
    users = list(dict.fromkeys(raw_users))
    web_identities = (
        validate_web_identities(raw_web_identities) if raw_web_identities else []
    )
    return bundles, users, web_identities


def _validated_registered_web_access_sources() -> list[str]:
    """Return the saved source policy for managed HTTPS forwarding."""

    state = read_godot_bundle_state()
    raw_sources = state.get("web_access_sources", [])
    if not isinstance(raw_sources, list) or not all(
        isinstance(source, str) for source in raw_sources
    ):
        raise RuntimeError("Invalid Godot web access sources in saved state")
    return list(
        dict.fromkeys(
            validate_network_ip_or_cidr(source, "Godot web access source")
            for source in raw_sources
        )
    )


def _install_selected_godot_bundles(
    bundles: list[str],
    users: list[str],
    web_identities: list[str],
    web_access_sources: list[str] | None = None,
) -> bool:
    """Install or update selected bundles for their registered users."""
    changed = False
    if "web" in bundles:
        for username in users:
            changed = install_or_update_godot_web_bundle(username) or changed
        from common.godot_web_steps import configure_godot_web_host

        changed = configure_godot_web_host(
            web_identities,
            users,
            web_access_sources or [],
        ) or changed
    if "publishing" in bundles:
        _butler_tag, butler_changed, _butler_sha256 = (
            install_or_update_butler_release()
        )
        changed = butler_changed or changed
        if detect_release_arch() == "amd64":
            run(
                "apt-get -o DPkg::Lock::Timeout=60 install -y -qq "
                "lib32gcc-s1 lib32stdc++6",
                check=True,
            )
        for username in users:
            changed = install_or_update_steamcmd(username) or changed
    return changed


def _register_godot_bundles(
    config: SetupConfig,
) -> tuple[list[str], list[str], list[str]]:
    """Merge a successful setup selection into recurring-maintenance state."""
    from lib.config import GODOT_BUNDLES

    from common.godot_web_steps import identities_for_config

    existing_bundles, existing_users, existing_web_identities = (
        _validated_registered_bundles()
    )
    selected = set(existing_bundles)
    selected.update(config.godot_bundles or [])
    bundles = [bundle for bundle in GODOT_BUNDLES if bundle in selected]
    users = list(existing_users)
    if config.username not in users:
        users.append(config.username)
    web_identities = list(existing_web_identities)
    for identity in identities_for_config(config.host, config.system_hostname):
        if identity not in web_identities:
            web_identities.append(identity)
    write_godot_bundle_state(
        bundles,
        users,
        web_identities,
        config.effective_access_sources(),
    )
    return bundles, users, web_identities


def install_godot_bundles(config: SetupConfig) -> None:
    """Install repeatable Godot workflow bundles for the configured user."""
    bundles = list(config.godot_bundles or [])
    if not bundles:
        return
    if is_dry_run():
        print(f"  [DRY-RUN] Would install Godot bundles: {', '.join(bundles)}")
        return
    from common.godot_web_steps import identities_for_config

    web_identities = identities_for_config(config.host, config.system_hostname)
    web_access_sources = config.effective_access_sources()
    _install_selected_godot_bundles(
        bundles,
        [config.username],
        web_identities,
        web_access_sources,
    )
    if "web" in bundles:
        from common.godot_web_steps import configure_godot_agent_skills

        configure_godot_agent_skills(
            config.username,
            config.selected_agent_tools(),
        )
    _register_godot_bundles(config)


def update_registered_godot_bundles() -> bool:
    """Reconcile saved Godot bundles during the weekly Godot update."""
    from common.godot_web_steps import discover_local_web_identities

    bundles, users, web_identities = _validated_registered_bundles()
    web_access_sources = _validated_registered_web_access_sources()
    if not bundles:
        return False
    if not users:
        raise RuntimeError("Godot bundles are registered without a target user")
    if "web" in bundles and not web_identities:
        web_identities = discover_local_web_identities()
        write_godot_bundle_state(
            bundles,
            users,
            web_identities,
            web_access_sources,
        )
    return _install_selected_godot_bundles(
        bundles,
        users,
        web_identities,
        web_access_sources,
    )


def configure_auto_update_godot(config: SetupConfig) -> None:
    """Configure automatic stable Godot release updates."""
    del config
    configured = configure_maintenance_timer(
        service_name="auto-update-godot",
        service_desc="Auto-update Godot Engine",
        timer_desc="Auto-update Godot Engine weekly",
        script_path="/opt/basaltwater/common/service_tools/auto_update_godot.py",
        schedule="Sun *-*-* 06:30:00",
        check_path=GODOT_BINARY_LINK,
        check_name="Godot",
        purpose="auto-update",
    )
    if not configured:
        raise RuntimeError("Godot auto-update timer failed verification")
