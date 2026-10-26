# Shared build-host policy

Status: internal. The [process entrypoint](build-host-entrypoint.md) applies
these pure helpers at SSH admission and worker launch. Setup enrollment and
controller selection remain pending. Standard deployments remain unchanged.

`lib.build_host_policy.parse_build_host_policy()` accepts a decoded mapping or
raw JSON bytes and returns a normalized, detached version-1 policy:

```json
{
  "schema": 1,
  "principals": {
    "alice@example": {
      "projects": {
        "site": {
          "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
          "runtimes": ["node"],
          "limits": {"timeout_seconds": 3600, "memory_bytes": 2147483648, "cpu_percent": 100, "scratch_bytes": 8589934592, "artifact_bytes": 2147483648, "artifact_entries": 100000, "log_bytes": 4194304}
        }
      }
    }
  }
}
```

Every principal needs an explicit project grant. Principal labels follow the
durable build owner format; project keys use build-request slugs. Grants allow
only Debian 13 x86-64, `go`/`node`/`python` build runtimes, and the fixed
`static`/`service` component types. Resource ceilings use the shared build
request parser, including its bounds and MiB alignment for scratch capacity.

Call `authorize_build_request(policy, principal=..., request=...,
requested_runtimes=...)` with the principal from authenticated transport state.
The runtime list must come from `build_manifest.required_tools()` after the
pinned manifest is parsed. Authorization reparses the current policy and build
request, checks project, platform, component types, tools, and every requested
limit, then returns only that project's grant. `capabilities_for_principal()`
returns only the named principal's projects and grants; unknown principals get
an empty map. Neither function returns commands, build environments, or
credentials. Transport authentication, policy loading, tool probes, queue
admission, and worker rechecks remain caller responsibilities.
