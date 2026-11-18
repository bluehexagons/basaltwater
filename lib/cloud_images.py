#!/usr/bin/env python3
"""Curated catalog of Debian cloud images for VM provisioning.

The entries here pin a specific snapshot of a Debian generic-cloud image so that
``provision_vm`` produces reproducible results. Refresh with
``scripts/update_cloud_images.py``.

Only Debian is supported. Each ``base`` key maps to a snapshot dict containing:

- ``codename``: Debian release codename (``bookworm``, ``trixie``, ...).
- ``version``: Debian major version as a string.
- ``snapshot``: Concrete upstream snapshot directory, e.g. ``20250115-2001``.
- ``filename``: The qcow2 filename inside that snapshot directory.
- ``url``: Fully-qualified download URL to the qcow2.
- ``sha512``: Required SHA-512 digest of the qcow2 (lowercase hex).
"""

from __future__ import annotations

import re
from typing import Mapping, Optional, TypeGuard, TypedDict
from urllib.parse import urlsplit

from lib.validation import validate_no_control_characters


class CloudImage(TypedDict):
    codename: str
    version: str
    snapshot: str
    filename: str
    url: str
    sha512: str


DEFAULT_DEBIAN_BASE = "debian"
DEBIAN_CLOUD_BASE = "https://cloud.debian.org/images/cloud"
SNAPSHOT_PATTERN = re.compile(r"[0-9]{8}-[0-9]{3,4}")

# NOTE: Refresh with `python3 scripts/update_cloud_images.py`. The script
# overwrites the dictionary literal below; preserve the ``# BEGIN/END
# CLOUD_IMAGES`` markers.

# The unversioned Debian alias follows current stable; numbered aliases remain
# available when a specific release is required.
# BEGIN CLOUD_IMAGES
CLOUD_IMAGES: dict[str, CloudImage] = {
    "debian": {
        "codename": "trixie",
        "version": "13",
        "snapshot": "20260803-2559",
        "filename": "debian-13-genericcloud-amd64-20260803-2559.qcow2",
        "url": "https://cloud.debian.org/images/cloud/trixie/20260803-2559/debian-13-genericcloud-amd64-20260803-2559.qcow2",
        "sha512": "769562604ecaac26b661167891ef922f71f4d87d50a11423fc04e51444fda0d882c87996dd1181170d233627f4728e6722db2695c0ef753dad762c4ac4ed32e1",
    },
    "debian-12": {
        "codename": "bookworm",
        "version": "12",
        "snapshot": "20260806-2562",
        "filename": "debian-12-genericcloud-amd64-20260806-2562.qcow2",
        "url": "https://cloud.debian.org/images/cloud/bookworm/20260806-2562/debian-12-genericcloud-amd64-20260806-2562.qcow2",
        "sha512": "3622c990108a044ed411652f8741e77c5822c365114d7b940206b243f8fb617b8586792df4cdb7afba1b71d1a09289d8ed632124688f2c8352cb08190a1e9868",
    },
    "debian-13": {
        "codename": "trixie",
        "version": "13",
        "snapshot": "20260803-2559",
        "filename": "debian-13-genericcloud-amd64-20260803-2559.qcow2",
        "url": "https://cloud.debian.org/images/cloud/trixie/20260803-2559/debian-13-genericcloud-amd64-20260803-2559.qcow2",
        "sha512": "769562604ecaac26b661167891ef922f71f4d87d50a11423fc04e51444fda0d882c87996dd1181170d233627f4728e6722db2695c0ef753dad762c4ac4ed32e1",
    },
}
# END CLOUD_IMAGES


def is_sha512_digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{128}", value) is not None


def debian_image_location(codename: str, version: str, snapshot: str) -> tuple[str, str]:
    """Construct the image name and URL from validated Debian snapshot coordinates."""
    if (not re.fullmatch(r"[a-z]+", codename)
            or not re.fullmatch(r"[1-9][0-9]*", version)
            or not SNAPSHOT_PATTERN.fullmatch(snapshot)):
        raise ValueError("Debian cloud images require a release and concrete snapshot")
    filename = f"debian-{version}-genericcloud-amd64-{snapshot}.qcow2"
    return filename, f"{DEBIAN_CLOUD_BASE}/{codename}/{snapshot}/{filename}"


def validate_cloud_catalog(entries: Mapping[str, Mapping[str, str]]) -> None:
    """Verify every alias points to a digest-pinned Debian snapshot."""
    if DEFAULT_DEBIAN_BASE not in entries:
        raise ValueError("Cloud image catalog is missing the default Debian alias")
    for key, image in entries.items():
        try:
            filename, url = debian_image_location(image["codename"], image["version"], image["snapshot"])
            if image.get("filename") != filename or image.get("url") != url:
                raise ValueError("filename and URL must identify the pinned snapshot")
            if not is_sha512_digest(image.get("sha512")):
                raise ValueError("expected a 128-character SHA-512 digest")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid cloud image catalog entry {key!r}: {exc}") from exc
    for key, image in entries.items():
        numbered = f"debian-{image['version']}"
        if entries.get(numbered) != image or (re.fullmatch(r"debian-[0-9]+", key) and key != numbered):
            raise ValueError(f"Invalid cloud image catalog entry {key!r}: alias must match {numbered}")


def resolve_cloud_image(base: str) -> CloudImage:
    """Return the catalog entry for ``base`` (case-insensitive)."""
    key = (base or "").strip().lower()
    if not key:
        key = DEFAULT_DEBIAN_BASE
    if key in CLOUD_IMAGES:
        validate_cloud_catalog(CLOUD_IMAGES)
        return CLOUD_IMAGES[key]
    raise ValueError(
        f"No cloud image registered for base {base!r}. "
        f"Known: {', '.join(sorted(CLOUD_IMAGES))}"
    )


def list_cloud_images() -> list[tuple[str, CloudImage]]:
    """Return all catalog entries, sorted by key."""
    validate_cloud_catalog(CLOUD_IMAGES)
    return sorted(CLOUD_IMAGES.items())


def cloud_image_local_filename(image: CloudImage) -> str:
    """Return the on-host filename used after download into image storage."""
    return image["filename"]


def is_local_image_ref(ref: str) -> bool:
    """True when ``ref`` is a Proxmox ISO or import storage reference."""
    if not ref:
        return False
    if ":" not in ref:
        return False
    head, _, tail = ref.partition(":")
    if not head or not tail:
        return False
    return any(
        tail.startswith(prefix) and len(tail) > len(prefix)
        for prefix in ("iso/", "import/")
    )


def parse_image_argument(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split ``--image`` into ``(url, storage_ref)``.

    Returns a ``(url, None)`` tuple when ``value`` is an HTTPS URL, a
    ``(None, storage_ref)`` tuple when it looks like ``storage:import/foo.qcow2``
    or ``storage:iso/foo.img``,
    or ``(None, None)`` when ``value`` is empty.
    """
    if not value:
        return None, None
    stripped = value.strip()
    if stripped.startswith("https://"):
        cloud_image_url_filename(stripped)
        return stripped, None
    if is_local_image_ref(stripped):
        _, _, volume = stripped.partition(":")
        content_type, _, filename = volume.partition("/")
        if content_type == "iso" and filename.lower().endswith((".qcow", ".qcow2")):
            raise ValueError(
                f"Invalid --image storage reference: {stripped!r}. "
                "qcow2 images require the Proxmox 'import' content type; "
                "use STORAGE:import/FILE or provide the image URL with "
                "--image-storage STORAGE."
            )
        return None, stripped
    raise ValueError(
        f"Invalid --image value: {value!r}. "
        "Expected an https:// URL or a Proxmox storage reference like "
        "'local:import/foo.qcow2'."
    )


def cloud_image_url_filename(url: str) -> str:
    """Return a safe storage basename from an HTTPS image URL."""
    validate_no_control_characters(url, "VM image URL")
    parsed = urlsplit(url)
    filename = parsed.path.rsplit("/", 1)[-1]
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename)):
        raise ValueError("Expected an HTTPS VM image URL with a valid storage filename")
    return filename
