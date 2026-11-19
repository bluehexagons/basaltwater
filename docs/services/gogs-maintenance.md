# Maintain and recover Gogs

[Topic index](gogs.md)

## Data, service, and updates

The selected data directory contains `custom/conf/app.ini`, the SQLite
database under `data/gogs.db`, repositories, logs, completed LFS objects under
`data/lfs-objects`, and temporary LFS uploads under
`data/tmp/lfs-objects`. Gogs uses its local LFS backend explicitly; no separate
LFS daemon or object store is required. Release binaries live under
`/opt/gogs/releases`; `/opt/gogs/current` and `/usr/local/bin/gogs` point to
the active release.
Generated service arguments and environment retain spaces, quotes, backslashes,
percent signs, and dollar signs in the data and account-home paths as literals.
Refresh an older managed service through normal setup or patch to regenerate it.
Basaltwater requires the SHA-256 supplied in GitHub's
release asset metadata and verifies the downloaded archive before extracting
or activating it. A failed activation restores the prior verified release;
if no verified rollback target exists, setup stops the service. Setup also
refuses to replace an active release whose digest-qualified path disagrees
with the saved state; repair or restore that root-owned state before retrying.
If either release-link publication command fails or is interrupted, setup and
the updater restore the previous link targets (or remove newly created links
on first install), then propagate the original failure or cancellation so a
retry can use the unchanged saved state. Recovery attempts every changed link
even if one restoration fails or is interrupted. A failed restoration reports
both the publication and recovery errors, including the affected link path and
its previous target. Cancellation remains a cancellation, with recovery details
attached as exception notes; inspect those paths and the saved state before
retrying. This interruption recovery covers release-link publication only.
Setup and patch require an existing regular `data/gogs.db`, open it read-only
for a SQLite quick check, and require the core `user` and `repository` tables.
An absent, empty, or schema-less database fails health; the probe never creates
one. On first install this check runs after private administrator initialization
and normal service startup. These checks establish basic integrity and schema
presence, not repository/LFS completeness or migration compatibility.
Setup and patch also verify that the `git` user can read and write each managed
directory, reject CIFS/SMB live storage, and print the backing filesystem, free
bytes, free inodes, and repository/LFS/attachment/log usage. Useful checks are:

```bash
sudo systemctl status gogs
sudo fail2ban-client status basaltwater-gogs
sudo journalctl -u gogs -n 100 --no-pager
sudo /usr/local/bin/gogs --version
basaltw gogs health git.example.com
basaltw gogs health git.example.com --json
```

Run `basaltw gogs health` on the control system. It reads the root-owned
managed state through non-interactive sudo and reports service and SQLite
health, the backing filesystem, free bytes and inodes, per-category usage,
directory access as `git`, the update service/timer, nginx's upload limit, and
whether a non-loopback LFS HTTP endpoint is configured. Gogs removes its LFS
staging directory at startup and recreates it on upload. When that directory
is absent, health checks read, write and traversal access to its nearest
existing parent and reports that path as `storage.lfs_temp_access_path` in JSON.
Persistent data directories must still exist. SQLite health uses the same
existing-file, read-only integrity and core-table checks as setup. Direct HTTP
mode requires a target-local HTTP 200 response on the validated backend port
from `app.ini`, with bounded connection and request timeouts. An active systemd
service alone is insufficient. For reverse-proxied
deployments it also validates nginx configuration and performs a target-local
request through the configured TLS listener or Cloudflare origin route. It
does not perform an external client or authentication probe, so a healthy
result is not a claim that public DNS, routing, an external firewall, the
Cloudflare edge, or credentials work. Defaults require at least 1 GiB and
10,000 inodes free; override them with `--min-free-bytes` and
`--min-free-inodes`. The check is read-only and never prunes LFS objects.

`auto-update-gogs.timer` checks weekly (Sunday at 05:30). It validates the
downloaded binary from a private, randomly named temporary workspace, refreshes
authorized keys and hooks, restarts Gogs, and updates the saved state only after
the existing bounded HTTP readiness probe succeeds on the backend port read
from `app.ini`. An unreadable or invalid backend port stops the check before
release installation. If a post-update command, restart, readiness probe, or
state write fails, the previous release symlink is restored and a failure
notification is emitted when notifications are configured. Rollback also checks
HTTP readiness before reporting the prior release restored; an unready service
remains a failed update and never records the candidate version as installed.
This is binary/link recovery only: it does not undo database migrations or
other application data writes, and HTTP readiness does not prove schema
compatibility. Keep a consistent pre-update backup; restoring SQLite or
downgrading its schema requires an explicit operator recovery decision.
Setup rollback likewise requires an active service and bounded HTTP readiness
on the backend port read from the current `app.ini` before reporting the prior
release restored. The saved release state follows the selected binary even
when recovery is unready; it is not a health-success record. Setup still fails
or propagates cancellation after recovery, and includes recovery failure details
in exception notes. Setup rollback also covers binary/link recovery only; it
does not restore earlier configuration, database migrations, or application data.
Every completed scheduled update check records a root-owned
result in `/opt/basaltwater/state/gogs_update.json`; health fails when the last
result failed or the record (falling back to initial setup state before the
first timer run) is older than nine days.

Run the normal `patch` flow to reapply the saved Gogs configuration. Do not
edit `app.ini` while the service is running unless you understand that a later
patch rewrites the managed settings. Back up the entire data directory before
database or repository maintenance; release updates do not replace it.

Gogs transfers LFS objects over HTTP/HTTPS even when ordinary Git uses an SSH
remote, so clients still need credentials and network access to the Gogs web
URL. Gogs does not provide Git LFS file locking. Treat repositories, SQLite,
configuration, and completed LFS objects as one recovery set; do not copy
those live paths independently and call the result a consistent backup.

### Rehearse recovery

For a Proxmox VM, a simple consistent recovery point is a gracefully shut down
guest whose archive includes the root disk and every Gogs data disk. Check the
disk backup flags before creating the archive. The
[Proxmox backup command](../operations/proxmox.md#placement-backups-and-migration)
verifies that a new archive exists; application recovery still needs a drill:

1. Restore to an unused VMID with automatic startup disabled. Keep it stopped
   while disconnecting **every virtual NIC**, and inspect any physical-device
   passthrough before booting. A whole-VM restore retains addresses, MACs,
   credentials and machine identity. A separate VMID alone does not isolate it
   from the original server. Changing only the MAC can also break a guest's
   existing network configuration.
2. Boot the disconnected copy and use the guest agent or console to inspect
   mounts, disk UUIDs, services and read-only SQLite health. For local HTTPS
   checks, preserve certificate verification and map the advertised address
   inside the disconnected guest only. Verify authenticated repository access;
   a login page or HTTP 200 alone does not establish data recovery.
3. Make a fresh clone with an empty LFS cache. Initialize Git LFS for that
   checkout (`git lfs install --local`), fetch the objects and compare expected
   commits, payload sizes and hashes. If cloning with `GIT_LFS_SKIP_SMUDGE=1`,
   unset it before the explicit pull. Check the working-tree payload, since
   `git lfs pull` can exit successfully while leaving a pointer when LFS has
   not been initialized for the repository.
4. Reboot and repeat the mount, identity and fresh-download checks. Keep a
   retained test copy stopped, with automatic startup disabled and its NICs
   disconnected. Reconnecting it requires an explicit cutover or preparation
   of a new network and machine identity.

The lab has exercised this boundary for one small private repository and LFS
object on a same-host HDD restore. It does not qualify off-host recovery,
database migration rollback, attachments or every repository permission mode.
Keep an independent backup outside the host's failure domain.

Agent VMs that need LFS can install and initialize the client once before all
normal repository clones:

```bash
basaltw credentials set agent-git --service git --destination https://git.example.com:3000

basaltw setup server_dev 192.168.1.41 agent \
  --git-access read-write \
  --git-credential https://git.example.com:3000 agent-git \
  --git-lfs \
  --repo https://git.example.com:3000/team/assets.git
```

Add `--git-ca-certificate ORIGIN SOURCE` when the Gogs certificate is signed by
a private CA or is the hostless self-signed certificate. A source such as
`ssh://gitadmin@192.168.1.10/etc/nginx/ssl/192.168.1.10.crt` retrieves the
certificate through the verified SSH connection and avoids a manual controller
copy. `--git-credential`
resolves the matching workspace password and configures both ordinary HTTPS Git
and Git LFS without putting the password in the repository URL. `--git-lfs`
does not alter repository URLs or credentials. A loopback-only Gogs deployment
is not remotely LFS-ready unless the client has a persistent HTTP tunnel and a
matching repository LFS URL; routine LFS use should use the HTTPS hostname mode
or source-restricted private listener.

## Troubleshooting

- A setup failure refreshing `authorized_keys` with `panic: error getting work
  directory: stat .: permission denied` means the Gogs CLI inherited a directory
  inaccessible to `git`. Managed admin commands run from `/opt/gogs/current`,
  and release checks run from the candidate release directory. The managed
  workflow does not require `--nopasswd` or broader home-directory permissions.
- An SSL or hostname setup that fails nginx validation fails setup and leaves Gogs
  stopped unless a prior verified release can be restored; run `sudo nginx -t`
  and inspect the generated `gogs_<hostname>` site before retrying.
- A hostless source setup fails closed when UFW is inactive or another rule
  already exposes the selected port. Inspect `sudo ufw status numbered` before
  retrying.
- A failed Git-over-SSH clone usually means the user's public key is missing or
  port 22 is blocked; check the SSH drop-in and `journalctl -u ssh`.
- If the web service is down, inspect `journalctl -u gogs` and verify ownership
  of the data directory (`git:git`).
- Check the update timer with `sudo systemctl status auto-update-gogs.timer`
  and its last run with `sudo journalctl -u auto-update-gogs.service`.

See [`command-line.md`](../reference/command-line.md) for the complete flag syntax and
[`maintenance.md`](../operations/maintenance.md) for timer policy.
