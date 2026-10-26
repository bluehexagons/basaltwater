# Deployment Safety Reference

This reference summarizes the deployment safety behavior operators need to
know.

## Automatic safeguards

- Stages every requested repository on the control system before starting
  remote setup. A failed fetch aborts the complete run, preventing a partial
  desired set from removing an existing Nginx route.
- Refuses repositories or existing release trees with common Ruby/Rails markers.
  These checks prevent an unsupported source tree from being treated as static.
  Current Basaltwater machines have no Rails services, and Ruby
  applications are outside Basaltwater's deployment workflows.
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
- Release destinations must stay inside the deployment base and outside
  `.basaltwater_shared`, so a release cannot replace application data or its
  own operation marker and lock.
- Artifact uploads validate and normalize local source paths and separate rsync
  options from operands, including relative names resembling options or hosts.
- Incremental deployment metadata and manifest port assignments use bounded,
  non-symlink JSON reads. Invalid state blocks the decision and retains the
  original file for recovery; only missing files use fresh defaults.
- Manifest service components get dedicated runtime users and writable state
  only under `.basaltwater_shared/<app>/<component>/data`; the component root
  and deployment backups remain root-controlled and outside the systemd unit's
  writable paths.
- App-server setup and patch reruns reconcile only `/var/www` itself to
  `root:deploy` mode `0775`, allowing deploy to create release directories.
  Existing descendants keep their contents, ownership, permissions and ACLs,
  including service databases, backups and private deployment state. Required
  directory paths reject symlink components before directory mutations;
  existing log and Nginx directory ownership and permissions are preserved.
  This does not repair descendants changed by older recursive setup commands:
  operators must restore their application-specific owners and permissions.
- Manifest builds use per-application build users, stable automatic ports, and
  a deployment lock. Existing services continue running during the build.
  Service inventory verifies the generated unit's release or persistent paths
  rather than treating a shared name prefix as proof of ownership. Cleanup and
  rollback preserve other applications' units. A service-name collision with
  another deployment or an unmanaged unit aborts before building or stopping
  services; choose another component name or deployment path to resolve it.
  Matching local timer, path, or socket activators also block deployment,
  including service retirement: manifest services run directly, and their
  snapshots do not manage operator-created activators. Stop and remove the
  activator or choose another service identity before retrying.
- The internal verified-prebuilt manifest API checks the build request's
  ordered component names, types, artifact exports, commands and build
  environment against the manifest. It requires the request platform to match
  the caller-observed target platform and verifies the bundle digest, descriptor
  and exported bytes before taking the deployment lock or changing target
  state. It stages only the verified extracted tree and uses the same service
  identity, persistent data, port, SQLite backup, health-check, unit and release
  rollback flow as a local manifest deployment. Before entering that shared
  activation transaction, it validates the verified private release against
  the initial Debian 13 x86-64 runtime slice: static components and services
  with self-contained ELF64 x86-64 executables. Dynamic interpreters, shared
  library dependencies, non-ELF executables and unsupported platforms fail
  before the deployment lock or service/release mutations. It also checks that
  the final release path remains literal in generated service commands. This
  API does not run repository build commands or provision a build identity or
  toolchain. The caller still provides authenticated admission and
  operation/generation authorization; digest and runtime checks establish byte
  identity and narrow compatibility, not authorization or provenance. Standard
  `deploy_manifest` deployments still build on the target and do not use this
  prebuilt runtime guard.
- Manifest runtime environment values preserve literal percent signs, including
  percent-encoded URLs, instead of expanding systemd unit specifiers.
- Proxy routes preserve application `Cache-Control` and `Expires` headers.
  Static-file caching rules do not overwrite backend `no-store` or private
  policies merely because a proxied URL ends in `.json`, `.css`, or `.js`.
- A manifest release is rolled back when service activation, a declared 2xx
  health check, or required version metadata persistence fails. Previous systemd
  units are restored with their permissions, ownership, enablement, and running
  state; previously stopped services stay stopped.
  Services waiting for an automatic restart are explicitly stopped before
  backup or release replacement and remain stopped on rollback, matching other
  previously non-running services.
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
  while the old unit is verified inactive or failed, with no automatic restart
  pending, and before replacement. Backups have an integrity check and
  manifest-controlled retention. Symlinked backup
  directories and pre-existing temporary backup paths are rejected before a
  privileged backup is written.
  Backup contents and publication are synced before pruning older archives;
  retention preserves the new recovery point even if the clock moved backwards.
  Database paths are encoded before the read-only SQLite URI is opened, so
  filename characters such as `?`, `#`, and `%` cannot change the selected file
  or connection parameters.
- Installs a weekly cleanup timer and caps journal growth on server-style
  setups.
- Uses conservative package-update policy for Node and uv by default.
- Installs recurring maintenance unit files atomically and verifies that each
  timer is enabled and active without first deleting the working timer.
- Validates Nginx hardening and default-site changes before reload, restoring
  the exact previous configuration when validation fails.
- Applies a seven-day freshness delay to dependency resolution and GitHub release
  selection unless the operator opts into a newer release.

## Before production rollout

Deployment success verifies local service activation and declared health
endpoints. It does not establish public routing, a backup schedule, or complete
application workflows. Verify these for each production host:

- Check every deployed hostname from outside the LAN over HTTPS, including
  the expected certificate and application response. Cloudflare Tunnel requires
  public hostname routes in the Cloudflare account; hosts-file overrides only
  test the origin. Test LAN access separately and confirm an untrusted client
  cannot reach restricted direct ports. See [Cloudflare tunnels](cloudflare.md).
- Exercise signup, login, and a persistent write for stateful applications,
  then verify the data after a service restart and a replacement deployment.
  Secure login cookies require HTTPS even when LAN HTTP access is enabled.
  Verify a failed release restores the previous service on a disposable host.
- Schedule consistent database backups between deployments, retain independent
  off-host copies, and perform a restore test. Manifest `sqlite_backup` runs
  before replacement only; its retention counts archives, not days. Generic
  rsync backups do not provide consistent live SQLite snapshots. See
  [backup consistency limits](../storage/backups.md#consistency-and-recovery-limits).
- Verify game clients against the intended protocol version and test UDP/STUN
  independently. Cloudflare Tunnel carries HTTP traffic; a healthy lobby HTTP
  endpoint does not verify a separately hosted UDP responder.
- Monitor public application health and backup freshness after setup. A running
  systemd process alone does not prove public routing remains available. Rebuild
  deployed binaries after toolchain security updates; updating Go on the host
  does not update the runtime embedded in existing executables.

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

Use [transaction recovery](../operations/transaction-recovery.md) for phase-specific guidance.
Completed operations retain a private last-result record beside the marker.

For a replacement host, first deploy the matching application revision to create
its service and private data directory. Keep the original instance stopped while
verifying recovery. Resolve ownership from the replacement unit; do not assume
numeric user IDs carry across hosts. Retain required configuration, secrets and
other persistent files separately; a SQLite archive alone is not a full application
backup.

Restore a manifest component's verified SQLite backup by resolving its generated
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
journaling controls described in [`maintenance.md`](../operations/maintenance.md). The
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

- [CI/CD System](cicd.md)
- [Recurring Maintenance](../operations/maintenance.md)
- [Command-Line Reference](../reference/command-line.md)
- [Machine Types](../reference/machine-types.md)
