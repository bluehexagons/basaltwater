"""Read-only physical capacity admission for controller-relayed builds."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from lib.build_protocol import MAX_DESCRIPTOR_BYTES
from lib.ssh_utils import build_ssh_command, strict_ssh_transport
from lib.validation import validate_filesystem_path


RESERVE = 512 * 1024**2
INODE_RESERVE = 10000

# Standard-library-only probe works before upgrading the target installation.
# Inputs are passed as a quoted argv document, never interpolated shell code.
PROBE = r'''import json,os,re,sys
requests=json.loads(sys.argv[1]); rows=[]
with open('/proc/self/mountinfo') as stream:
    mounts=[re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),line.split()[4]) for line in stream]
role=sys.argv[2]; roots={}
if role:
    config='/etc/basaltwater/build-'+role+'/config.json'
    if os.path.lexists(config):
        fd=os.open(config,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            raw=os.read(fd,8*1024*1024+1)
            if len(raw)>8*1024*1024: raise ValueError('oversized role configuration')
            roots=json.loads(raw)['roots']
            required={'admission','artifacts','releases','verify'} if role=='target' else {'state','inputs','scratch','artifacts','logs'}
            if not isinstance(roots,dict) or set(roots)!=required: raise ValueError('incomplete role roots')
        finally: os.close(fd)
for path,needed,entries in requests:
    logical=path
    prefix='/var/lib/basaltwater/build-'+role+'/'
    if role and path.startswith(prefix): path=roots.get(path[len(prefix):],path)
    if not isinstance(path,str) or not path.startswith('/') or os.path.normpath(path)!=path:
        raise ValueError('invalid role root')
    parent=path
    while not os.path.exists(parent): parent=os.path.dirname(parent)
    parent=os.path.realpath(parent); info=os.stat(parent); usage=os.statvfs(parent)
    mount=max((point for point in mounts if point=='/' or parent==point or parent.startswith(point+'/')),key=len)
    rows.append(dict(path=logical,observed_path=path,mount=mount,device=info.st_dev,
        free_bytes=usage.f_bavail*usage.f_frsize,free_inodes=usage.f_favail,
        inodes_supported=usage.f_files!=0,required_bytes=needed,required_inodes=entries))
print(json.dumps(rows))
'''


def check_observations(rows: object, requests: list[list]) -> list[dict]:
    """Combine concurrent allocations by filesystem, leaving one reserve."""
    if not isinstance(rows, list) or len(rows) != len(requests):
        raise ValueError("Storage probe returned an incomplete inventory")
    groups = {}
    for row, (path, needed, entries) in zip(rows, requests):
        if (not isinstance(row, dict) or row.get("path") != path
                or row.get("required_bytes") != needed or row.get("required_inodes") != entries
                or type(row.get("inodes_supported")) is not bool
                or any(type(row.get(key)) is not int or row[key] < 0
                       for key in ("device", "free_bytes", "free_inodes"))
                or not isinstance(row.get("mount"), str)):
            raise ValueError("Storage probe returned invalid observations")
        validate_filesystem_path(row["mount"])
        actual = row.get("observed_path", path)
        validate_filesystem_path(actual)
        if not os.path.isabs(actual) or os.path.normpath(actual) != actual:
            raise ValueError("Storage probe returned an invalid physical path")
        reserve = 64 * 1024**2 if path == "/tmp" else RESERVE
        group = groups.setdefault(row["device"], dict(
            mount=row["mount"], paths=[], free_bytes=row["free_bytes"],
            free_inodes=row["free_inodes"], required_bytes=0, reserve_bytes=reserve,
            required_inodes=0, inodes_supported=row["inodes_supported"],
        ))
        group["reserve_bytes"] = max(group["reserve_bytes"], reserve)
        group["paths"].append(actual)
        group["free_bytes"] = min(group["free_bytes"], row["free_bytes"])
        group["free_inodes"] = min(group["free_inodes"], row["free_inodes"])
        group["required_bytes"] += needed
        group["required_inodes"] += entries
    result = list(groups.values())
    for group in result:
        group["required_bytes"] += group["reserve_bytes"]
        group["required_inodes"] += (1000 if group["paths"] == ["/tmp"] else INODE_RESERVE)
        group["shortfall_bytes"] = max(0, group["required_bytes"] - group["free_bytes"])
        group["shortfall_inodes"] = (max(0, group["required_inodes"] - group["free_inodes"])
                                      if group["inodes_supported"] else 0)
    return result


def probe(endpoint, requests: list[list], *, phase: str, role: str = "") -> list[dict]:
    if role not in {"", "host", "target"}:
        raise ValueError("Invalid storage role")
    for path, needed, entries in requests:
        validate_filesystem_path(path)
        if not os.path.isabs(path) or type(needed) is not int or type(entries) is not int or min(needed, entries) < 0:
            raise ValueError("Invalid storage admission request")
    with strict_ssh_transport(endpoint.host, identity_file=endpoint.identity_file,
                              known_hosts_path=endpoint.known_hosts_file, port=endpoint.port):
        command = build_ssh_command(endpoint.host, "root", remote_command=shlex.join(
            ["python3", "-I", "-B", "-c", PROBE, json.dumps(requests), role]))
    response = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=True)
    if len(response.stdout) > 64 * 1024:
        raise ValueError("Storage probe response is oversized")
    rows = check_observations(json.loads(response.stdout), requests)
    print(f"  Storage preflight {phase}: {json.dumps(rows)}")
    if any(row["shortfall_bytes"] or row["shortfall_inodes"] for row in rows):
        raise ValueError(f"Insufficient storage during {phase}: {json.dumps(rows)}; "
                         "preview managed cleanup, lower reviewed request ceilings or increase capacity")
    return rows


def preflight_delivery_storage(config, selected, endpoint, plans) -> dict:
    """Budget all retained bundles plus extraction/copy peaks before bootstrap.

    Existing live releases already consume observed blocks. New staging and
    verification copies are additional allocations. Conservative entry allowances
    include block rounding and directory/inode metadata; these are not quotas.
    """
    target = [["/var/lib/basaltwater/build-target/" + name, 0, 0]
              for name in ("artifacts", "verify", "releases", "admission")]
    builder = [["/var/lib/basaltwater/build-host/" + name, 0, 0]
               for name in ("inputs", "scratch", "artifacts", "logs", "state")]
    scratch = 0
    packaging = 0
    for plan in plans:
        limits = plan.request["limits"]
        size = limits["artifact_bytes"]
        entries = limits["artifact_entries"] + 8
        # All bundles stay staged. Verification and release copying may overlap.
        target[0][1] += size + MAX_DESCRIPTOR_BYTES
        target[1][1] += size + entries * 8192
        target[0][2] += 8
        target[1][2] += entries
        root = config.build_target_enrollment["policy"]["applications"][plan.deployment["application"]]["deployment_root"]
        # Delivered live copies remain while final-profile reconciliation
        # prepares another copy. Do not assume the old source release is freed.
        target.append([os.path.dirname(root), 2 * size + entries * 16384, 2 * entries])
        builder[0][1] += os.stat(plan.source_path).st_size
        builder[2][1] += size + MAX_DESCRIPTOR_BYTES
        builder[3][1] += limits["log_bytes"]
        builder[0][2] += 16
        scratch = max(scratch, limits["scratch_bytes"])
        packaging = max(packaging, 2 * size + MAX_DESCRIPTOR_BYTES + entries * 8192)
    builder[1][1] += scratch
    builder[1][2] += max(plan.request["limits"]["artifact_entries"] for plan in plans) + 16
    # Export snapshot and bundle temporary trees live beside final artifacts.
    builder[2][1] += packaging
    builder[2][2] += max(plan.request["limits"]["artifact_entries"] for plan in plans) + 16
    # Include setup's separate temporary mount without charging full artifacts
    # to tmpfs: artifact extraction/staging uses explicit private roots.
    target.append(["/tmp", 128 * 1024**2, 1000])
    builder.append(["/tmp", 128 * 1024**2, 1000])
    return {"target": probe(endpoint, target, phase="target delivery", role="target"),
            "builder": probe(selected.endpoint, builder, phase="builder admission", role="host")}
