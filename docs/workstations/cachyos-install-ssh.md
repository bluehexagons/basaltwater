# CachyOS SSH setup

[Topic index](cachyos-install.md)

## SSH access

Local setup resolves whether SSH is selected from the workstation's current
service state. An active or enabled native `sshd.service`, `sshd.socket`, or
Basaltwater-managed SSH service means SSH stays enabled; a disabled or absent
service means it stays off. Merely having OpenSSH installed does not count as
enablement. An existing valid SSH session also counts as enabled. Use
`--ssh` or `--no-ssh` to choose explicitly. If local setup cannot determine the
state, apply requires one of those flags; a dry-run reports that selection is
deferred. Remote-controller setup defaults to SSH; `--no-ssh` opts out.

On a workstation without recognized SSH, Basaltwater can install its
root-managed `basaltwater-cachyos-ssh.service` for the existing non-root desktop
account on IPv4 port 22, with key-only authentication and no KDE login
requirement. It does not rewrite distro SSH configuration. A recognizable
packaged native SSH service can be safely adopted after validation, preserving
existing SSH sessions. Foreign custom units, drop-ins, and unknown port-22
listeners are refused for review; Basaltwater does not take them over. Turning
SSH off stops the managed service and recognizable stock native SSH, while
foreign custom SSH remains untouched and can make disablement fail safely.

### Install the administrator SSH helper

Before enabling, adopting, repairing, or removing SSH, provision the SSH
helper separately on the workstation. This also applies when setup detects
existing SSH or runs from a remote controller. A workstation with SSH already
off and no managed service to remove can complete setup without the helper.

1. Obtain the exact reviewed Basaltwater commit ID through a trusted release or
   administrator channel. As administrator, fetch from the official repository
   into a fresh root-owned staging directory under `/opt`. Verify that the
   fetched commit matches the approved ID before exporting files.
2. Export the commit's `common/`, `lib/`, and `plugins/` trees into a new directory
   in that staging area. Install the directory as `/opt/basaltwater-cachyos-ssh` with its
   contents owned by root, ordinary files and directories readable by the
   desktop account, and no group/other write access or symlinks. The entry
   point is `common/service_tools/cachyos_ssh.py` within that directory. Keep
   the complete import trees from the same approved commit.
3. Run the normal setup command as the desktop account. Run it again after
   provisioning a replacement helper when updating the SSH lifecycle code.
   Stage and verify the replacement separately before publishing it.

Desktop upgrades and controller uploads leave the administrator installation
alone. The setup preflight checks ownership and permissions before sudo. The
installed helper uses isolated system Python, verifies its import trees, and
uses native system commands with a fixed environment. Keep the administrator
installation in place through managed SSH removal with `--no-ssh`.

### Configure SSH access

When SSH is enabled without explicit access flags, setup automatically enables
private IPv4 LAN access for the selected T3, Sunshine, and SSH firewall scopes.
LAN discovery runs on each setup and refresh, so a roaming workstation follows
its current private IPv4 network. The private selection record saves the
resolved SSH boolean; explicit `--access-source` values persist. Use
`--access-source PRIVATE_IPV4_OR_CIDR` to specify private IPv4 sources, or
`--no-lan-access` / `--no-access-source` to disable automatic discovery.
Password modes still require explicit private access flags; automatic LAN
discovery does not satisfy that requirement. No public IPv4 or IPv6 SSH grants
are created, and unrelated firewall rules are retained. Before changing
firewall policy, setup requires an active SSH client source (when present) to
be one of the selected private IPv4 sources; otherwise setup stops without
changing the firewall. Connect from an allowed private IPv4 address and avoid
switching from a public or IPv6 SSH path during the handover.

Adopting an active or enabled stock SSH service into key-only mode requires a usable
`authorized_keys` file for the existing account. If it is missing or unsafe,
setup refuses adoption to avoid locking out the account unless a password mode
or enrollment window is explicitly selected. A new key-only service
does not require a key before setup: explicitly open a temporary password
bootstrap window to enroll one. It listens on port 2222 for 1–60 minutes, uses
the existing account password, and stores no password. Verify the host
fingerprint through a trusted local channel, copy the key, then test ordinary
port-22 access before the window expires:

```fish
basaltw setup agent_cachyos localhost --ssh --ssh-password-window 15 --lan-access --codex
ssh-copy-id -p 2222 USER@WORKSTATION_PRIVATE_IP
ssh USER@WORKSTATION_PRIVATE_IP
```

The temporary root service has a one-shot runtime, bounded stop, and no restart
policy. Reboot or expiry stops its process group and closes active sessions;
the refresh record never saves or replays a password window. Persistent
password authentication for the existing account remains enabled until
the next full setup unless `--ssh-password-auth` is selected again. Full setup
intentionally defaults to key-only authentication even for an existing managed
password service. An ordinary `refresh` preserves a saved persistent password
selection; use `--no-ssh-password-auth` or `--no-ssh` to clear it. Password mode
requires explicit `--lan-access` or private `--access-source` on each request.
`--no-ssh` also clears password selection. A refresh that includes a fresh
window starts a new one-shot window; an ordinary refresh does not recreate one. Do not combine
persistent password authentication with a temporary password window.

Lockdown restarts only the port-22 listener, preserving established SSH
sessions, including sessions authenticated with a password. Legacy managed units
first receive the same session-preserving stop policy before authentication
changes. Existing sessions are not revoked. Enroll and test a key in a second
connection before closing your password session; new password logins stop
working after lockdown. Temporary port-2222 sessions still close on expiry or
ordinary refresh.

To repeat a successful selection while opening a fresh 15-minute enrollment
window, preserve the selected tools and explicitly select LAN access for this
request:

```fish
basaltw refresh --ssh --ssh-password-window 15 --lan-access
```

The duration is used for this refresh only and is never saved. A saved refresh
record containing a password window is rejected. An ordinary refresh closes
any active bootstrap window; its port-2222 allow rule may remain until refresh
removes it, but no listener remains after expiry.

Remote setup is useful when a controller is separate from the workstation and
the target already has reachable SSH and a non-root desktop account:

```fish
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --dry-run --codex
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --codex
```

The controller does not create accounts, enable KDE autologin, or copy
credentials. Setup uses the existing account's sudo access for privileged
steps. Selected GUI tools that require the user's session still need that user
bus/session. Remote setup verifies CachyOS on the target before applying
profile steps. Its dry-run opens no SSH connections. Use `--key PATH` (or
`--ssh-key PATH`) for a controller identity; it is not copied to the target.
Remote setup requires Python 3 and the existing SSH service before upload.
The Debian `--restart-if-needed` / `--wait-for-restart` workflow is unsupported.

Uploaded source remains in the user's private
`~/.local/share/basaltwater-cachyos-remote/runtime` so selected service helpers
survive temporary upload cleanup and ordinary installer updates. New targets
receive `~/.local/bin/basaltw`; existing launchers are preserved. A launcher
using this source snapshot is updated by repeating setup from the controller;
it cannot use channel upgrade or `refresh`. Install through the local installer
when channel-based upgrades and local refresh are wanted. See
[remote development](cachyos-development.md) for commissioning details.
