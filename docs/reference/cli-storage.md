# Storage and data movement

[Documentation index](command-line.md)

## Storage and data movement

Generated project cleanup uses the guarded command below. Preview is the
default; apply requires explicit `--apply` and authorized cleanup scope:

```text
basaltw agent storage clean-generated ABSOLUTE_PROJECT --rule NAME [--policy RELATIVE_JSON] [--dry-run | --apply]
```

The rule must name an existing strategy generated from a reviewed version 1
cleanup policy. Rules select exact disjoint relative directories and cannot
overlap preserved paths, archives, or agent roots; generated strategies have
no age filter or archive step. The remover never executes policy commands or
accepts arbitrary path argv. Its guards check Git tracked files, ownership,
private checkout ancestry, links and mounts, bounded inventory, and active use.
Process checks are best effort, so stop jobs that use generated files first.
For isolated checkouts, authorized cleanup scope must also permit writes to
target parents. The read-only `agent storage project` command reports
`generated_cleanup_commands` preview/apply argv. Existing script and agent
cleanup rules remain supported.

`basaltw scrub status HOST` lists persistent integrity findings. Use `inspect`
or `verify` with `--file /absolute/target/path` to inspect one file or check its
existing parity. `repair`, `restore --from /absolute/backup/path`, and `accept`
require confirmation (`--yes` for scripts). Recovery retains originals and
verifies staged content before publication; acceptance explicitly establishes
a new baseline. All paths refer to the target. Commands support `--json`,
`--directory`, and `--database`; SSH defaults to root and the saved key, with
`--username` and `--key` overrides. See [Scrub recovery](../storage/scrub-recovery.md).

| Flag | Description |
|------|-------------|
| `--samba` | Install and harden Samba for authenticated SMB3 file sharing |
| `--samba-source IP_OR_CIDR` | Add a Samba-only ingress source; repeatable and combined with generic access sources |
| `--no-samba-source` | Clear saved Samba-specific ingress sources |
| `--samba-metadata-cache PATH` | Put Samba's disposable TDB metadata cache in an absolute directory outside share paths |
| `--no-samba-metadata-cache` | Return Samba metadata caching to `/var/cache/samba` |
| `--share TYPE NAME PATH USERS` | Configure one Samba directory share |
| `--credential USERNAME PASSWORD` | Transient password for username-only share entries; prefer workspace credentials to avoid shell-history and process-argument exposure |
| `--smbclient` | Install SMB/CIFS client |
| `--mount-smb MOUNTPOINT IP CREDENTIALS SHARE SUBDIR` | Mount an SMB share persistently; `SUBDIR` may be `/` |
| `--syncthing` | Install a managed, non-root Syncthing endpoint with an authenticated HTTPS admin UI |
| `--syncthing-admin USERNAME` | Select the workspace credential used for the web administrator (default: `syncthing-admin`) |
| `--syncthing-root PATH` | Confine GUI-managed folders to a storage root (default: `/srv/syncthing`) |
| `--no-syncthing` | Stop and remove the managed service while preserving its identity, database, and synchronized files |
| `--sync SOURCE DEST INTERVAL` | Configure rsync sync |
| `--scrub DIR DBPATH REDUNDANCY FREQ` | Configure par2 integrity checking |
| `--notify TYPE TARGET` | Configure notifications |
| `--notification-level LEVEL` | Set outbound volume to `verbose`, `normal` (default), `warning`, `error`, or `off`; local logs are unaffected |
| `--notification-strict-https` | Verify HTTPS webhook certificates and hostnames. The default accepts self-signed certificates |
| `--notification-allow-unverified-https` | Override saved strict checking and accept unverified HTTPS webhook certificates |
| `--notification-ca-file PATH` | Enable webhook HTTPS verification with an additional sender-local PEM trust bundle at an absolute path. Pass an empty value to clear the bundle |

Samba shares are authenticated and hardened; `TYPE` is `read` or `write`, and
`PATH` is one absolute directory. The metadata-cache option moves only Samba's
non-persistent TDB state, not share contents. See
[Samba Shares](../storage/samba-shares.md) for credentials, access control, fast
updates, removals, and SMB client mounts.
See [Managed Syncthing](../storage/syncthing.md) for HTTPS administration, GUI-managed
device enrollment and folders, relay behavior, and recovery boundaries.
See [Storage operations](../storage/storage-operations.md) for sync, parity, schedules,
mount checks, and logs, and [Notifications](../services/notifications.md) for delivery
targets and failure behavior.
