# Managed Syncthing

The Syncthing integration provides private, bidirectional file exchange for a
known group without requiring a VPN or a publicly reachable server. Syncthing
encrypts device-to-device traffic and can use outbound relays when direct
connections are unavailable.

Basaltwater owns the service boundary: a non-root systemd service, an
authenticated HTTPS admin endpoint, one configurable writable storage root,
and conservative network defaults. The Syncthing web GUI owns devices,
folders, folder direction, and versioning. Rerunning Basaltwater preserves
those GUI changes.

## Install the endpoint

Save the web administrator password in the workspace credential store, then
enable Syncthing:

```bash
basaltw credentials set syncthing-admin
basaltw setup server_lite fileserver admin --syncthing
```

The credential command prompts without exposing the password in shell history.
The final `Access:` summary includes the server device ID and HTTPS admin URL.
The same details are repeated after a successful `patch`. The default login
username is `syncthing-admin`; choose a different workspace credential name
with `--syncthing-admin USERNAME`:

```bash
basaltw credentials set file-admin
basaltw setup server_lite fileserver admin \
  --syncthing --syncthing-admin file-admin
```

The HTTPS listener uses the existing Basaltwater web gateway, certificate, and
access-source policy. If the server uses its local CA, enroll that public CA on
each administrator's browser device as described in
[Internal HTTPS sites and previews](INTERNAL_WEB.md#certificate-trust). Do not
disable certificate verification.

## Choose the storage root

The default storage root is `/srv/syncthing`. Select another bounded service
path with `--syncthing-root`; `/data` and paths below `/data`, `/mnt`, `/srv`,
or `/var/lib` are accepted:

```bash
basaltw setup server_lite fileserver admin \
  --syncthing --syncthing-root /mnt/team-files
```

Basaltwater creates the root for the setup user and confines the systemd
service to that root. Setup verifies an basaltwater-declared VM data mount
before writing to it, and the generated service also uses `RequiresMountsFor=`
for declared or separately managed mounts. This prevents a missing disk from
redirecting setup or synchronization writes into the root filesystem.

### Provision a VM with dedicated Syncthing storage

When provisioning a Proxmox VM, declare the root disk, a named data disk, its
guest mount, and the matching Syncthing root together:

```bash
basaltw credentials set syncthing-admin

basaltw setup server_lite 192.168.0.60 admin \
  --provision-on pve1 --name fileserver \
  --cores 2 --memory 4G \
  --storage root local-lvm 32G \
  --storage syncthing-data bulk-lvm 512G \
  --storage-mount syncthing-data /srv/syncthing ext4 empty \
  --disk-backup syncthing-data \
  --syncthing --syncthing-root /srv/syncthing
```

Replace the Proxmox host, pools, target address, and sizes with the deployment's
values. The named disk and `--storage-mount` are provisioning declarations;
`--syncthing-root` tells the service which mounted tree it may use. Keeping the
same path for both makes the dependency explicit and fail-closed. Proxmox disk
backup inclusion does not replace Syncthing versioning or an independent
off-host backup.

For an existing Basaltwater-managed QEMU VM, start with the command shown by
`basaltw cmd fileserver` so its other service choices remain intact, then
append the new disk, mount, and Syncthing flags. Existing storage declarations
may be left in that reconstructed command unchanged, or the shorter storage
portion may contain only the addition:

```bash
basaltw credentials set syncthing-admin

basaltw setup server_lite 192.168.0.60 admin \
  --provision-on pve1 \
  --storage syncthing-data bulk-lvm 512G \
  --storage-mount syncthing-data /srv/syncthing ext4 empty \
  --disk-backup syncthing-data \
  --syncthing --syncthing-root /srv/syncthing
```

Basaltwater merges the saved root disk and existing mounted disks, verifies
them at Proxmox, checks capacity, and hot-adds only the newly named blank disk.
The same additive workflow supports ordinary mounted data disks unrelated to
Syncthing. It does not replace a missing old disk, adopt a manually attached
volume, or resize or detach storage.

Changing `--syncthing-root` does not move files or rewrite GUI folder paths.
Before changing it on an existing endpoint, migrate the data and update or
recreate the GUI folders so every folder path is below the new root. Setup
checks a running endpoint before stopping it and rejects a configuration that
would leave a GUI-managed folder outside the service sandbox. Storage roots
and configured folder roots may not escape through symbolic links.

## Add people and folders

1. Open the **Syncthing admin** URL from the final access summary and sign in.
2. Have each coworker install Syncthing and send you their device ID.
3. In the server GUI, select **Add Remote Device**, enter the ID and a helpful
   name, and save it. Do not mark a coworker as an introducer.
4. Select **Add Folder**, use a path below the configured Syncthing root, and
   select only the devices that should receive it.
5. On each coworker's client, add the **Syncthing device ID** from the final
   access summary and accept the offered folder at an appropriate local path.

Coworkers need only the Syncthing client. They do not need the admin URL, its
password, SSH access, a VPN, or access to one another's devices. A normal
**Send & Receive** folder is bidirectional. Use separate folders for groups
that need different access, because Syncthing does not provide per-file ACLs
inside one folder.

New folders default to the configured storage root and staggered versioning for
up to one year. Both are visible and adjustable in the GUI. Existing folder
settings are never reset by a Basaltwater rerun. Versioning is useful recovery
but is not an independent backup; retain server snapshots or another backup
for disk loss, administrator mistakes, or compromise.

## Administrator credential rotation

Replace the stored credential and reconcile the saved setup:

```bash
basaltw credentials set syncthing-admin
basaltw patch fileserver --syncthing
```

Use the custom username instead when `--syncthing-admin` was selected. Setup
resets the web login from that workspace credential but preserves the server's
device identity, remote devices, folders, and their settings. For a routine
membership or folder change, use the GUI only; no setup rerun is needed.

## Service operations and removal

```bash
sudo systemctl status basaltwater-syncthing.service
sudo journalctl -u basaltwater-syncthing.service -n 200 --no-pager
```

The device certificate and database live in
`/var/lib/basaltwater/syncthing`. Preserve that directory to retain the server
device ID. The Debian package remains authoritative, and Syncthing's
self-updater is disabled.

Reruns reject symlinked state directories, identity files, and service paths
before changing ownership or generating configuration. Setup verifies that
the managed daemon stopped before updating its offline configuration; a failed
stop aborts instead of racing the running service.
Runtime staging makes `/var/lib/basaltwater` private to root. Syncthing setup
reapplies a traverse-only ACL for the service user on that parent before
checking the existing endpoint, then verifies access as that user. Its own
state directory remains mode `0700`; device keys and configuration are retained.
This also repairs reruns that previously failed with `mkdir ... permission denied`.

On a combined Samba/Gogs/Syncthing server, use separate sibling roots, such as
`/srv/shares`, `/srv/gogs`, and `/srv/syncthing`. Do not synchronize or export
live Gogs state. Sharing the same writable files through Samba and Syncthing
also requires deliberate Unix group/ACL design; the default per-share Samba
groups and Syncthing service account do not automatically grant each other
access. Separate roots avoid setup changing the other service's ownership.

To remove the service and HTTPS route while preserving state and files:

```bash
basaltw patch fileserver --no-syncthing
```

Re-enabling the endpoint retains the configured storage root, device ID, and
GUI-managed sharing state. Delete `/var/lib/basaltwater/syncthing` or the
configured storage root only as a separate, deliberate cleanup after verifying
the data is no longer needed.

## Security boundaries

- The GUI remains bound to `127.0.0.1:8384`; only the managed HTTPS gateway is
  externally reachable, and its firewall scope follows Basaltwater access
  sources.
- The service can write only its state directory and configured storage root;
  GUI folder paths outside that root fail rather than expanding access.
- Automatic router mapping is disabled. Existing routing can permit direct
  device connections; otherwise outbound relay fallback remains enabled.
- Device IDs grant folder-level Syncthing trust, not Unix or general network
  access. A compromised authorized device can still replace or delete shared
  data.
- Do not synchronize live databases, virtual-machine images, or application
  state that requires transactional consistency.
- A second Syncthing instance using TCP/QUIC 22000 or GUI port 8384 conflicts
  with the managed service.
- This capability is rejected for `server_proxmox`, the root account, and OCI
  targets. Use a normal Debian VM, LXC, workstation, or physical server.
