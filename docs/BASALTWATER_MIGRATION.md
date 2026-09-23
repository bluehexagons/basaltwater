# One-time migration to Basaltwater

Basaltwater is a full rename: distribution `basaltwater`, entry module
`basaltwater.py`, command `basaltw`, and Basaltwater paths, services, skills,
environment variables, and generated configuration. Normal operation has no
old-name aliases or fallback paths. The repository is hosted at
`bluehexagons/basaltwater`.

## Supported starting point

The one-time `migrate` command targets recent infra-tools development installs
with `lib/installation_info.py` source provenance support and their current
workspace/state layouts. Historical releases such as `v0.2.0` and mixed-version
operation after migration are unsupported. Use a separate checkout of the
selected Basaltwater commit to perform the cutover; do not overwrite the old
installation with a Git pull first.

Stop controller operations and agent sessions before applying. Managed linked
Git worktrees in user data move with their reciprocal Git path pointers updated
in the recovery journal. Branches, indexes, uncommitted and untracked files, and
repository-owned configuration and links are preserved. Reopen sessions at the
new paths afterward. The common repository must remain outside the migrating
product directories; nested source-installation worktrees and inconsistent or
symlinked Git metadata are refused. Back up private data outside
both installation directories. Preview lists paths and actions, never credential
contents. It does not change files or start/stop services.

## Controller and user data

Commands that load the default workspace automatically move recent
`~/.config/infra_tools` and `~/.config/infra-tools` configuration into
`~/.config/basaltwater` before looking up hosts. This includes saved setups,
Proxmox hosts, credentials, SSH trust, and history. File bytes, ownership and
modes are preserved. Empty or disjoint destination directories can merge;
duplicate files and unsafe directory links stop the operation without replacing
data. An interrupted merge resumes on the next command. Explicit custom
workspaces remain unchanged. This local configuration migration also occurs
when a setup dry run needs saved hosts; the target remains untouched.

For the remaining user data, source installation, launchers and services,
run from the new checkout as the account that owns the old installation:

```sh
python3 basaltwater.py migrate
python3 basaltwater.py migrate --apply
python3 basaltwater.py bootstrap --skip-system-packages
basaltw --version
basaltw list --json
```

The user pass moves the old `.config`, `.cache`, `.local/share`, `.local/state`
and `Pictures` product directories into the Basaltwater namespace. It preserves
credential bytes, ownership and modes, updates path-bearing saved JSON fields,
replaces managed workflow skills with the current catalog, updates managed
launchers and shell/agent configuration, and renames affected user units.
Old paths and launchers are removed rather than retained as aliases.
Codex project trust paths change only beneath directories moved by this pass.
Independent repositories named `infra-tools` and `basaltwater` retain their
separate trust entries; their names alone do not make them migration targets.

For a recent source installation at a custom location, supply
`--installation /absolute/path/to/old-source` on preview and apply. Its new
directory is the sibling `basaltwater`; an existing destination is refused.
User source paths must be inside the current account's home. Explicit workspace
paths outside the default product directories remain operator-owned: copy them
deliberately and configure `BASALTWATER_WORKSPACE` or `--workspace` afterward.

## Existing servers

Run the normal `basaltw setup ...` command from the new controller checkout.
Setup automatically detects a recent `/opt/infra_tools` installation and
migrates it before running the requested setup steps. It stages the new code in
a separate private directory, completes the system pass, then runs user passes
as the existing login accounts with their own home directories and permissions.
No separate migration command or opt-in flag is required on the target VM.
The controller's default configuration migrates on lookup as described above;
its remaining installation data uses the explicit user migration.

`--dry-run` announces the automatic migration without connecting to or changing
the target. A conflict or interrupted migration stops setup; the recovery
instructions below apply. A later setup retries unfinished user passes after a
successful system pass. The new setup payload is not executed until those
passes succeed.

If an earlier setup stopped with `Remove managed Git worktrees before migration`,
rerun setup from the updated controller after closing active workspace sessions.
That preflight refusal did not create a user migration journal or move user
data; the completed system pass is retained and setup retries the user pass.
Do not remove worktrees or delete the completed system journal to work around it.

If setup stopped with `Conflicting agent configuration tables` because Codex
has trust entries for separate old- and new-named repositories, rerun setup
from the updated controller checkout. That planning error did not modify user
data or create a user journal. Keep both trust entries and the completed system
journal; actual collisions for moved projects or managed MCP servers still
require resolution before migration.

System migration includes journald and zram-generator drop-ins. Setup reruns
after an older completed migration repair these two previously omitted files:
an old file moves when its canonical path is absent; an identical duplicate is
retained with a `.backup` suffix that systemd ignores. Different contents,
unsafe links and existing backup destinations stop setup for inspection. This
prevents an old zram file from overriding later swap changes. These filename
repairs do not restart services or recreate active swap devices.

An unfinished systemd unit-replacement marker in either namespace blocks setup
and migration before runtime replacement. Resolve that operation using its
existing marker and backups first. Hidden unit backups remain untouched; idle
empty operation lock files can remain and do not represent active operations.

Setup reruns also repair the encoded ownership comments on all recent managed
UFW rule families: T3 Code, HTTPS forwards, Gogs, SSH, RDP, web TCP, mDNS,
Proxmox access sources, and Samba. They also repair ownership markers on managed
`/etc/codex` policies.
This applies even when a prior rename pass completed. Firewall repair changes
comments only, preserving ports, source restrictions, and unrelated rules;
operator-owned Codex policies remain untouched. An old refresh timestamp is a
separate Codex freshness warning, not proof that a refresh token has expired.

For a standalone migration or a custom source path, copy the selected
Basaltwater checkout to a separate directory on the target and run the explicit
commands below. The system pass precedes user passes when user launchers point
to `/opt/infra_tools`.

```sh
sudo python3 basaltwater.py migrate --system
sudo python3 basaltwater.py migrate --system --apply
sudo python3 basaltwater.py bootstrap --user USERNAME
basaltw agent doctor --json
```

The system pass moves persistent product directories, stages a fresh
Basaltwater runtime, preserves deployed repository sources and machine state,
and updates owned systemd, Nginx, sudoers, security and gateway configuration.
Affected units, including services with managed drop-ins, stop before paths
change. Enabled and active states are recorded;
only the corresponding new units are enabled or started. Unit drop-in
directories move with their owning units, retaining operator
overrides. The managed initramfs resume configuration also moves, so later swap
changes can replace or remove it through the new path. Service-account and
desktop-group renames retain numeric ownership. Account home-directory records
under the moved product paths are updated too. When Syncthing state joins the
private host-state directory, migration installs ACL tools if needed and grants
its existing account traversal before restarting services. Recovery restores
the recorded directory ACL; other accounts gain no access. Later runtime staging
also restores traversal, including setup runs without Syncthing steps.
Locks must be idle; a busy lock
stops migration before service changes or journal creation, so setup can be
retried after the operation finishes. Certificates
and private keys retain their bytes and trust identity; existing certificate
subjects are not reissued merely to change branding.

The resulting runtime is a source snapshot with provenance. For future updates,
use a Basaltwater controller to rerun setup, or use the installer to establish
a managed Git installation. The installer requires migration first; ordinary
setup performs that migration automatically on its target.

After both passes, verify services, timers, agent diagnostics, saved setups,
private file modes and application access before resuming automation. Update
external scripts to `basaltw`, `BASALTWATER_*`, `basaltwater-web`, and the new
paths. Deployment repositories now use `basaltwater.json`; rename their
`infra.json` manifests before the next deployment. External repositories and
arbitrary user scripts are not modified by migration. A repository containing
only the retired manifest is rejected instead of falling back to automatic
project detection.

## Conflicts and interrupted cutover

Disjoint legacy directories can merge. Duplicate files, conflicting canonical
destinations, unsafe symlinks, unknown managed skills and conflicting service
accounts are refused. Nothing is selected by timestamp or silently overwritten.

The exception is matching empty `provision-<SHA256>.lock` files in
`/run/lock/infra-tools` (or `infra_tools`) and `/run/lock/basaltwater`. Migration
acquires both locks, preserves the canonical Basaltwater inode, and archives the
empty legacy file in its private recovery journal. When `/run/lock` and `/var/lib`
are on different filesystems, the empty archive is written durably before the
legacy path is removed; recovery restores its permissions and ownership.
Active locks in either namespace
stop migration before changes; nonempty, linked, or unrecognized duplicate files
remain conflicts. If setup previously stopped on this lock-file conflict, update
the controller and rerun setup after provisioning operations finish. Do not
delete lock files or migration journals to bypass the check.

Setup automatically recovers the specific interrupted lock-retirement state
left by the earlier `Invalid cross-device link` failure, provided the old and
canonical empty locks still exist and all provisioning locks are idle. It
reverses the interrupted cutover, archives the recovered journal beside the
original directory with a `-recovered-` suffix, and retries migration. Completed
nodes remain completed. Other interrupted cutovers still use explicit recovery.

The apply operation records private recovery intent under
`/var/lib/basaltwater-migration` for system work and
`~/.local/state/basaltwater-migration` for user work. These directories can contain
configuration backups; keep them private. If cutover is interrupted, preserve
the journal and its referenced archives. Runtime snapshots and retired launchers
are kept in private `.basaltwater-migration-*` directories beside their original
paths, allowing atomic moves when `/opt`, `/usr`, and `/var` use separate
filesystems. Run recovery from the separate Basaltwater checkout:

```sh
python3 basaltwater.py migrate --recover
# Or, for the interrupted system pass:
sudo python3 basaltwater.py migrate --system --recover
```

Recovery reverses the interrupted filesystem changes and restores recorded old
unit state. Archive the recovered journal directory before retrying. A completed
migration cannot use this recovery command to downgrade; supporting old releases
after successful cutover is outside the contract. A successful rerun with no
legacy resources reports that there is nothing to migrate.

## Package installation

Use a separate virtual environment for the new wheel while migrating. Remove
the old `infra_tools` distribution before installing `basaltwater` into the same
environment: the distributions share runtime packages. Do not install both and
then uninstall the old one, which could remove new files. Fresh-wheel validation
is `python3 scripts/check_wheel_artifact.py`; historical wheel rollback is not
supported. See the [release qualification checklist](BASALTWATER_RELEASE.md).
