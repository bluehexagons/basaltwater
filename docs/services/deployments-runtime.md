# Configure services and diagnose deployment

[Documentation index](deployments.md)

## Service options

In addition to the common fields (`name`, `type`, `domain`, `path`, `build`, and
`env`), a `service` component supports:

- `port`: an integer from 1024 through 65535, or `"auto"` for a stable
  basaltwater-managed assignment;
- `binary` or `exec`: exactly one is required; `exec` cannot use systemd's
  privilege-control prefixes (`+` or `!`);
- `working_dir`: repository-relative directory or a supported template path;
- `env_file`: absolute server path, or a path using deploy-time templates; the
  file must be readable but not writable by the component's dedicated service
  account, and its parent directory must not be writable by that account;
- `runtime_binding`: a named administrator-owned environment file, scoped to
  this release root and component; mutually exclusive with `env_file`. See
  [runtime secret bindings](runtime-bindings.md);
- `runtime_env`: environment values written directly into the generated
  systemd unit; values may use deploy-time templates. EnvironmentFile entries
  take precedence over matching `Environment=` values;
- `health`: optional URL path polled on `127.0.0.1:port` after startup;
- `reverse_proxy`: set false for a worker or internal service that should not
  receive an Nginx route;
- `sqlite_backup`: optional absolute or templated SQLite database path backed
  up with SQLite's online backup API before release replacement; the resolved
  path must remain under that component's service-owned `{{data_dir}}`; and
- `backup_retention`: number of deployment backups to retain, from 1 to 100.

Basaltwater always writes the hardened systemd unit and runs it under the
component's dedicated service account. Repository-supplied unit files are not
accepted because installing one as root would bypass that isolation boundary.

Supported templates include `{{release_dir}}`, `{{base_dir}}`, `{{name}}`,
`{{service_name}}`, `{{domain}}`, `{{path}}`, `{{web_user}}`, `{{web_group}}`,
`{{port}}`, `{{binary}}`, `{{working_dir}}`, `{{env_file}}`, `{{shared_dir}}`,
and `{{data_dir}}`. Unknown templates fail validation. A health endpoint must
return a 2xx response within the 30-second activation deadline; persistent
failure rejects the release and restores the previous release and service
units.

## Runtime and update behavior

The following describes ordinary build-on-target deployments. For selected
builders and retained artifact refresh, use [the flow above](deployments-selected-builder.md#build-on-a-saved-builder).

- Manifest deployments build every component on each deployment; they do not
  use incremental builds.
- Builds run in a temporary sibling directory. For manifests containing an
  ordinary static component, allow space for both the build workspace and a
  complete release snapshot during deployment. The snapshot keeps service
  executables and their relative links outside public outputs at their usual
  paths. The operation marker records both directories during staging.
- Existing application services are stopped and verified inactive before
  basaltwater opens a declared SQLite database for its privileged backup; a
  backup failure restarts the unchanged release.
- Repository build commands run as an application-specific non-root build
  account that cannot modify another application's active release.
- Node and uv are provisioned in that application's persistent build home;
  Node projects may pin the build version with `.nvmrc` at the root and in
  conventional `frontend/`, `client/`, or `web/` packages. Build caches survive
  release replacement without being shared between applications.
- Build homes under `/var/lib/basaltwater/build-users` remain private to their
  respective accounts. Every deployment repairs home ownership and permissions,
  restores a traverse-only ACL on the root-private state parent after runtime
  staging, and verifies access as the build user before installing tools.
  Existing build accounts receive the same repair on reruns; sibling state and
  credential file modes are preserved.
- Static output directories and service binaries are checked before any active
  service is stopped. A declared binary must exist and be executable.
- Existing release files are replaced only after services are stopped. Static
  files are owned by the deployment user. For ordinary static components,
  writes through a build-time file handle cannot change the served copy. A
  service or health-check failure restores the previous release, including
  its static files.
- Automatic static deployments stage in a temporary sibling directory and
  replace the active tree atomically. A staging failure leaves the active
  release untouched; failure to clean up the previous release retains it for
  later cleanup without reporting a failed deployment.
- Every requested repository must be staged successfully before remote setup
  begins; one failed fetch aborts the complete setup instead of silently
  dropping that route from the desired deployment set.
- Each service receives a dedicated system user and persistent writable state
  only under `/var/www/.basaltwater_shared/<app>/<component>/data`. The parent
  component directory and its `backups` directory remain root-controlled.
- Service state remains outside the release directory, so replacing a release
  does not remove component data.
- Manifest deployments are serialized while stable ports are assigned and
  activated, preventing concurrent deployments from claiming the same port.
- Release files preserve executable bits instead of making every source file
  executable or writable.
- `basaltw patch HOST --deploy ...` reruns the saved deployment with the
  same manifest-aware path. Use `basaltw deploy PATTERN` to rerun saved
  configurations.

## Validation and troubleshooting

Manifest validation is strict: unknown fields, duplicate names, invalid domains,
unsafe paths, unsupported versions, invalid ports, invalid environment variable
names, duplicate resolved domain/path routes, and malformed service templates
are rejected. Control characters and systemd privilege-control command prefixes
are also rejected before any unit is installed. Check the remote deployment
output and inspect the generated service when a component does not start:

```text
sudo systemctl status app-<deployment>-<component>.service
sudo journalctl -u app-<deployment>-<component>.service -n 100 --no-pager
```

Replace `<deployment>` and `<component>` with the generated service name.
Basaltwater also refuses to replace an existing same-named Nginx site unless it
recognizes that site as one it generated. Rename or explicitly migrate a manual
site before assigning its domain to a managed deployment.

For Nginx and deployment rollback behavior, see
[`deployment-safety.md`](./deployment-safety.md). For webhook-triggered builds,
see [`webhook-builds.md`](./webhook-builds.md).
