# Deployment Safety Reference

This reference summarizes the deployment safety behavior operators need to
know.

## Automatic safeguards

- Stages every requested repository on the control system before starting
  remote setup. A failed fetch aborts the complete run, preventing a partial
  desired set from removing an existing Nginx route.
- Refuses repositories or existing release trees with common Ruby/Rails markers.
  Ruby support belongs to pinned legacy basaltwater releases; current setup
  leaves existing legacy Rails units and their generated Nginx routes alone.
- Automatic Node builds are discontinued; repositories with `package.json`
  require a manifest and are rejected before target setup when it is missing.
  Ready-to-serve static deployments stage beside the active release and switch
  directories atomically. A staging failure leaves the previous release active.
  Old-release cleanup failures retain the backup without rejecting activation.
  Static releases also record interruption state and restore the old tree when
  activation or metadata persistence fails.
  A plain static deployment refuses to overwrite a release with managed
  services; first deploy an all-static manifest to retire those units safely.
- Repository symlinks and special files are rejected on the controller before
  manifest inspection or upload. Target-side source copying also refuses links.
- Artifact uploads validate and normalize local source paths and separate rsync
  options from operands, including relative names resembling options or hosts.
- Incremental deployment metadata and manifest port assignments use bounded,
  non-symlink JSON reads. Invalid state blocks the decision and retains the
  original file for recovery; only missing files use fresh defaults.
- Manifest service components get dedicated runtime users and writable state
  only under `.basaltwater_shared/<app>/<component>/data`; the component root
  and deployment backups remain root-controlled and outside the systemd unit's
  writable paths.
- Manifest builds use per-application build users, stable automatic ports, and
  a deployment lock. Existing services continue running during the build.
- A manifest release is rolled back when service activation or a declared 2xx
  health check fails. Previous systemd units are restored with their permissions,
  ownership, enablement, and running state; previously stopped services stay stopped.
  Failure to remove an old backup after successful activation leaves that
  backup for later cleanup without undoing the new release.
- Manifest activation writes a versioned operation marker before staging or
  service interruption. A clean deployment or verified rollback removes it;
  interrupted and incomplete-recovery markers block another deployment.
  Static and manifest deployments share the lock and check each other's markers.
  Rollback restores previous manifest port assignments along with release files.
- Deployment-owned Nginx sites, enabled links and selected generated TLS files
  have private durable snapshots and an operation lock/marker. Failed validation,
  reload or finalization restores, validates and reloads the previous
  configuration. Incomplete recovery blocks retry and preserves the snapshot.
  Nginx must already be active; this path does not start an inactive daemon.
- Services that declare `sqlite_backup` receive a consistent SQLite API backup
  while the old unit is verified inactive and before replacement, with
  an integrity check and manifest-controlled retention. Symlinked backup
  directories and pre-existing temporary backup paths are rejected before a
  privileged backup is written.
  Backup contents and publication are synced before pruning older archives;
  retention preserves the new recovery point even if the clock moved backwards.
- Installs a weekly cleanup timer and caps journal growth on server-style
  setups.
- Uses conservative package-update policy for Node and uv by default.
- Installs recurring maintenance unit files atomically and verifies that each
  timer is enabled and active without first deleting the working timer.
- Validates Nginx hardening and default-site changes before reload, restoring
  the exact previous configuration when validation fails.
- Applies a seven-day freshness delay to dependency resolution and GitHub release
  selection unless the operator opts into a newer release.

## Recovery Path

Replace the angle-bracket placeholders in these command templates with values for the target application and host.

Manifest SQLite backups live under the component instead:

```text
/var/www/.basaltwater_shared/<app_name>/<component>/backups/
```

Interrupted manifest state is recorded at:

```text
/var/www/.basaltwater_shared/<app_name>/manifest-operation.json
```

If a later deployment reports an unfinished operation, inspect the marker and
the `staging_path`, `backup_path`, `failed_path`, `units`, and `errors` recorded in its
`context`. Verify which release is active and reconcile the named services
before moving the marker aside for audit. Do not remove or replace a
`recovery_required` marker merely to make deployment proceed.

The adjacent private `manifest-units.previous.json` records the previous unit
contents, permissions, ownership, and service state before activation. Filesystem
or service restoration failures retain the recovery marker and failed release
for inspection. A later deployment replaces this snapshot under the application
lock.

Use [transaction recovery](TRANSACTION_RECOVERY.md) for phase-specific guidance.
Completed operations retain a private last-result record beside the marker.

Restore a manifest component's latest SQLite backup by resolving its generated
service identity, stopping it, removing stale journal files, installing the
database with service ownership, checking it, and starting the service again:

```bash
service_unit="app-<app_name>-<component>.service"
service_user="$(systemctl show "$service_unit" --property=User --value)"
database_path="/var/www/.basaltwater_shared/<app_name>/<component>/data/<database>.sqlite3"
backup_path="/var/www/.basaltwater_shared/<app_name>/<component>/backups/<backup_file>"
test -n "$service_user" &&
  sudo systemctl stop "$service_unit" &&
  sudo rm -f "$database_path-wal" "$database_path-shm" "$database_path-journal" &&
  sudo install -o "$service_user" -g "$service_user" -m 600 \
    "$backup_path" "$database_path" &&
  sudo sqlite3 "$database_path" "PRAGMA quick_check;" | grep -qx ok &&
  sudo systemctl start "$service_unit"
```

## Maintenance

Deployment hosts install the same managed cleanup, update, restart, and
journaling controls described in [`MAINTENANCE.md`](./MAINTENANCE.md). The
deployment-specific guarantees are:

- cleanup runs APT `autoremove --purge` to retire unused packages such as
  superseded kernels, while leaving installed language runtimes alone;
- restart checks fail safe when uptime or active-session detection is
  unavailable;
- Gogs release activation validates the new binary and restores the previous
  release after a failed post-update check; and
- dependency-resolving installs use a seven-day freshness delay unless
  `--deploy-latest DOMAIN_OR_PATH GIT_URL` explicitly opts out.

## Related guides

- [CI/CD System](CICD.md)
- [Recurring Maintenance](MAINTENANCE.md)
- [Command-Line Reference](COMMAND_LINE.md)
- [Machine Types](MACHINE_TYPES.md)
