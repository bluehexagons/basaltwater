# Retired infra-tools migration

All managed systems have completed the namespace cutover. Current Basaltwater
no longer moves infra-tools files, rewrites old configuration, or renames old
services and accounts. `basaltw migrate` returns an error with this guide.
Default workspace lookup refuses old client directories rather than treating
them as an empty current workspace. Setup refuses old target runtimes/state
and incomplete historical journals before activating the new runtime.
Completed migration journals and their private archives remain untouched.
Older installations with a real `/opt/basaltwater/state` directory are also
refused. Current setup uses durable `/var/lib/basaltwater` state and a runtime
link; it does not copy or rename old state or operation markers.

## Intermediate version for an old installation

Use commit `97da1799615d5ad7e3d34c7c38a38ae41631e5fd` from this repository.
It retains the complete one-time migration and recovery implementation.
Its [archived migration guide](https://github.com/bluehexagons/basaltwater/blob/97da1799615d5ad7e3d34c7c38a38ae41631e5fd/docs/BASALTWATER_MIGRATION.md)
documents starting layouts, conflicts, permissions, and recovery.
This intermediate version supports recent development installations with
`lib/installation_info.py` provenance, not historical releases such as
`v0.2.0`.

Stop operations and agent sessions and back up private data outside both
installation directories. Create a separate checkout; preserve the old
installation and any journal until the cutover is verified:

```bash
git clone https://github.com/bluehexagons/basaltwater.git basaltwater-migration
git -C basaltwater-migration checkout --detach 97da1799615d5ad7e3d34c7c38a38ae41631e5fd
cd basaltwater-migration

# Preview and apply this account's user migration.
python3 basaltwater.py migrate
python3 basaltwater.py migrate --apply
```

For an already renamed Basaltwater installation that still has runtime-relative
state, run its normal host setup using this pinned checkout before upgrading.
That version moves the state into `/var/lib/basaltwater` and installs the
runtime link. Review any retained `setup-operation.pre-persistence.json` and
resolve unfinished operations before resuming setup with the current version.

Saved configuration translation is also retired. Current setup refuses old
`privilege_broker` origins, `no_restart`, the removed `rdp_max_sessions`,
`rdp_kill_disconnected`, and `rdp_disconnected_timeout` policy fields, and removed
Ruby, API-subdomain, desktop-interface, and Syncthing topology fields. Cache
files are retained, and automatic restart checks refuse those old policies.
Use this same intermediate checkout for the normal saved-configuration
setup/deploy flow and verify the resulting settings before upgrading. It writes
`privilege_broker_port` and `auto_restart`, removes obsolete fields, and applies
the current persistent desktop-session policy. Review that policy change before
resuming desktop work. Do not delete saved configurations to bypass this check.

For a system installation, perform the system pass first on that host:

```bash
sudo python3 basaltwater.py migrate --system
sudo python3 basaltwater.py migrate --system --apply
```

Then run the user pass as each account owning old user data. Supply
`--installation /absolute/path/to/old-source` for a custom source location.
Inspect the pinned guide before applying; do not pull current code over the
old runtime or run both namespaces concurrently.

If an old cutover is interrupted, keep its journal and referenced backups.
From the same pinned checkout, use the appropriate recovery command:

```bash
python3 basaltwater.py migrate --recover
sudo python3 basaltwater.py migrate --system --recover
```

These are alternatives for user and system recovery. A completed migration
cannot be reversed by those commands. Do not delete journals to bypass an
incomplete cutover. After migration, verify private file modes, saved hosts,
services, timers, agent configuration, and application access. Upgrade to
current Basaltwater using the current installer or controller setup, then
resume normal operations.

The supported names are `basaltw`, `basaltwater.py`, `BASALTWATER_*`, and
`basaltwater.json` deployment manifests. External scripts and deployment
repositories need those names too.
