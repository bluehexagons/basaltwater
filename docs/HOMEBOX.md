# HomeBox inventory

Install one native [HomeBox](https://github.com/sysadminsmedia/homebox) instance
per Debian server. It uses a dedicated `homebox` system account, systemd,
SQLite, and local attachments. Initial installations use the verified
`v0.26.2` release; amd64 hosts are supported.

## Set up and sign in

For access through an SSH tunnel:

```bash
basaltw setup server_lite inventory-host operator \
  --homebox :7745 /srv/homebox --homebox-admin owner@example.com
ssh -N -L 7745:127.0.0.1:7745 operator@inventory-host
```

Open `http://127.0.0.1:7745/` while the tunnel is running. For a dedicated
hostname with publicly trusted HTTPS:

```bash
basaltw setup server_web inventory-host operator \
  --homebox inventory.example.com /srv/homebox \
  --homebox-admin owner@example.com --ssl --ssl-email owner@example.com
```

Point the hostname's DNS at the server and allow inbound TCP 80 and the HTTPS
frontend port through upstream firewalls. Port 80 remains public for ACME
renewal and redirects. `--access-source` and `--lan-access` restrict inventory
access through both the regular firewall policy and Nginx. Sources do not
restrict ACME challenges. A hostname's optional `:PORT` selects the HTTPS
frontend; use `--homebox-port` to change its private backend from 7745.
Loopback mode never opens a public application listener.

The supported profiles are `server_lite`, `server_web`, `server_dev`,
`agent_vm`, and `control_plane`. OCI and `--steps` are rejected. Add `--dry-run`
to validate and preview setup before changing a target.

HomeBox support is limited to amd64 hosts; unsupported architectures are
rejected before setup makes target-side changes.

Setup creates the first owner through a temporary loopback-only service and
verifies login before starting the permanent service with registration off.
Retrieve the generated password in a private terminal on the target:

```bash
sudo python3 -c 'import json; print(json.load(open("/etc/homebox/secrets.json"))["password"])'
```

The default email is `admin@homebox.local` when `--homebox-admin` is omitted.
Change the password through HomeBox after signing in. The saved initial
password is not updated when the application password changes. Rerunning setup
preserves users, passwords, inventory, and the API-key pepper. The initial
email is immutable setup metadata; later account changes belong in HomeBox.
Group invitations remain an application-managed way to add users while open
registration is disabled.

## Files and storage

| Path | Purpose |
| --- | --- |
| `/opt/homebox/releases/TAG-SHA256/homebox` | Verified native releases |
| `/opt/homebox/current` | Selected release link |
| `/etc/homebox/homebox.env` | Root-only managed environment |
| `/etc/homebox/secrets.json` | Root-only initial password and persistent API-key pepper |
| `/opt/infra_tools/state/homebox.json` | Validated release, endpoint, and storage state |
| `/opt/infra_tools/state/homebox_update.json` | Last automatic update result, with no credentials |
| `/var/lib/homebox` or selected data path | `homebox.db`, SQLite sidecars, and attachments |
| `/var/lib/homebox-backups` | Private manual backups and automatic recovery archives |

Use local ext4, xfs, btrfs, or zfs storage below `/srv`, `/mnt`, `/data`, or
`/var/lib`. Network/FUSE filesystems, symlinked paths, and overlap with managed
file shares or other services are rejected. A declared storage mount must
contain a HomeBox subdirectory; do not select the mount root itself. Setup and
operations verify backing-mount identity and capacity, and the unit requires
its data mount. Data-path changes require a deliberate migration, not a patch.

Basaltwater owns the environment, systemd unit, and Nginx site. Manual changes
to those generated files are reconciled on setup. Keep the API-key pepper:
changing it invalidates existing API keys. Neither it nor the initial password
is stored in controller setup records or printed by health commands.

## Health and updates

```bash
basaltw homebox health inventory-host --username root --json
sudo systemctl status homebox.service
sudo journalctl -u homebox.service -n 100 --no-pager
```

Remote commands inherit the saved SSH key and username. Select root explicitly
when the saved account lacks non-interactive sudo. All SSH host keys must be
enrolled through the normal Basaltwater workflow.

Health checks service activity, maintenance state, mount identity, free space,
SQLite integrity and users, secrets, executable digest, API version,
registration policy, generated configuration and environment permissions, and
the configured HTTPS frontend. When installed, it also reports the updater
timer and its last check (`pending` before the first run). SQLite probes run as
the database owner so they cannot leave root-owned WAL files that prevent a
restart. Rerun setup to reconcile drift; a stopped-service backup also repairs
sidecars left by earlier probes. The HTTPS probe verifies the target locally,
not public DNS, external firewall reachability, or an authenticated session.

Select a reviewed stable release explicitly:

```bash
basaltw patch inventory-host --homebox-version v0.26.2 --dry-run
basaltw patch inventory-host --homebox-version v0.26.2
```

Replace the example tag with the desired newer stable release. Releases are
verified against GitHub's publisher-supplied SHA-256 asset digest. Reruns keep
the installed version, while `auto-update-homebox.timer` checks the latest
stable release every Sunday around 06:00 with a randomized delay. The timer
uses the same recovery transaction as an explicit upgrade and sends configured
maintenance notifications only for a successful update or a failure. Disable
HomeBox to disable the timer. Downgrading requires restoring the matching
complete backup because startup can migrate SQLite.

Setup blocks public inventory requests, stops the service, snapshots its full
state, and verifies the new API and HTTPS route before reopening requests.
A failure before reopening restores the old snapshot and executable. A failure
after reopening is reported without rolling back newly accepted writes. Stop
SSH-tunnel clients and local API jobs during maintenance: local processes can
reach the private listener independently of Nginx's maintenance gate.

## Backup and restore

Commands use absolute paths **on the target**, and briefly stop HomeBox:

```bash
basaltw homebox backup inventory-host \
  /var/lib/homebox-backups/manual.tar.gz --username root
basaltw homebox restore inventory-host \
  /var/lib/homebox-backups/manual.tar.gz --username root --yes
```

The destination directory must already exist. Backup refuses to overwrite a
file or write inside live data. It verifies the executable digest and initialized
database before publishing an archive. Hard-linked attachments are stored as
independent regular files so the archive remains restorable. It publishes the
final archive only after writing and syncing it. Archives contain SQLite and
its sidecars, attachments, secrets, non-secret configuration metadata, and the
matching binary. Generated
environment, unit, and proxy configuration are rebuilt during restore.
Certificates remain under Certbot rather than in these archives.

Keep completed archives off-host using the [generic backup](BACKUPS.md) flow
or a protected transfer. Archives contain credentials: store them privately
and restore them as root-owned mode-`0600` regular files. Reserve space for the
archive, its unpacked contents, and restoration on the data filesystem. No
automatic pruning touches manual archives. After each successful setup or
automatic upgrade, Basaltwater retains the four newest
`*-before-setup.tar.gz` recovery archives and removes older automatic recovery
archives only.

For a clean amd64 replacement server, set up HomeBox at the same data path,
hostname, and version first so accounts, dependencies, TLS, and mounts exist. Copy the
archive there, set root ownership and mode `0600`, and run restore. Restored
settings replace the temporary instance, while retaining the replacement
server's validated mount identity. Reconcile the controller's saved setup
with the restored endpoint and version before a later patch.

Restore validates the archive before replacing data and preserves a
`before-restore` archive of the current state. If existing files are damaged,
it instead preserves a `damaged-before-restore` archive of raw files for manual
recovery; that archive is not a validated input to the restore command.
Use `--json` to see the preserved archive path. Restore can repair corrupt
management state, missing secrets, a deleted data directory, or a damaged binary
from a valid backup. The input archive must be outside the live data directory
so replacing inventory cannot delete the recovery source. Restore and upgrade
rollback verify HTTPS while the maintenance gate still blocks public requests;
a failed TLS probe keeps that gate in place.

An interrupted mutation leaves `/etc/homebox/maintenance` with the recovery
archive path. Inspect it and explicitly restore that archive before retrying
setup. Failed recovery retains the marker and artifacts. Do not delete the
marker to bypass a failed migration. An interrupted preparation with no
recorded backup is safely replayed because application mutation has not begun.

## Password recovery and disabling

HomeBox includes `reset-password --email=EMAIL`, which prints a one-time reset
URL without SMTP. On the target, run it in a private root terminal using the
managed environment and service identity:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --property=User=homebox --property=Group=homebox \
  --property=WorkingDirectory=/srv/homebox \
  --property=EnvironmentFile=/etc/homebox/homebox.env \
  /opt/homebox/current/homebox reset-password --email=owner@example.com
```

Substitute the actual data path and account email. Treat the printed URL as a
credential. Use the normal HTTPS endpoint or SSH tunnel to open it. Restore the
pepper from backup if it is lost; password recovery does not repair API keys.

```bash
basaltw patch inventory-host --no-homebox
```

Disabling stops the application and removes its unit and Nginx ingress while
retaining inventory, credentials, releases, and archives. It also disables the
HomeBox update timer. Re-enable by supplying the original `--homebox` settings.
If initial onboarding had not completed before disabling, re-enabling still
performs the private bootstrap. Shared Nginx/Certbot packages and certificates
remain installed. Permanent deletion is a separate manual task.

This support excludes Cloudflare, containers, PostgreSQL, external object
storage, multiple instances, OIDC, and adoption of unmanaged installs. See the
[implementation record](plans/HOMEBOX_SUPPORT.md) for validation evidence.
