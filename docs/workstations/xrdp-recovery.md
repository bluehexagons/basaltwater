# Migrate or recover an XRDP setup

[Topic index](xrdp.md)

## Migration and recovery

All desktop profiles and `local desktop` use this stack. An explicit `--desktop`
or `--rdp` enables it on server/agent profiles; `agent_vm` remains headless by
default. `--no-rdp` retains a desktop profile's IPv4 loopback listener, including
for Guacamole, and removes the managed direct RDP firewall rules and jail. A
setup rerun that returns to a headless profile also stops and disables the
previously managed xRDP frontend. Desktop packages and user data stay installed.

Run setup from SSH or a text console when desktop work is saved. Before package
upgrades, setup automatically requests normal logout of the configured account's
managed desktop and waits up to 60 seconds plus
in-flight request time. It then allows up to 10 seconds for logind/sesman cleanup.
If the supervisor disconnects during logout, setup rechecks status within the
same deadline instead of resending logout or assuming it finished. A connection
reset, broken pipe or incomplete reply alone is not proof of shutdown; setup
still requires stopped status and the subsequent graphical-session checks.
Persistent disconnections, permission/protocol errors and replacement sessions
continue to block setup. This handling also works with older running supervisors.
If a session disappears between listing it and reading its properties, setup
confirms its absence with a fresh session list and continues quietly. Older
versions could retain `Failed to get path for session …: No session … known`
in the final run notes even after logout and setup succeeded; that teardown
race is harmless. A failed session list or an unreadable session that still
exists continues to block desktop setup, as does a remaining XRDP process.
No separate logout command or confirmation is required. A canceled logout stops
setup; no applications are force-killed. Disable
RDP automatic reconnect during setup. A replacement generation aborts the logout
wait, and desktop steps recheck idle state before cutover. Unmanaged/legacy
X11/Wayland or XRDP sessions still require manual logout. Setup leaves the desktop
stopped until the next explicit start or authenticated RDP login.
The setup masks GDM, LightDM, SDDM, LXDM and XDM plus the systemd display
manager alias, so graphical console login cannot create a second desktop.
The VM/hardware console becomes a text/recovery surface; direct physical-console
GUI attachment is not provided. Existing application packages and home data stay.
Unknown display-manager aliases require operator migration. OCI and
`--harden-user` configurations are rejected because persistent user services
are required. XFCE is the supported default; other desktop environments and
LXC have only basic, unqualified coverage.

The dedicated `basaltwater-desktop` group contains only the configured owner. SSH's
`remoteusers` group remains separate. User window-manager overrides and alternate
RDP shells are disabled. Legacy `~/startwm.sh` files are no longer executed.
The startup wrapper owns a private D-Bus session; applications launched by the
CLI join its desktop process group. Teardown never kills the entire user manager.
Applications explicitly launched as independent user services retain that
service's lifecycle.

Setup installs `/etc/systemd/system/xrdp-sesman.service` from the packaged unit,
removing its `BindsTo=xrdp.service` dependency and disabling `StopWhenUnneeded`.
This requires a full unit override: systemd cannot remove dependencies through
drop-ins. Setup refreshes the copy on reruns, preserves other packaged settings,
and rejects existing custom units or effective dependencies that still tie
sesman to the frontend. Rerun desktop setup after XRDP package upgrades to
incorporate vendor unit changes. The obsolete `shared-desktop.conf` drop-in is
removed during migration.

Managed XRDP files keep first-install `.bak` copies. Desktop configuration is
versioned at `/etc/basaltwater/desktop.json`, with a `.json.bak` on replacement.
The former display-manager symlink is retained as
`/etc/systemd/system/display-manager.service.basaltwater-backup`.
Setup failure stops the setup operation; inspect its error and rerun during a
session-free window. There is no automatic rollback that logs users out.
For administrator rollback, first log out, stop XRDP, restore the backed-up
XRDP files and desired display-manager alias, remove the managed
`/etc/systemd/system/xrdp-sesman.service` override and run `systemctl daemon-reload`,
unmask only the former display
manager and `display-manager.service`, and deliberately start that service.
Rollback restores configuration, not unsaved applications. Keep SSH available.
