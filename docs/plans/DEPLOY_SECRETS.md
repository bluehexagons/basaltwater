# Deploy Secrets & Optional Components

Status: verified as needed, P1 after the transactional execution foundation.
As of 2026-08-09, manifest components support literal build environment values
and an `env_file` path, but they do not declare secret references, secret-gated
optional components, or rotation relationships.

The intended shape is simple:

- provision `.env` content from the workspace instead of hand-copying it onto
  servers
- gate optional service components on secret presence
- keep secret transport off the command line and out of world-readable staging
- let build env vary based on whether a component was enabled

This remains a design note only. When implementation starts, the work should be
built on top of the current deployment and manifest code paths:

- `docs/DEPLOYMENTS.md`
- `docs/DEPLOYMENT_SAFETY.md`
- `lib/credentials.py`
- `lib/deployment.py`
- `lib/project_manifest.py`

## Recommended design

Manifests should name secret references, never contain secret values. Resolve
those references on the orchestration host through a provider interface, then
transport the resulting content over stdin or a permission-restricted temporary
file. Secrets must not appear in argv, reconstructed commands, operation logs,
build output, or world-readable deployment staging.

Start with the existing workspace credential store, but keep resolution behind
an interface that can later support SOPS/age or a remote secret manager. Do not
make an external service mandatory for the first implementation.

The existing credential store is currently shaped around named login
credentials. Do not overload that schema with arbitrary deploy secrets; reuse
its atomic file/permission techniques behind a separate versioned secret-store
schema and provider interface.

The deployment engine should write a root-owned or service-owned environment
file atomically, set mode `0600`, and pass only its path to systemd. Build-time
secrets should be opt-in and should not be copied into the release or artifact.

Optional components should declare required secret names. A missing required
secret should either skip a component explicitly marked optional or fail
validation before any deployment mutation. It must never fail halfway through
activation because a secret was discovered late.

## Required operations

- list secret names and consumers without displaying values;
- set, replace, and remove workspace-backed values;
- report which deployments need rotation after a value changes;
- rotate a secret and restart only affected components; and
- remove remote secret files when their last managed consumer is removed.

## Acceptance criteria

- Secret values are absent from CLI arguments, saved setup commands, logs,
  process listings, and deployment artifacts.
- Missing secrets are reported during planning and preflight validation.
- Remote files are atomic, mode `0600`, and owned by the intended service.
- Optional-component selection is deterministic and visible in plan output.
- Direct and webhook deployments resolve the same manifest declarations.
- Tests cover redaction, interrupted writes, removal, rotation, and a failed
  deployment that leaves the previous secret file and service intact.

## Non-goals

- Inventing a new encryption format.
- Storing secret values in `basaltwater.json`.
- Supporting every secret manager in the initial release.
