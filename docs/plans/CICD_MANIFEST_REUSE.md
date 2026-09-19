# CI/CD Manifest Reuse

Status: queued P1 native path, after deploy secrets and transactional
activation. Coolify evaluation is deferred; it is an optional future platform
for complex applications and does not gate this work. As of 2026-08-09, the
webhook CI/CD path still consumes its server-side repository/script
configuration and does not load `basaltwater.json`.
This is the follow-up project for teaching that path to use the shared manifest.

Why it is separate:

- the current webhook executor is built around one artifact, one remote path,
  and one domain
- the manifest is multi-component and can combine static sites with managed
  services
- remote service install and health polling need a larger rework than a small
  shim

If this work resumes, start from:

- `docs/DEPLOYMENTS.md`
- `docs/CICD.md`
- `lib/project_manifest.py`
- `web/service_tools/cicd_executor.py`
- `lib/deployment.py`

## Recommended direction

Extract a deployment engine that accepts a validated manifest, immutable source
revision, resolved non-secret build inputs, secret references, and target
capabilities. The setup CLI and webhook executor should call that engine rather
than translate manifests into the existing one-artifact webhook model.

Keep repository scripts as explicit component build/test hooks. The executor
must continue to pin the authenticated commit, disable Git hooks, and run as the
dedicated build user. Reuse does not weaken the current webhook trust boundary.

## Delivery phases

1. Validate `basaltwater.json` at the authenticated commit before queueing mutation.
2. Build all components in the isolated build workspace and record an artifact
   manifest with checksums and component metadata.
3. Transfer artifacts to a target-side staging directory.
4. Invoke the same transactional activation and health checks as direct deploy.
5. Record component-level status in logs and notifications.
6. Add changed-component builds only after full-manifest deployment is proven.

Incremental behavior should hash component build commands, relevant source
paths, declared environment inputs, and toolchain metadata. A commit-only cache
key is insufficient when secrets or build configuration can change.

## Acceptance criteria

- The same `basaltwater.json` produces equivalent services, users, routing, and
  persistent paths through setup and webhook entry points.
- Multi-component static and service deployments activate transactionally.
- A failed component health check prevents the release from becoming current.
- Logs and notifications identify the failing component and rollback result.
- Artifacts are checksummed before activation and cannot escape target paths.
- Existing script-based repositories keep working through a documented legacy
  mode during migration.

## Non-goals

- A general-purpose hosted CI platform.
- Distributed builds across multiple build servers.
- Incremental builds before shared full-manifest deployment is reliable.
