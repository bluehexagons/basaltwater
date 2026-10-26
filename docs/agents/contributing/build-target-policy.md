# Target deployment policy

`lib.build_target_policy` is a pure, internal authorization and projection
slice for the next typed target transport. It is not wired to SSH, admission,
artifact verification, runtime validation, or activation. Ordinary local
deployments keep their existing build-on-target behavior.

## Administrator policy

`parse_target_policy()` accepts a decoded mapping or raw UTF-8 JSON bytes and
returns a detached, sorted schema-1 policy. Pass raw bytes to reject duplicate
JSON keys. The document is limited to 4 MiB, 256 applications, 128 distinct
principals and projects, 128 applications per principal, 4096 principal to
application grants, and 100 named components per application. Every grant is
explicit; there are no wildcard or ambient grants.

```json
{
  "schema": 1,
  "applications": {
    "shop": {
      "project": "shop-source",
      "principals": ["alice@example"],
      "deployment_root": "/srv/basaltwater/apps/shop",
      "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
      "limits": {"artifact_bytes": 2147483648, "artifact_entries": 100000},
      "components": {
        "web": {"type": "static", "domain": "shop.example", "path": "/"}
      }
    }
  }
}
```

Each application binds one build project, allowed authenticated principal
labels, an administrator-selected absolute deployment root, Debian 13 x86-64,
artifact byte and entry ceilings, and exact component route owners. Roots must
use safe canonical absolute path characters, cannot overlap another
application's root, and must have distinct target-generated service identity
basenames. The reserved `.basaltwater_shared` state path is not a deployment
root.

Domains are normalized to lowercase without a terminal dot; URL paths are
normalized by removing a trailing slash except for `/`. Conflicting normalized
domain/path routes are rejected across the policy. These checks are lexical;
the future target caller remains responsible for provisioning the root safely.

## Typed deployment projection

`parse_target_deployment(value, request=...)` requires exactly `schema`,
`application`, `project`, `platform` and `components`. Each component must match
the build request by name, type and exact artifact exports. The complete set of
component names must match. Routes must then match the application's named
policy routes exactly.

Every component contains `name`, `type`, `domain`, `path` and `artifacts`.
Static components also require a relative `output` covered by the exports,
using the manifest and Nginx character set `[A-Za-z0-9_./-]+`, and a boolean
`spa`. Nested output paths are allowed when an export covers them. Services
require a literal relative `binary` covered by the exports and may include a
safe `health` URL path. Service ports and generated service identities use
target defaults; client fields for `port`, `exec`,
`env_file`, `runtime_env`, environment values, commands, writable directories,
SQLite, UDP, or identity overrides are rejected as unknown fields.

The projection contains no build commands or build environment. The service
binary is checked for safe relative syntax, templates, systemd expansion and
export coverage, but this helper does not inspect whether the artifact exists
or is executable.

## Authorization and capabilities

Call `authorize_target_deployment(policy, principal=..., application=...,
request=..., deployment=...)` with `principal` from authenticated transport
state. It revalidates the policy and build request, checks principal, project,
platform, artifact ceilings, full component set and exact routes, then returns
`{"bindings": ..., "bindings_sha256": ...}`. Bindings are detached private
data and include the fixed deployment root. Hashing uses canonical compact
UTF-8 JSON with sorted keys and SHA-256. The caller should pass the digest to
`DeploymentAdmissionStore.reserve()` and keep the private bindings outside the
ledger. The build admission store persists only the digest.

`capabilities_for_principal()` returns only that principal's application and
project IDs, routes, platform, and artifact ceilings. It excludes deployment
roots, artifact paths, executable paths, build commands, environments, secrets,
and service identities. Unknown principals get an empty application map.

These functions do not authenticate, load policy, reserve a generation, read or
verify artifact files, check ELF compatibility, or activate anything. A target
transport must reserve intent before submitting a builder job, and a later
activation caller must recheck current policy, admission identity, artifact
integrity, and runtime compatibility. Standard local deployment behavior is
not changed by this module.
