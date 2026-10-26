# Target deployment process entrypoint

`common/service_tools/build_target.py` is one internal SSH forced-command
entrypoint for externally built target deployments. It accepts only
`--serve --principal LABEL [--config ABSOLUTE_PATH]`, requires root and
isolated Python, and requires the exact `SSH_ORIGINAL_COMMAND` value
`basaltwater-target-v1`. It serves one typed request with a 15-minute deadline.
The [target installer](build-target-setup.md) provisions its authority through
setup enrollment and the [selected-builder flow](build-setup.md).
Ordinary local deployment still builds on the destination.

## Protected installation and configuration

Install the script and its importable project packages in a root-owned tree
outside account-writable directories. The entrypoint validates the script,
all ancestors, and the same complete package set as `build_host.py` before it
adds the repository root to `sys.path` or imports project modules. Launch it
with `/usr/bin/python3 -I`. It does not probe or install target toolchains.

Before the first configuration read, `main()` takes the protected, nonblocking
shared lease from `lib.build_target_maintenance.target_lease()`. It holds that
lease until the complete typed operation and response finish. The fixed lock
is `/etc/basaltwater/build-target/.maintenance.lock`, regardless of `--config`;
an alternate configuration cannot bypass enrollment coordination. The installer
holds it exclusively for updates and disable. Requests arriving during an update
refuse immediately. A missing/unsafe lock, disabled enrollment or retained
incomplete-install hold also refuses before loading configuration. The stable
lock must be provisioned by the installer and must never be replaced or removed.

The default configuration is
`/etc/basaltwater/build-target/config.json`. Its parent must be root-owned mode
`0700`; the file must be a no-follow, single-link root-owned regular file mode
`0600`. Reads are bounded to the 4 MiB target-policy ceiling plus 64 KiB for
the outer configuration and are checked for file identity changes. A local
operator may select another protected path with `--config`; the SSH client
cannot select it through `SSH_ORIGINAL_COMMAND`.

The configuration has exactly these fields:

```json
{
  "schema": 1,
  "roots": {
    "admission": "/var/lib/basaltwater/build-target/admission",
    "artifacts": "/var/lib/basaltwater/build-target/artifacts",
    "releases": "/var/lib/basaltwater/build-target/releases",
    "verify": "/var/lib/basaltwater/build-target/verify"
  },
  "policy": {
    "schema": 1,
    "applications": {
      "shop": {
        "project": "shop-source",
        "principals": ["alice@example"],
        "deployment_root": "/srv/basaltwater/apps/shop",
        "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
        "limits": {"artifact_bytes": 2147483648, "artifact_entries": 100000},
        "components": {"web": {"type": "static", "domain": "shop.example", "path": "/"}}
      }
    }
  },
  "max_records": 128,
  "max_bytes": 68719476736
}
```

`policy` follows [target policy](build-target-policy.md). Every private root
must already exist as a distinct, non-overlapping root-owned `0700` directory
with trusted ancestors. Private roots cannot overlap any policy deployment
root. `max_records` is between 1 and 4096. `max_bytes` is between 256 MiB and
8 TiB. These quotas are passed to the target artifact store; admission itself
retains at most 4096 operations and 16 MiB of ledger state. Changing a root or
quota is coordinated by the exclusive installer lease; policy is reloaded for
each authorization operation. Direct root configuration edits must independently
exclude requests; structural changes observed within a request still refuse.

## Composition and current boundary

The process composes `DeploymentAdmissionStore` at
`admission/admissions.json`, `BuildTargetArtifactStore` with the artifacts
root, `BuildTargetService`, and `TargetReleaseActivator` with the releases and
verification roots. The admission, [dispatcher](build-target-service.md),
[policy](build-target-policy.md), and [activation callback](build-target-activation.md)
contracts are implemented together. The artifact store's `stage()` and
`open_verified_bundle()` APIs pin and verify artifacts without reentering the
admission store. Managed enrollment belongs to the installer and controller
setup composes the typed operations. This entrypoint does not install its own
authority. Internal `serve()` expects the caller to hold the request lease;
the SSH boundary is `main()`.

For the duration of `serve()`, the entrypoint retains a private, non-inheritable
binary duplicate of OS stdout solely for the protocol response. It redirects
both Python stdout and OS fd1 to stderr before importing and composing the
service. Deployment/Nginx diagnostics, native `os.write(1, ...)` calls and child
commands inheriting stdout therefore stay off the wire. Executed children do
not inherit the private protocol descriptor. Cached Python stdout buffers are
flushed while fd1 still points to stderr, including during failure cleanup.
The entrypoint restores Python stdout, fd1 and its original inheritance flag,
then closes the private duplicate on success, failure and interruption. Stdin
is preserved; the connection deadline still surrounds composition, serving and
descriptor cleanup, and `main()` retains the request lease throughout.

This descriptor isolation belongs to the single-connection entrypoint.
`BuildTargetService`'s Python-only redirection is insufficient for native or
inherited child output: another transport caller must independently isolate
its protocol channel. Ordinary local deployment output is unaffected.
Failures at the process boundary emit one fixed generic message and never
include paths, environment values or credentials. Admission and activation
guards retain uncertain work for operator reconciliation; the entrypoint never
retries it automatically.
