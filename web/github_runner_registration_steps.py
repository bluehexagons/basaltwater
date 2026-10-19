"""Install the panel's narrowly scoped socket-activated registration helper."""

from __future__ import annotations

import pwd

from lib.github_runner_config import APP, DATA
from lib.github_runner_registration import SOCKET
from lib.unit_transaction import remove_units, replace_units, snapshot_unit_file
from web.github_runner_steps import runner_lock


SOCKET_UNIT = "basaltwater-github-runner-registration.socket"
SERVICE_UNIT = "basaltwater-github-runner-registration@.service"
_MARKER = "# Managed by Basaltwater runner registration"


def preflight_registration_units() -> None:
    for unit in (SOCKET_UNIT, SERVICE_UNIT):
        previous = snapshot_unit_file("/etc/systemd/system/" + unit)
        if previous and _MARKER not in previous["content"]:
            raise ValueError("Refusing unmanaged runner registration unit")


def configure_registration_service(account: pwd.struct_passwd) -> None:
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise ValueError("Runner registration requires an unprivileged panel account")
    preflight_registration_units()
    socket_unit = f"""{_MARKER}
[Unit]
Description=Volatile GitHub runner registration socket

[Socket]
ListenStream={SOCKET}
SocketUser=root
SocketGroup={account.pw_gid}
SocketMode=0660
Accept=yes
MaxConnections=1
Backlog=1
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
"""
    service_unit = f"""{_MARKER}
[Unit]
Description=Register the pending GitHub runner
PartOf={SOCKET_UNIT}

[Service]
Type=exec
User=root
UMask=0077
ExecStart=/usr/bin/python3 -I /opt/basaltwater/common/service_tools/github_runner_registration.py --panel-uid {account.pw_uid}
StandardInput=socket
StandardOutput=null
StandardError=null
RuntimeMaxSec=240
TimeoutStopSec=10
KillMode=control-group
LimitCORE=0
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths={APP} {DATA} /etc/basaltwater /etc/systemd/system /run
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
"""
    with runner_lock():
        replace_units({SOCKET_UNIT: socket_unit, SERVICE_UNIT: service_unit}, activate=(SOCKET_UNIT,))


def remove_registration_service() -> None:
    preflight_registration_units()
    if any(snapshot_unit_file("/etc/systemd/system/" + unit) for unit in (SOCKET_UNIT, SERVICE_UNIT)):
        with runner_lock():
            remove_units((SOCKET_UNIT, SERVICE_UNIT))
