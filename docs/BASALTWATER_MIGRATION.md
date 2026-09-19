# One-time migration to Basaltwater

Basaltwater is a full rename: distribution `basaltwater`, entry module
`basaltwater.py`, command `basaltw`, and Basaltwater paths, services, skills,
environment variables, and generated configuration. Normal operation has no
old-name aliases or fallback paths. The GitHub repository stays at
`bluehexagons/infra_tools` until its owner performs the hosting rename.

## Supported starting point

The one-time `migrate` command targets recent infra-tools development installs
with `lib/installation_info.py` source provenance support and their current
workspace/state layouts. Historical releases such as `v0.2.0` and mixed-version
operation after migration are unsupported. Use a separate checkout of the
selected Basaltwater commit to perform the cutover; do not overwrite the old
installation with a Git pull first.

Stop controller operations and agent sessions before applying. Close and remove
managed linked Git worktrees through the old workspace tool first, preserving
branches and uncommitted work; migration refuses to relocate linked worktrees
because their Git metadata embeds absolute paths. Back up private data outside
both installation directories. Preview lists paths and actions, never credential
contents. It does not change files or start/stop services.

## Controller and user data

Run from the new checkout as the account that owns the old installation:

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
The controller's own data still uses the explicit user migration above.

`--dry-run` announces the automatic migration without connecting to or changing
the target. A conflict or interrupted migration stops setup; the recovery
instructions below apply. A later setup retries unfinished user passes after a
successful system pass. The new setup payload is not executed until those
passes succeed.

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
only the corresponding new units are enabled or started. Service-account and
desktop-group renames retain numeric ownership. Account home-directory records
under the moved product paths are updated too. Locks must be idle; a busy lock
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
arbitrary user scripts are not modified by migration.

## Conflicts and interrupted cutover

Disjoint legacy directories can merge. Duplicate files, conflicting canonical
destinations, unsafe symlinks, unknown managed skills and conflicting service
accounts are refused. Nothing is selected by timestamp or silently overwritten.

The apply operation records private recovery intent under
`/var/lib/basaltwater-migration` for system work and
`~/.local/state/basaltwater-migration` for user work. These directories can contain
configuration backups; keep them private. If cutover is interrupted, preserve
the journal and run from the separate Basaltwater checkout:

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
