# Gogs mirrors and dedicated VMs

[Topic index](gogs.md)

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
push URLs, and writes a `.lfsconfig` whose endpoint is the Gogs repository.
Committing that file publishes the mirror hostname and repository path. For a
personal/private mirror, keep it out of Git with local exclude rules, or use
local Git LFS configuration for each clone. Commit `.lfsconfig` only for an
intentionally public project endpoint or an explicit user request.
One subsequent `git push origin` therefore uploads LFS objects to Gogs and
sends Git refs and LFS pointer files to both GitHub and
Gogs. Use `--no-combined-push` when Git refs should be pushed to the `gogs` remote
separately. In either mode, the selected mirror remote's explicit push URL is
reset in the repository's local config to its Gogs URL, including when using a
custom mirror name. Combined pushes are not atomic: after a failure, inspect
refs on both remotes before a deliberate retry because one may have advanced
while the other did not.

Before applying changes, the command validates the repository's local Git config
target and checks the origins of effective `remote.origin.url`,
`remote.origin.pushurl`, and the selected mirror's `remote.<name>.url` and
`remote.<name>.pushurl` values. Stale multiple push URLs stored directly in that
local config can be repaired.
An inherited fetch URL is allowed only when it exactly matches the intended
fetch URL. Inherited push URLs are allowed only when they exactly match the
destinations selected for that remote in this push mode. An unexpected
inherited destination stops configuration before repository actions; the helper
does not rewrite global, system, included, or per-worktree config. Matching
inherited URLs remain effective.

For an existing remote with a local or enabled per-worktree config entry,
including entries in local config includes, the helper replaces its local fetch
URL with `git config --replace-all remote.<name>.url`. This updates multiple
local URL values and preserves fetch refspecs and other remote settings,
including on fetch-only remote definitions. When a remote exists only in global
or system config, the helper uses `remote add` to create the local remote and
its fetch refspec. Matching inherited URLs do not make it appear to be an
existing local remote. This check does not inspect `insteadOf` or `pushInsteadOf`
URL rewrite rules, which remain a separate transport layer.

A nonempty `GIT_CONFIG` environment override stops configuration before Git is
run because it can redirect config writes away from the validated local target.
The command leaves that environment variable and its file unchanged.

The command also checks effective `lfs.url` and
`lfs.pushurl` settings, `.lfsconfig`'s upload URL, and endpoint settings for
`origin` and the selected mirror. A failed or malformed config query, or an
invalid endpoint or one that points somewhere other than the requested Gogs LFS
endpoint, stops configuration before any repository actions. Endpoint-conflict
diagnostics do not print configured endpoint values. Matching endpoints are
accepted. The helper replaces `.lfsconfig`'s download URL with the intended Gogs
URL, but does not rewrite Git's global or system configuration. Remote-specific
endpoint mismatches are rejected conservatively to avoid relying on Git LFS
version-specific precedence; for example,
`remote.origin.lfsurl` may not override the generated download endpoint in the
installed Git LFS version. Review and correct an intentional mismatch yourself
before retrying. The command does not stage, commit, push, create either remote
repository, or configure credentials.

Run first with `--dry-run` to inspect the exact Git commands. It still checks
that the worktree is clean, the filesystem is local, and Git LFS is available,
but skips commands that change repository configuration or tracking. It avoids
`git lfs status`, which can initialize repository configuration even though
the status output is not used. The utility rejects embedded credentials,
non-HTTPS repository URLs, unsafe `.lfsconfig` or `.gitattributes` targets,
Git LFS endpoint conflicts, and CIFS, SSHFS, NFS, or other known remote worktree
filesystems. Review `.lfsconfig` against the privacy boundary above and commit
the intended `.gitattributes` before the first push. Every clone that needs
asset contents must be able to reach and authenticate to Gogs; an off-network
clone can use `GIT_LFS_SKIP_SMUDGE=1` until
the endpoint becomes reachable.

## Dedicated mixed-media VM

For a modest internal Git and file server, keep the operating system on the
SSD and give Gogs, cluster storage, and user storage separate HDD-backed data
disks. Addresses, host/storage names and accounts below are fictional; size the
disks for your workload. Save the placeholder Samba credentials without putting
passwords in the setup command or shell history:

```bash
basaltw credentials set gitadmin --service gogs --destination 10.23.0.40
basaltw credentials set cluster --service samba --destination 10.23.0.40
basaltw credentials set alice --service samba --destination 10.23.0.40
basaltw credentials set bob --service samba --destination 10.23.0.40

basaltw setup server_web 10.23.0.40 gitadmin \
  --provision-on pve1 --name git-1 --image-storage local \
  --memory 2G --balloon-min 1536M --cores 2 \
  --storage root local-lvm 32G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --storage git-data bulk-lvm 256G \
  --storage-mount git-data /srv/gogs ext4 empty \
  --no-disk-ssd git-data --disk-discard git-data --disk-backup git-data \
  --storage cluster-data bulk-lvm 256G \
  --storage-mount cluster-data /srv/cluster ext4 empty \
  --no-disk-ssd cluster-data --disk-discard cluster-data --disk-backup cluster-data \
  --storage user-data bulk-lvm 512G \
  --storage-mount user-data /srv/shares ext4 empty \
  --no-disk-ssd user-data --disk-discard user-data --disk-backup user-data \
  --swap-mode auto \
  --swap-zram fast 512M priority=200 algorithm=zstd \
  --swap-file root /swapfile 2G priority=50 \
  --swappiness 20 \
  --access-source 10.23.0.0/24 \
  --gogs :3000 /srv/gogs --gogs-source 10.23.0.0/24 \
  --samba --samba-source 10.23.0.0/24 \
  --share write cluster /srv/cluster cluster \
  --share write users /srv/shares alice,bob
```

Replace the example IP and trusted LAN before running it. The setup user
`gitadmin` becomes the initial Gogs administrator; the separately managed
`git` system account remains reserved for Git-over-SSH. The hostless Gogs mode
serves plaintext HTTP only to the source-restricted private network. Use an
internal hostname with `--ssl` instead when traffic crosses an untrusted
network.

The three example data disks total 1 TiB; retain pool headroom for other guests
and later planning. Two vCPUs and a 2 GiB maximum
with a 1.5 GiB balloon floor are a reasonable starting point for a lightly used
Gogs/SQLite and Samba server; reserve additional memory for its host. A small
high-priority zram area absorbs short pressure spikes; the lower-priority
SSD-root swap file provides an emergency backstop. Omit `--balloon-min` for a
fixed 2 GiB allocation.

The per-device SSD, discard, and backup flags are deliberately explicit, so a
rerun reconciles an existing VM to the intended policy. Discard is useful when
the Proxmox storage stack can reclaim freed blocks even though the underlying
medium is rotational. The backup flags include all four disks in Proxmox VM
backups, but do not create a backup job or make a live SQLite snapshot
application-consistent. If cluster data is independently replicated and
rebuildable, `--no-disk-backup cluster-data` can avoid duplicating it in every
VM backup. Keep `git-data` backed up as one coordinated Gogs recovery set.

No SSD cache is allocated in this low-traffic example: a cache would
consume shared SSD capacity and add recovery complexity without
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

The Gogs service requires the mount containing its data configuration before
starting. Samba likewise installs dependencies for its managed share paths and
custom metadata cache. These dependencies apply on subsequent boots, including
when a data mount uses fstab's `nofail`; retain the persistent mount definitions.

This automation initializes blank disks allocated with a new VM and reconciles
the declared Proxmox hardware flags on managed disks during reruns. It does not
adopt an unrelated existing disk, migrate populated Gogs data, or put live
Gogs data on CIFS/Samba. Samba is appropriate for a consistent offline archive
or export, not as the live SQLite, repository, or LFS-object filesystem.
