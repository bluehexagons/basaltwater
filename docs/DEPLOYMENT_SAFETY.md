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
- Legacy automatic static and Node builds run beside the active release and
  switch directories atomically only after a successful build. A failed build
  leaves the previous release active.
- Manifest service components get dedicated runtime users and writable state
  only under `.basaltwater_shared/<app>/<component>/data`; the component root
  and deployment backups remain root-controlled and outside the systemd unit's
  writable paths.
- Manifest builds use per-application build users, stable automatic ports, and
  a deployment lock. Existing services continue running during the build.
- A manifest release is rolled back when service activation or a declared 2xx
  health check fails. Previous systemd units are restored with the release.
- Manifest activation writes a versioned operation marker before staging or
  service interruption. A clean deployment or verified rollback removes it;
  interrupted and incomplete-recovery markers block another deployment.
- Deployment-owned Nginx files are snapshotted and restored when `nginx -t`
  rejects a generated configuration.
- Services that declare `sqlite_backup` receive a consistent SQLite API backup
  while the old unit is verified inactive and before replacement, with
  an integrity check and manifest-controlled retention. Symlinked backup
  directories and pre-existing temporary backup paths are rejected before a
  privileged backup is written.
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
the `staging_path`, `backup_path`, `units`, and `errors` recorded in its
`context`. Verify which release is active and reconcile the named services
before moving the marker aside for audit. Do not remove or replace a
`recovery_required` marker merely to make deployment proceed.

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
