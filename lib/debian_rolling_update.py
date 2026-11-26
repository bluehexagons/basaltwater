"""Target-side Debian guest checks and package commands for rolling updates."""

from __future__ import annotations

import shlex

from lib.maintenance_defaults import (
    APT_DPKG_OPTIONS, APT_LOCK_OPTIONS, APT_UPDATE_OPTIONS, APT_UPGRADE_SAFETY_OPTIONS,
)


DEBIAN_RELEASES = {"bookworm": "12", "trixie": "13"}
# A separate exit status lets the controller retry an explicitly blocked attempt
# without replaying an installation whose outcome is unknown.
DEBIAN_UPDATE_BLOCKED = 75
_UPGRADE_OPTIONS = [*APT_UPGRADE_SAFETY_OPTIONS, "--with-new-pkgs"]

# Run against the target's APT state, using its existing maintenance lock. The
# program travels with the SSH request so a guest needs no new runtime deployed.
_GUEST_PROGRAM = r'''
import json
import os
import platform
import re
import shutil
import subprocess
import sys


def capture(*args):
    return subprocess.run(args, text=True, capture_output=True, check=False)


def check_guest(expected):
    if os.path.isdir('/etc/pve') or shutil.which('pveversion'):
        raise RuntimeError('Proxmox host requires a saved server_proxmox setup')
    release = platform.freedesktop_os_release()
    codename = release.get('VERSION_CODENAME')
    if release.get('ID') != 'debian' or codename not in releases:
        raise RuntimeError('Debian 12 (bookworm) or 13 (trixie) is required')
    if expected and codename != expected:
        raise RuntimeError('Debian release changed since preflight')
    virtualization = capture('systemd-detect-virt', '--container')
    if (virtualization.returncode, virtualization.stdout.strip()) != (1, 'none'):
        raise RuntimeError('Debian rolling updates require a system outside a container')
    health = capture('systemctl', 'is-system-running', '--wait')
    if health.returncode or health.stdout.strip() != 'running':
        raise RuntimeError('Debian systemd health check failed; inspect systemctl --failed')
    audit = capture('dpkg', '--audit')
    if audit.returncode or audit.stdout.strip() or audit.stderr.strip():
        raise RuntimeError('dpkg reports an incomplete transaction; repair it first')
    return codename


def read_policy(*packages):
    policy = capture('apt-cache', '-o', 'Dir::Cache::pkgcache=', '-o',
                     'Dir::Cache::srcpkgcache=', 'policy', *packages)
    if policy.returncode:
        raise RuntimeError('Could not inspect cached APT policy')
    return policy.stdout


def simulate(codename):
    suites = {codename, *(codename + suffix for suffix in ('-security', '-updates', '-backports'))}
    debian_suites = set()
    for line in read_policy().splitlines():
        line = line.strip()
        if line.startswith('release '):
            fields = dict(field.split('=', 1) for field in line[8:].split(',') if '=' in field)
            if fields.get('o') in {'Debian', 'Debian Backports'}:
                debian_suites.add(fields.get('n', ''))
    if codename not in debian_suites:
        raise RuntimeError('No cached APT index for the installed Debian release; check sources and refresh APT')
    if debian_suites - suites:
        raise RuntimeError('APT has indexes for another Debian release; review repository suites')
    major = releases[codename]
    candidate = re.search(r'^\s*Candidate:\s+([0-9]+)(?:[.+~\-]|$)', read_policy('base-files'), re.M)
    if not candidate or candidate[1] != major:
        raise RuntimeError('APT base-files candidate is outside the installed Debian release')
    plan = capture('apt-get', '--simulate', '-q', *cache_options, *upgrade_options, 'upgrade')
    actions = plan.stdout.splitlines()
    if plan.returncode or any(line.startswith(('Remv ', 'Purg ')) for line in actions):
        raise RuntimeError('APT could not plan a same-release upgrade without package removals')
    if any(re.match(r'^Inst (?:pve-manager|proxmox-ve)(?:[: ])', line) for line in actions):
        raise RuntimeError('APT would install a Proxmox host package on a Debian target')
    return sum(line.startswith('Inst ') for line in actions)


sys.path.insert(0, '/opt/basaltwater')
try:
    from lib.maintenance_lock import maintenance_lock
except ImportError:
    sys.exit('Debian rolling updates require the installed Basaltwater maintenance runtime')

action, expected = sys.argv[1:]
os.environ['LC_ALL'] = 'C'
os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
if action != 'apply':
    try:
        codename = check_guest(expected)
        packages = simulate(codename) if action == 'plan' else None
    except (OSError, RuntimeError) as exc:
        sys.exit(str(exc))
    print(json.dumps({'codename': codename, 'packages': packages,
                      'reboot_required': os.path.isfile('/run/reboot-required')}))
    sys.exit(0)

installing = False
try:
    with maintenance_lock() as acquired:
        if not acquired:
            raise RuntimeError('Setup, maintenance or a build is active on the Debian target')
        codename = check_guest(expected)
        subprocess.run(['apt-get', *refresh_options, 'update'], check=True)
        simulate(codename)
        installing = True
        subprocess.run(['apt-get', '-y', *install_options, 'upgrade'], check=True)
except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1 if installing else blocked_status)
'''


def debian_update_command(action: str, codename: str = "") -> str:
    if action not in {"plan", "health", "apply"} or (codename and codename not in DEBIAN_RELEASES):
        raise ValueError("Invalid Debian rolling-update probe")
    settings = {
        "releases": DEBIAN_RELEASES,
        "blocked_status": DEBIAN_UPDATE_BLOCKED,
        "cache_options": ["-o", "Dir::Cache::pkgcache=", "-o", "Dir::Cache::srcpkgcache="],
        "upgrade_options": _UPGRADE_OPTIONS,
        "refresh_options": [*APT_LOCK_OPTIONS, *APT_UPDATE_OPTIONS],
        "install_options": [*APT_LOCK_OPTIONS, *APT_DPKG_OPTIONS, *_UPGRADE_OPTIONS],
    }
    preamble = "".join(f"{name} = {value!r}\n" for name, value in settings.items())
    return shlex.join(["/usr/bin/python3", "-IB", "-c", preamble + _GUEST_PROGRAM, action, codename])
