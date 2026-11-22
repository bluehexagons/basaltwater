# Deployment secret references

Status: target-owned runtime bindings are implemented for ordinary and prebuilt
service deployment. A root-owned local registry scopes each ID to one exact
release root/component and maps it to a private environment file. Selected-builder
runtime grants authorize the ID; paths and values remain target-local. See
[operator setup and recovery](../services/runtime-bindings.md). Versioned secret
values, optional-component gates, consumer inventory and rotation tracking remain
planned. Literal build/runtime environment is public configuration.

This work follows the [shared Debian build/CI host](cicd-manifest-reuse.md).
Secret-free artifact builds do not need a new secret store.

## Delivered foundation: target-owned runtime bindings

Projects request one `runtime_binding` ID per service. A separate target-local
registry owns paths and exact component scopes so controller enrollment never
needs private file locations. IDs are authorized during admission and rechecked
with file metadata before activation and retained refresh. systemd reads the
root-only environment file; Basaltwater does not read or copy its values.

**Automated acceptance:** scope and revocation, unsafe file/ancestor refusal,
ordinary/prebuilt installation, retained reconstruction, build-time revocation,
privacy and failed-release preservation are covered. Selected native Debian
component checks qualify real systemd delivery, manual rotation with rollback,
retained reconciliation and guest reboot; see the
[dated scope](../testing/runtime-bindings.md). Managed target enrollment and real
controller-client SSH delivery also pass. Full selected-build setup with a remote
worker, interrupted activation and power loss remain separate qualification.
Deployment leaves administrator files unchanged; old values remain recoverable
only while their old files are retained. No automatic value rotation is claimed.

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
