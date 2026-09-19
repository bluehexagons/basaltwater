# Gogs Git service

`--gogs` installs a minimal self-hosted Gogs instance with Git-over-SSH, Git
LFS over HTTP/HTTPS, SQLite storage, nginx integration when a hostname is
supplied, and a managed weekly release update. The setup creates the `git`
system account with `git-shell`; it cannot be used as a general login account.

## Choose a deployment mode

Hostname mode is the usual choice. The port in `--gogs DOMAIN:PORT` is the
public Gogs HTTPS port. Gogs listens on a separate Basaltwater-managed
loopback port and nginx serves the public URL:

```bash
basaltw setup server_web git.example.com deploy \
  --gogs git.example.com:3000 /var/lib/gogs \
  --ssl --ssl-email admin@example.com
```

The example is available at `https://git.example.com:3000/`; Gogs does not
claim port 443. With `--ssl`, Basaltwater obtains and renews a Let's Encrypt
certificate. Port 80 remains the ACME challenge and redirect listener. With
`--cloudflare`, the nginx-to-Gogs connection remains private and the tunnel
serves standard external HTTPS; public HTTP/HTTPS firewall ports are not
opened and Gogs does not create a local port-443 listener.

For a lab or private-network service, omit the hostname. With no source rules,
Gogs stays on loopback and setup prints an SSH tunnel command:

```bash
basaltw setup server_web 192.168.1.10 deploy \
  --gogs :3000 /srv/gogs-data

ssh -L 3000:127.0.0.1:3000 deploy@192.168.1.10
```

To make HTTPS reachable directly on a private network, enable SSL and repeat
`--gogs-source` for the exact IPv4 hosts or networks that need access:

```bash
basaltw setup server_web 192.168.1.10 deploy \
  --gogs :3000 /srv/gogs-data \
  --ssl \
  --gogs-source 192.168.1.0/24 \
  --gogs-source 10.0.0.0/8
```

Because a private IP cannot use the normal public-domain certificate flow,
hostless `--ssl` creates a self-signed certificate with the target IP in its
subject alternative name and includes `127.0.0.1` when the listener is
loopback-only. Basaltwater revalidates the certificate identity, key pair, and
remaining lifetime on rerun and replaces it when fewer than 30 days remain.
Trust `/etc/nginx/ssl/192.168.1.10.crt` explicitly on each client, or use a
hostname with Let's Encrypt to avoid certificate warnings. The resulting URL
is `https://192.168.1.10:3000/`.

Source-restricted mode requires active UFW. Setup stops the existing Gogs
service, installs and verifies replacement source rules, removes obsolete
Basaltwater-managed rules, and only then writes the listener. It refuses
public IPv4 sources from either `--gogs-source` or the generic
`--access-source`/`--lan-access` policy, explicit Gogs IPv6 sources, and
unmanaged allow rules for the same port. Generic IPv6 sources remain available
to other managed services but do not expose Gogs. Omit `--ssl` only when
plaintext HTTP is intentional on a trusted network. Hostname mode requires
`--ssl` or `--cloudflare`; a literal IP is hostless mode, and Cloudflare also
requires a hostname. Let's Encrypt hostname mode rejects source restrictions
because its HTTP-01 renewal listener on port 80 must remain publicly reachable.
Switching between hostless, hostname, direct TLS, and Cloudflare modes removes
obsolete managed firewall rules and Gogs nginx sites, including the former
port-443 listener.

The direct public HTTP/HTTPS port defaults to 3000, so `--gogs 3000` is also
valid. With `--cloudflare`, that value is the private backend port instead.
Port 80 is reserved whenever nginx, TLS, a hostname, or Cloudflare is involved;
a hostless plaintext direct listener may deliberately use it. The optional data
path must be absolute and defaults to `/var/lib/gogs`.

## First login and Git access

Registration is disabled. Setup creates the initial administrator named by
the setup username. To choose its password without exposing it in shell
history, save a matching workspace credential before setup:

```bash
basaltw credentials set gitadmin
```

The setup command may instead include `--credential gitadmin PASSWORD`, but
that exposes the value to shell history and potentially the process list. If
there is no matching credential, setup generates a 24-character random
password. In either case, setup records the initial value in the root-only file
`/opt/infra_tools/state/gogs_admin_credentials.json`. A rerun preserves an
existing administrator account and does not rotate its password. Use a unique,
high-entropy value and enable MFA for administrator accounts.

Reverse-proxied deployments, including hostless `--ssl`, throttle password and
MFA submission endpoints to five requests per minute per client with a small
burst. Nginx also emits a
privacy-preserving marker for failed current-API web authentication and all
HTTP Basic authentication; five failures within ten minutes produce a one-hour
Fail2ban source ban. This covers Git/LFS and API Basic Auth without throttling
successful high-volume transfers.
Both the current Gogs API sign-in paths and the login/MFA paths used by older
managed releases receive the request limit. Cloudflare deployments derive the
client address from the trusted tunnel header. Plain-HTTP hostless mode does
not pass through Nginx, so retain its private source restriction or use the
default SSH tunnel. Gogs session cookies are marked secure whenever the managed
external URL is HTTPS.

The web UI manages repository users and their SSH keys. Clone over HTTPS using
the configured public port, or over SSH through the `git` account:

```bash
git clone https://git.example.com:3000/team/project.git
git clone git@git.example.com:team/project.git
```

The SSH drop-in at `/etc/ssh/sshd_config.d/99-gogs-git-user.conf` permits
public-key Git operations for `git` while disabling passwords, forwarding, and
interactive sessions. Keep TCP port 22 reachable when SSH cloning is needed.

## Data, service, and updates

The selected data directory contains `custom/conf/app.ini`, the SQLite
database under `data/gogs.db`, repositories, logs, completed LFS objects under
`data/lfs-objects`, and temporary LFS uploads under
`data/tmp/lfs-objects`. Gogs uses its local LFS backend explicitly; no separate
LFS daemon or object store is required. Release binaries live under
`/opt/gogs/releases`; `/opt/gogs/current` and `/usr/local/bin/gogs` point to
the active release. Basaltwater requires the SHA-256 supplied in GitHub's
release asset metadata and verifies the downloaded archive before extracting
or activating it. A failed activation restores the prior verified release;
if no verified rollback target exists, setup stops the service. Setup also
refuses to replace an active release whose digest-qualified path disagrees
with the saved state; repair or restore that root-owned state before retrying.
Setup and patch also run a SQLite quick check, verify that the `git` user can
read and write each managed
directory, reject CIFS/SMB live storage, and print the backing filesystem, free
bytes, free inodes, and repository/LFS/attachment/log usage. Useful checks are:

```bash
sudo systemctl status gogs
sudo fail2ban-client status infra-tools-gogs
sudo journalctl -u gogs -n 100 --no-pager
sudo /usr/local/bin/gogs --version
basaltw gogs health git.example.com
basaltw gogs health git.example.com --json
```

Run `basaltw gogs health` on the control system. It reads the root-owned
managed state through non-interactive sudo and reports service and SQLite
health, the backing filesystem, free bytes and inodes, per-category usage,
directory access as `git`, the update service/timer, nginx's upload limit, and
whether a non-loopback LFS HTTP endpoint is configured. For reverse-proxied
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
success. If a post-update command, restart, or state write fails, the previous
release symlink is restored and a failure notification is emitted when
notifications are configured. Every completed check records a root-owned
result in `/opt/infra_tools/state/gogs_update.json`; health fails when the last
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

Agent VMs that need LFS can install and initialize the client once before all
normal repository clones:

```bash
basaltw credentials set agent-git

basaltw setup server_dev 192.168.1.41 agent \
  --git-access read-write \
  --git-credential https://git.example.com:3000 agent-git \
  --git-lfs \
  --repo https://git.example.com:3000/team/assets.git
```

Add `--git-ca-certificate ORIGIN SOURCE` when the Gogs certificate is signed by
a private CA or is the hostless self-signed certificate. A source such as
`ssh://gitadmin@192.168.0.51/etc/nginx/ssl/192.168.0.51.crt` retrieves the
certificate through the verified SSH connection and avoids a manual controller
copy. `--git-credential`
resolves the matching workspace password and configures both ordinary HTTPS Git
and Git LFS without putting the password in the repository URL. `--git-lfs`
does not alter repository URLs or credentials. A loopback-only Gogs deployment
is not remotely LFS-ready unless the client has a persistent HTTP tunnel and a
matching repository LFS URL; routine LFS use should use the HTTPS hostname mode
or source-restricted private listener.

## GitHub primary with a local Gogs mirror and LFS

Create the same empty repository path on Gogs before configuring an existing
GitHub worktree. Then run the local utility from a clean worktree on local
storage:

```bash
basaltw gogs repo-configure ~/repos/project \
  --github-url https://github.com/team/project.git \
  --gogs-url https://git.example.com:3000/team/project.git \
  --track 'assets/**' \
  --track '*.blend'
```

The command installs the repository-local Git LFS hook, keeps GitHub as the
`origin` fetch URL, adds the `gogs` remote, gives `origin` both GitHub and Gogs
push URLs, and writes a `.lfsconfig` intended to be committed whose endpoint is
the Gogs repository. One subsequent `git push origin` therefore uploads LFS
objects to Gogs and sends Git refs and LFS pointer files to both GitHub and
Gogs. Use `--no-combined-push` when Git refs should be pushed to the `gogs` remote
separately. The command does not stage, commit, push, create either remote
repository, or configure credentials.

Run first with `--dry-run` to inspect the exact Git commands. The utility
rejects a dirty worktree, embedded credentials, non-HTTPS repository URLs,
unsafe `.lfsconfig` or `.gitattributes` targets, and CIFS, SSHFS, NFS, or other
known remote worktree filesystems. Review and commit `.lfsconfig` and
`.gitattributes` before the first push. Every clone that needs asset contents
must be able to reach and authenticate to Gogs; an off-network clone can use
`GIT_LFS_SKIP_SMUDGE=1` until the endpoint becomes reachable.

## Dedicated mixed-media VM

For a modest internal Git and file server, keep the operating system on the
SSD and give Gogs, cluster storage, and user storage separate HDD-backed data
disks. Save the placeholder Samba credentials without putting passwords in the
setup command or shell history:

```bash
basaltw credentials set gitadmin
basaltw credentials set cluster
basaltw credentials set alice
basaltw credentials set bob

basaltw setup server_web 192.168.0.50 gitadmin \
  --provision-on ts1 --name git-1 --image-storage local \
  --memory 2G --balloon-min 1536M --cores 2 \
  --storage root local-lvm 32G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --storage git-data ts1-storage 256G \
  --storage-mount git-data /srv/gogs ext4 empty \
  --no-disk-ssd git-data --disk-discard git-data --disk-backup git-data \
  --storage cluster-data ts1-storage 256G \
  --storage-mount cluster-data /srv/cluster ext4 empty \
  --no-disk-ssd cluster-data --disk-discard cluster-data --disk-backup cluster-data \
  --storage user-data ts1-storage 512G \
  --storage-mount user-data /srv/shares ext4 empty \
  --no-disk-ssd user-data --disk-discard user-data --disk-backup user-data \
  --swap-mode auto \
  --swap-zram fast 512M priority=200 algorithm=zstd \
  --swap-file root /swapfile 2G priority=50 \
  --swappiness 20 \
  --access-source 192.168.0.0/24 \
  --gogs :3000 /srv/gogs --gogs-source 192.168.0.0/24 \
  --samba --samba-source 192.168.0.0/24 \
  --share write cluster /srv/cluster cluster \
  --share write users /srv/shares alice,bob
```

Replace the example IP and trusted LAN before running it. The setup user
`gitadmin` becomes the initial Gogs administrator; the separately managed
`git` system account remains reserved for Git-over-SSH. The hostless Gogs mode
serves plaintext HTTP only to the source-restricted private network. Use an
internal hostname with `--ssl` instead when traffic crosses an untrusted
network.

The three data disks total 1 TiB, leaving most of a nominal shared 4 TB HDD
unallocated for other guests and later planning. Two vCPUs and a 2 GiB maximum
with a 1.5 GiB balloon floor are a reasonable starting point for a lightly used
Gogs/SQLite and Samba server on an 8 GiB host. A small high-priority zram area
absorbs short pressure spikes; the lower-priority SSD-root swap file provides
an emergency backstop. Omit `--balloon-min` for a fixed 2 GiB allocation.

The per-device SSD, discard, and backup flags are deliberately explicit, so a
rerun reconciles an existing VM to the intended policy. Discard is useful when
the Proxmox storage stack can reclaim freed blocks even though the underlying
medium is rotational. The backup flags include all four disks in Proxmox VM
backups, but do not create a backup job or make a live SQLite snapshot
application-consistent. If cluster data is independently replicated and
rebuildable, `--no-disk-backup cluster-data` can avoid duplicating it in every
VM backup. Keep `git-data` backed up as one coordinated Gogs recovery set.

No SSD cache is allocated in this low-traffic example: a 128 GiB cache would
consume one quarter of a shared 512 GB SSD and add recovery complexity without
a demonstrated need. Samba's small disposable TDB metadata cache stays at
`/var/cache/samba` on the root SSD. `--samba-metadata-cache PATH` can place it
on another already mounted SSD filesystem, but it is not a file-data cache and
must not point inside a share or the Gogs data tree. Gogs already supplies the
server side of Git LFS; `--git-lfs` installs the client and therefore is not
needed on this server command.

Basaltwater identifies all data devices by stable serial, requires new devices
to be blank, and mounts the ext4 filesystems before Gogs or Samba creates data.
Gogs and each Samba share then check the mount marker. A missing or wrong mount
stops setup instead of allowing repositories, LFS objects, or shared files to
spill onto the SSD boot filesystem.

This automation initializes blank disks allocated with a new VM and reconciles
the declared Proxmox hardware flags on managed disks during reruns. It does not
adopt an unrelated existing disk, migrate populated Gogs data, or put live
Gogs data on CIFS/Samba. Samba is appropriate for a consistent offline archive
or export, not as the live SQLite, repository, or LFS-object filesystem.

## Troubleshooting

- A setup failure refreshing `authorized_keys` with `panic: error getting work
  directory: stat .: permission denied` means the Gogs CLI inherited a directory
  inaccessible to `git`. Update Basaltwater and rerun setup. Managed admin
  commands now change to `/opt/gogs/current`, and release checks run from the
  candidate release directory. This fix does not require `--nopasswd` or broader
  home-directory permissions.
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

See [`COMMAND_LINE.md`](./COMMAND_LINE.md) for the complete flag syntax and
[`MAINTENANCE.md`](./MAINTENANCE.md) for timer policy.
