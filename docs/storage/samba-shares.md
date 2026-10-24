# Samba Shares

Basaltwater configures authenticated Samba 4 file shares on Debian systems.
This document covers initial setup, credentials, access changes, fast updates,
removals, and related SMB client mounts.

## How shares are managed

Each share has an access mode, name, one absolute directory path, and one or
more users:

```text
--share ACCESS_TYPE SHARE_NAME PATH USERS
```

`ACCESS_TYPE` is `read` or `write`. Share names may contain letters, numbers,
dots, underscores, and hyphens, and must be unique in a command. A share has
exactly one directory root (never `/`); use a separate `--share` option for
each directory.
Roots must not be equal or nested, including normalized aliases such as
`/srv/docs/../docs`. Setup and direct share reconciliation reject overlaps
before changing accounts or filesystem permissions, because each share owns
its root's recursive group permissions.

The server creates a Unix user and Samba password for every declared user, then
uses a per-share Unix group. The share's group membership and configuration are
reconciled to the declared state. A later update can therefore remove users as
well as add them.

## Initial setup

The full setup installs and hardens Samba, configures the firewall and
fail2ban on machines that can enforce their own firewall policy, and creates
the requested shares:

```bash
basaltw setup server_lite fileserver admin \
  --samba \
  --samba-source 192.168.1.0/24 \
  --share read documents /srv/documents alice,bob \
  --share write dropbox /srv/dropbox alice,carol
```

The target must be Debian and the setup user must have root SSH access. Share
paths are created when needed. Paths below `/mnt` must already be on a mounted
filesystem; this prevents accidentally creating a share on the root disk when
a data drive is missing.

Setup checks every declared share mount before changing any share permissions.
Share roots must use normalized absolute paths without `..`, `.` components,
repeated separators or a trailing slash. Reconciliation rejects other spellings
before storage checks or share mutations, so aliases cannot bypass a declared
data mount. Share roots with symbolic-link traversal are rejected. The managed
`smbd.service.d/basaltwater-storage.conf` drop-in requires the configured share
and custom metadata-cache mounts at service startup, including mounts marked
`nofail` in fstab. Keep their persistent mount definitions in place.

Share reconciliation and global hardening validate a private candidate with
`testparm` before atomically replacing `smb.conf`, preserving its permissions
and ownership. Validation errors, exceptions and interruptions leave the live
file untouched. If publishing the validated file, installing the mount drop-in,
reloading systemd or reloading Samba fails, the previous `smb.conf` is restored
and the error propagates. Retry the same operation to repeat activation; unchanged
successful configurations skip the Samba reload. Account, password, permission,
cache-directory and mount drop-in changes are outside this file rollback. This
is bounded exception recovery, without a durable Samba transaction marker:
after a hard kill, failed restoration or an uncertain reload, inspect the live
configuration and service before retrying or manually reloading Samba.

`--samba-source` adds a Samba-only ingress policy. It is combined with any
generic `--access-source`/`--lan-access` policy but does not restrict SSH,
Gogs, or other services. Repeat it for each client network. A later patch can
use `--no-samba-source` to clear the saved Samba-specific list.

On firewall-capable machines, `server_lite --samba` also selects the base UFW
setup. Samba firewall reconciliation requires active UFW with a deny or reject
incoming default. Failed inspection, rule removal, reload, or policy verification
fails setup. Obsolete managed source rules and exact legacy broad TCP port
allows are removed; unrelated rules remain. If another rule still permits
unrequested SMB sources (including an application profile or port range), setup
reports it for operator review. The Proxmox profile retains its native firewall
plan; Samba's UFW step does not establish native Proxmox filtering.

Samba also has a small disposable TDB metadata cache. To place that metadata
on an SSD-backed filesystem, declare an absolute directory that is outside all
share paths:

```bash
basaltw setup server_lite fileserver admin \
  --samba \
  --samba-source 192.168.1.0/24 \
  --samba-metadata-cache /srv/ssd-cache/samba \
  --share write projects /srv/data/shares/projects alice,bob
```

This option configures Samba's `cache directory`; it does not cache shared
file contents. The directory is root-owned, mode `0750`, and its contents may
be deleted while Samba is stopped. Use `--no-samba-metadata-cache` to return
to `/var/cache/samba`. For data acceleration shared by Gogs, Git LFS, and
Samba, use the VM block-cache feature documented in
[Proxmox](../operations/proxmox.md), not this metadata option.

For a NAS with storage maintenance, combine shares with sync and scrub jobs:

```bash
basaltw setup server_lite fileserver admin \
  --samba \
  --share read media /srv/media guest \
  --sync /srv/documents /srv/backup daily \
  --scrub /srv/backup .pardatabase 5% weekly
```

## Credentials

The safest workflow stores passwords in the mode-0600 workspace credential
store, without putting them in shell history or process arguments:

```bash
basaltw credentials set alice
basaltw credentials set bob

basaltw setup server_lite fileserver admin \
  --samba \
  --share write documents /srv/documents alice,bob
```

The `USERS` value is a comma-separated list. Bare usernames resolve through
the workspace credential store; every bare username must have a saved
credential. Inline `username:password` values are supported for controlled
automation, but `username:password` values and `--credential USERNAME
PASSWORD` expose passwords in shell history or process arguments. Prefer
`basaltw credentials set USERNAME` to enter each secret interactively; never
use a sample or placeholder password as a real credential.

Passwords are omitted from saved setup state and reconstructed commands. Samba
password changes are supplied to `smbpasswd` over standard input rather than
embedded in a shell command.

## Access modes and permissions

Read shares set `read only = yes` and grant users read/traverse permissions.
Write shares set `read only = no`, add the share group to `write list`, and use
setgid directory permissions so new files inherit the share group:

```bash
# Read-only reference material
basaltw setup server_lite fileserver admin \
  --samba --share read reference /srv/reference alice,bob

# Collaborative directory
basaltw setup server_lite fileserver admin \
  --samba --share write projects /srv/projects alice,bob
```

The same share name identifies a logical share. Changing `read` to `write`
replaces the old generated section and group; it does not leave the old access
path active.

## Fast share-only updates

After an initial setup, use `shares` to update Samba without reinstalling
packages, changing the firewall, rebuilding unrelated services, or running the
full setup lifecycle. The host must have a saved setup configuration:

```bash
# Add a new share while preserving the other saved shares
basaltw shares fileserver \
  --share read archive /srv/archive alice
```

The `--share` option replaces the saved share with the same name. Supply the
complete desired user list when changing membership:

```bash
# Add carol and remove bob from the documents share
basaltw shares fileserver \
  --share write documents /srv/documents alice,carol
```

Change access mode or path with the same operation:

```bash
# Convert documents from read-only to collaborative write access
basaltw shares fileserver \
  --share write documents /srv/documents alice,carol
```

Update a user's workspace password, then apply it to the share. Use the same
workspace as the saved host:

```bash
basaltw credentials set carol
basaltw shares fileserver \
  --share write documents /srv/documents alice,carol
```

Remove a share by logical name. Its managed Samba section and access group are
removed; unrelated hand-written sections remain untouched:

```bash
basaltw shares fileserver --remove-share archive
```

Multiple changes can be combined. Each requested share is reconciled and Samba
is reloaded once after the complete candidate configuration passes `testparm`:

```bash
basaltw shares fileserver \
  --share write documents /srv/documents alice,carol \
  --share read photos /srv/photos bob \
  --remove-share old-media
```

Use `--dry-run` to validate and display the remote operation without making a
connection or changing the saved configuration:

```bash
basaltw shares fileserver \
  --share read photos /srv/photos bob \
  --dry-run
```

## Security behavior

The generated global Samba configuration:

- requires SMB3 or newer;
- requires signing and encryption;
- disables NetBIOS and exposes TCP 445 only;
- disables guest access and anonymous enumeration;
- removes the deprecated `null passwords` setting from the managed global
  section; and
- validates candidate `smb.conf` content with `testparm` before reload.

On machines that enforce their own firewall, setup also configures a fail2ban
jail for failed Samba authentication. A share's internal directories
identified by configured scrub jobs are hidden from SMB clients with `veto
files`.

Unprivileged and OCI containers defer firewall enforcement to their host and
do not retain the basaltwater-managed Samba fail2ban jail.

## Git and Git LFS storage

Samba is a file-storage and operator-access layer. It is not a Git transport,
Git authorization boundary, or replacement for a Git LFS server. Use HTTPS
Git and the Git host's HTTPS LFS endpoint for repository operations.

Keep live application data and active work local:

| Data | Recommended location | Samba use |
| --- | --- | --- |
| Gogs database, configuration, secrets, hooks, and repositories | Local Gogs data filesystem | Offline backup/export only |
| Gogs LFS objects and temporary uploads | Local Gogs LFS paths | Offline backup/export only |
| Agent `.git` directories and active worktrees | Local VM filesystem | Import/export or backup only |
| Large non-repository assets | An explicitly mounted share | Optional shared asset storage |
| Consistent Git/Gogs archives | A restricted backup share | A local backup job or dedicated backup account writes; ordinary agents have no access or read-only access |

Do not make the live Gogs data directory a Samba share. Setup rejects a share
that is the Gogs data path, contains it, or sits below it. Direct SMB access
bypasses Gogs authentication, hooks, repository policy, and LFS reachability
checks, while concurrent access can make SQLite and repository operations
unsafe. A separate share directory may use the same physical data disk, but it
must not contain the live database, secrets, hooks, repositories, or LFS object
path. Samba users should have only the read or write access they need. The
`force user` setting and symlink exceptions are not safe substitutes for that
separation.

For a Gogs backup, stop Gogs for the short archive window (or use an
equivalent consistent snapshot), create and checksum the archive locally, copy
it to the share under a temporary name, and rename it only after the copy is
complete. Restore locally, then verify ordinary Git cloning and Git LFS upload
and download before re-enabling service access. A live copy of SQLite,
repositories, and LFS objects taken at unrelated times is not a recoverable
backup. Do not configure Gogs's LFS `OBJECTS_PATH` directly on a CIFS mount in
the initial design.

Agent worktrees should also stay on local ext4 or xfs storage. CIFS changes
locking, case behavior, symlink and executable-bit handling, file
notifications, latency, and concurrent-writer semantics. A `file://` Git
remote on a share does not provide Gogs authentication or server hooks. Use a
mounted share for assets, staging, or archives; use a normal HTTPS remote for
Git and Git LFS. `git lfs install` and the local LFS cache remain part of the
agent VM setup, and a share does not bypass the LFS credential.

The current CLI composes these roles with the existing options rather than
adding Git-specific share syntax. Create the account through the credential
store first; the examples intentionally contain no password:

```bash
# Prompts for the restricted share account without putting its password in the command
basaltw credentials set backup-admin

# Restricted server-side archive area
basaltw setup server_lite fileserver admin \
  --samba --share read gogs-backups /srv/gogs-backups backup-admin

# Optional asset share mounted on an agent VM
basaltw setup workstation_dev agent-vm agent \
  --smbclient --mount-smb /mnt/assets fileserver agent-assets assets_write /
```

If a future release supports share-backed worktrees or live LFS object
storage, it must use an explicit storage mode with filesystem compatibility,
locking, disconnect, and recovery tests. It must never infer that behavior
from a path under `/mnt`.

## Connecting from a client

The server share name is `<SHARE_NAME>_<ACCESS_TYPE>`:

```bash
# Linux client, read-only share
smbclient //fileserver/documents_read -U alice

# Linux client, interactive mount
sudo mount -t cifs //fileserver/projects_write /mnt/projects \
  -o username=alice,vers=3.0,seal
```

For persistent client mounts managed by Basaltwater, use `--mount-smb` on the
client setup. It creates a root-only credential file and a systemd automount:

```bash
basaltw credentials set alice
basaltw setup workstation_desktop client admin \
  --mount-smb /mnt/projects fileserver alice projects_write /
```

The mount `CREDENTIALS` field follows the same rule as share users: use a bare
username backed by the workspace credential store, or an inline
`username:password` only in controlled automation.

Managed SMB mountpoints must be distinct, normalized directories below `/mnt`
(for example, `/mnt/projects`). This prevents a mount configuration from
changing ownership of an operating-system path.

Each client mount uses matching native `.mount` and `.automount` units. Only
the automount is enabled at boot; first access mounts the share after network
readiness, and `TimeoutIdleSec=60` requests unmount after a minute of inactivity.
The automount itself has no network dependency, so it can start before the
network is available. Setup replaces both units together, disabling the old
boot-enabled mount during migration. Unit validation or activation failures
restore the previous unit files and activation state; incomplete rollback
retains the shared systemd recovery marker. Credentials and directory ownership
changes are outside that unit transaction.

Sync and backup workers retain a private mount view for the entire rsync run,
so a host-side idle unmount cannot redirect an active mirror onto the underlying
local directory. The mount must be available when the worker validates it;
namespace creation requires `CAP_SYS_ADMIN` (normally root), and denied
isolation fails the sync. See [storage mount safety](storage-operations.md#sync-behavior)
for endpoint checks, nested exclusions, and standalone behavior.

Credential filenames retain systemd's literal unit-name escapes (for example,
`\x2d` for a hyphen) in `Options=`, so mounts with hyphens, spaces or percent
signs reference the actual root-only credential file. Percent signs in unit
values are escaped to prevent systemd specifier expansion.

## Troubleshooting

Inspect the generated configuration and service state on the server:

```bash
sudo testparm -s /etc/samba/smb.conf
sudo systemctl status smbd
sudo journalctl -u smbd --since '-15 min'
sudo pdbedit -L
```

If a fast update reports that Samba is not installed, run the full setup once
with `--samba`. If a username-only share fails validation, create or update its
workspace credential first:

```bash
basaltw credentials set alice
basaltw shares fileserver --share read documents /srv/documents alice
```
