"""Typed Debian software selections and the APT transaction presented for review."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

from lib.validation import validate_package_name
from lib.privilege_execution import validate_runtime

JUPYTERLAB_PACKAGES = ("jupyterlab", "jupyter-core", "python3-jupyterlab-server", "python3-jupyter-server", "python3-ipykernel")
AV_TOOL_PACKAGES = ("ffmpeg", "imagemagick", "libimage-exiftool-perl")
GL_TOOL_PACKAGES = ("apitrace", "mesa-utils")
WORKER = "/opt/basaltwater/common/service_tools/software_install.py"
SOFTWARE_FLAGS = {
    "jupyterlab": ("install_jupyterlab", JUPYTERLAB_PACKAGES),
    "av-tools": ("install_av_tools", AV_TOOL_PACKAGES),
    "gl-tools": ("install_gl_tools", GL_TOOL_PACKAGES),
}
SOFTWARE_PROFILES = {"agent_vm", "agent_vm_lite", "agent_code_vm", "agent_workstation"}


def selection(parameters: object) -> tuple[str, tuple[str, ...]]:
    if not isinstance(parameters, dict) or parameters.keys() - {"runtime_seconds"} != {"kind", "name", "scope"}:
        raise ValueError("Choose one software flag or Debian package and its scope")
    if "runtime_seconds" in parameters:
        validate_runtime(parameters["runtime_seconds"])
    name = parameters["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", name):
        raise ValueError("Invalid software name")
    if parameters["scope"] not in ("saved", "temporary"):
        raise ValueError("Choose saved or temporary scope")
    if parameters["kind"] == "apt":
        return "apt_packages", (validate_package_name(name),)
    if parameters["kind"] != "flag" or name not in SOFTWARE_FLAGS:
        raise ValueError("Unsupported software setup flag")
    return SOFTWARE_FLAGS[name]


def save_selection(config: dict, parameters: dict) -> dict:
    """Change only the selected field in the host's existing declaration."""
    field, packages = selection(parameters)
    if parameters["scope"] != "saved":
        raise ValueError("A temporary selection cannot be saved")
    if config.get("system_type") not in SOFTWARE_PROFILES:
        raise ValueError("Software requests require a Debian agent profile")
    if field == "apt_packages":
        previous = config.get(field)
        if previous is None:
            previous = []
        if not isinstance(previous, list) or not all(isinstance(item, str) for item in previous):
            raise ValueError("Invalid saved APT selection")
        return {**config, field: list(dict.fromkeys([*previous, *packages]))}
    return {**config, field: True}


def apt_plan(parameters: dict):
    """Resolve once. The worker commits this same cache under APT's system lock."""
    import apt
    import apt_pkg

    field, packages = selection(parameters)
    audit = subprocess.run(["/usr/bin/dpkg", "--audit"], capture_output=True, timeout=15, check=False)
    if audit.returncode or audit.stdout.strip():
        raise ValueError("Resolve the existing dpkg audit findings before installing software")
    if field == "install_jupyterlab":
        # Each plan runs in a fresh Debian Python process with its own APT configuration.
        apt_pkg.init_config()
        apt_pkg.config.set("APT::Install-Recommends", "false")
    cache = apt.Cache(memonly=True)
    if cache.broken_count or cache.dpkg_journal_dirty:
        raise ValueError("Repair the existing APT transaction before requesting software")
    for name in packages:
        if name not in cache or cache[name].name != name or cache[name].candidate is None:
            raise ValueError(f"Package unavailable from configured APT indexes: {name}")
        if cache[name].is_installed and cache[name]._pkg.current_state != apt_pkg.CURSTATE_INSTALLED:
            raise ValueError(f"Repair the incomplete Debian package first: {name}")
        if not cache[name].is_installed:
            cache[name].mark_install(auto_fix=True, auto_inst=True)
    if cache.broken_count:
        raise ValueError("APT could not satisfy the requested dependencies")
    changes = {pkg.fullname: pkg for pkg in cache.get_changes()}
    rows, selected = [], []
    reviewed = {cache[name].fullname: cache[name] for name in packages}
    reviewed.update(changes)
    for fullname, pkg in sorted(reviewed.items()):
        changed = fullname in changes
        version = pkg.candidate if changed else pkg.installed
        if (version is None or pkg.marked_delete or pkg.marked_downgrade
                or changed and pkg._pkg.selected_state == apt_pkg.SELSTATE_HOLD):
            raise ValueError("Software requests cannot remove, downgrade or change held packages")
        origins = [o for o in version.origins if o.archive != "now"]
        if (not origins or any(not o.site or o.origin != "Debian" or not o.trusted for o in origins)
                or not re.fullmatch(r"[0-9a-f]{64}", version.sha256 or "")):
            raise ValueError(f"A version from a trusted Debian archive is required for {pkg.name}")
        row = {"package": fullname, "version": version.version, "sha256": version.sha256,
               "previous": pkg.installed.version if pkg.installed else None,
               "sources": sorted({f"{o.site} {o.archive}" for o in origins})}
        if changed:
            rows.append(row)
        if pkg.name in packages:
            selected.append(row)
    digest = hashlib.sha256(json.dumps([selected, rows], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    summary = {"digest": digest, "requested": list(packages), "changes": len(rows),
               "upgrades": sum(row["previous"] is not None for row in rows),
               "download_bytes": sum(pkg.candidate.size for pkg in changes.values()),
               "disk_bytes": cache.required_space, "packages": rows[:8]}
    return cache, summary, rows


def reviewed_install(parameters: dict) -> tuple[dict, list[str], str]:
    from lib.privilege_policy import protected_path
    selection(parameters)
    protected_path(WORKER)
    args = [parameters["scope"], parameters["kind"], parameters["name"]]
    command = ["/usr/bin/python3", "-I", WORKER]
    try:
        result = subprocess.run([*command, "preview", *args], capture_output=True, text=True, timeout=45, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("The software plan exceeded its planning deadline") from exc
    if result.returncode:
        raise ValueError("Software plan unavailable. Check the Debian indexes and installed broker configuration.")
    summary = json.loads(result.stdout)
    if not re.fullmatch(r"[0-9a-f]{64}", summary["digest"]):
        raise ValueError("Invalid software plan digest")
    effect = (f"Install {parameters['name']} from Debian. APT changes {summary['changes']} packages "
              f"including {summary['upgrades']} upgrades. Archive bytes: {summary['download_bytes']}. "
              f"Additional disk bytes: {summary['disk_bytes']}. Package maintainer scripts may start services. ")
    effect += ("Save this selection on the controller for rebuilds before installing." if parameters["scope"] == "saved"
               else "Install on this host only. Installed packages remain on the host after the task.")
    return summary, [*command, "apply", *args, summary["digest"]], effect
