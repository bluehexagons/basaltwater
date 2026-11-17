# Runtime secret bindings

Service components can request one named target-local environment file with
`"runtime_binding": "shop-api"`. The same mechanism works for ordinary target
builds, selected-builder deployments and retained artifact refresh. A binding
file may contain all environment variables needed by that component. Binding
IDs are literal lowercase slugs of 1–64 characters; static components cannot
request them. `runtime_binding` and the legacy `env_file` field are mutually
exclusive.
Update Basaltwater on the controller, builder and target before using this
manifest field; older installations reject it during manifest validation.

## Configure the target

An administrator provisions the values directly on the target. Keep them out of
the repository, controller enrollment documents and build environment. For
example, prepare `/etc/basaltwater/runtime-secrets/shop-api.env` with systemd
EnvironmentFile syntax, owned by root with mode `0600`. Use an administrator's
private editor or file transfer, without putting values in command arguments.
Keep its directory root-owned and mode `0700`.

Create `/etc/basaltwater/runtime-bindings.json`, also root-owned mode `0600`:

```json
{
  "schema": 1,
  "bindings": {
    "shop-api": {
      "deployment_root": "/var/www/app_example_com",
      "component": "api",
      "env_file": "/etc/basaltwater/runtime-secrets/shop-api.env"
    }
  }
}
```

Use the exact release destination for `deployment_root`, rather than its parent
or a URL. For ordinary deployment to `app.example.com` under `/var/www`, this
is `/var/www/app_example_com`. For selected-builder deployment, use the target
application grant's `deployment_root`. Names and roots are exact matches, with
no wildcard grants. Separate components need separate binding IDs/grants, even
when an administrator deliberately maps them to the same environment file.

The registry accepts at most 256 grants and 256 KiB. Paths must be canonical
absolute ASCII paths using letters, digits, underscores, periods, hyphens and
slashes. Symlinks, hardlinks, special files, foreign owners and writable parent
directories are refused. All ancestors must be root-controlled; a root-owned
sticky ancestor such as `/tmp` is permitted only above a protected directory.
Environment files must be regular root-owned mode-`0600` files outside release
and managed data trees. Never place secrets inside any application's exported
artifacts, worktree or release directory.

## Request a binding

Add the ID to the service in `basaltwater.json`:

```json
{
  "version": 1,
  "components": [{
    "name": "api",
    "type": "service",
    "domain": "{{domain}}",
    "binary": "bin/api",
    "port": "auto",
    "runtime_binding": "shop-api",
    "runtime_env": {"HOST": "127.0.0.1", "PORT": "{{port}}"},
    "health": "/health"
  }]
}
```

For an explicit selected-builder target policy, its service route's `runtime`
mapping must also include `"runtime_binding": "shop-api"` and match the rest of
the projected runtime settings. Automatic target enrollment derives this
mapping from the manifest. Saved enrollment is retained: use the existing
`--build-target-auto` option to deliberately regenerate it after a runtime
change. This grants only the ID; provisioning the registry and values remains
a separate target-administrator operation.

Target reservation, staging and activation check the current local grant and
file metadata. Both deployment paths check before building/staging a release,
again after a build and before stopping old services, and immediately before
service installation. Retained artifact refresh checks all installed bindings
before beginning its release changes. Missing, revoked or unsafe bindings
refuse deployment rather than starting with an empty environment.

Only systemd reads the environment values when starting the service. Basaltwater
inspects file metadata and passes the target-local path to `EnvironmentFile=`;
it never copies or parses those values. Build requests omit runtime bindings;
the source manifest and target runtime projection can contain IDs. Resolved
paths and values do not enter build requests, artifacts, controller metadata or
transport receipts. The generated target unit contains the file path. The
service receives the environment values, while build and service accounts need
no permission to read the original file. As with systemd generally, environment
values are available to the service and privileged inspection; this feature
does not isolate secrets from authorized application code.

`runtime_env` is public configuration stored in units and metadata. Do not put
secrets there. systemd EnvironmentFile values take precedence over `Environment=`
values with the same name, so avoid defining the same key in both places.

## Rotation and recovery

Provision a new private environment file and atomically update the local registry
to point to it, keeping the same binding ID and scope. Coordinate administrator
edits with deployments; there is no transaction spanning external file edits and
activation. Redeploy or refresh to restart the component with the new values.
Rotation is manual: no automatic consumer inventory or restart is added.

Keep the previous file until the new service passes its health check. Existing
unit rollback restores the previous unit's path; deleting or overwriting that
previous file would prevent recovery of its old environment. Deployments never
write, remove or restore administrator-owned environment files. An interrupted
deployment retains the existing operation guards and private files; follow
[transaction recovery](../operations/transaction-recovery.md) before retrying.

Remove the registry grant to revoke future deployments or remove
`runtime_binding` from the manifest and matching target runtime grant before
retiring a binding. Revocation does not stop an already running process or erase
its environment. Coordinate service shutdown and file retirement separately.

Automated tests cover scope and revocation, unsafe files, privacy, ordinary and
prebuilt deployment, retained reconstruction and failed-release recovery using
temporary files and mocked service/system commands. Selected native Debian
checks also cover real systemd delivery, manual rotation and failed-rotation
rollback on both deployment paths, retained artifact refresh and guest reboot.
See the [dated qualification and limits](../testing/runtime-bindings.md).
Managed target enrollment and actual controller-client SSH delivery also pass.
Full selected-build setup with a remote worker, interrupted activation and power
loss remain separate qualification.
