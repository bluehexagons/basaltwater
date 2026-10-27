# Deployment secret references

Status: versioned deployment-secret references, optional-component gates and
rotation tracking are not implemented. Ordinary manifests accept literal build
environment values and target-local `env_file` paths. The selected-builder path
rejects runtime environment values and `env_file`; it cannot use those bindings
yet. See [current deployment limits](../services/deployments.md#build-on-a-saved-builder).

This work follows the [shared Debian build/CI host](cicd-manifest-reuse.md).
Secret-free artifact builds do not need a new secret store.

## First deliverable: target-owned runtime bindings

Let target policy map named binding IDs to administrator-owned environment
files. Projects request IDs, never arbitrary target paths or secret values.
Authorize and bind the IDs during admission; resolve and revalidate them on the
target before activation. Keep runtime values out of builders, bundles,
controller command reconstruction and durable request metadata.

**Acceptance:** authorized bindings reach only their intended service;
missing/revoked bindings fail before activation; interrupted changes preserve
the previous working file and service. Ordinary and prebuilt activation share
the same binding semantics without broadening project authority.

## Subsequent work

- **Workspace-backed values:** use a separate versioned schema and provider
  interface, reusing private atomic-write techniques from `lib/credentials.py`.
  Do not overload named login credentials. No external secret manager is required.
- **Rotation and removal:** inventory names/consumers without values, report
  affected deployments, restart only affected components, and remove a managed
  file only after its last consumer is removed.
- **Optional components:** explicitly declare required references and whether
  absence skips the component or rejects the plan. Decide before mutation.
- **Build-time grants:** require explicit authorization to trusted build code.
  Include version references in build identity, never values. Authorized code
  can leak a secret; redaction is not an isolation boundary.

## Implementation and verification

Own changes in manifest validation, target policy/admission, deployment and
credential transport; follow [deployment safety](../services/deployment-safety.md).
Use private stdin or restricted files, atomic publication and appropriate
root/service ownership with mode `0600`.

Test missing/revoked references, deterministic component selection, rotation,
removal and interrupted writes. Check that values never enter argv, saved
commands, logs, process listings, artifacts or world-readable staging. Keep
secret values out of `basaltwater.json`; encryption formats and additional
providers are separate work.
