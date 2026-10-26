# Managed build-target enrollment

`common.build_target_steps` installs explicit SSH authority for typed target
deployment requests. It performs no builds and installs no compiler toolchain.
Setup accepts `--build-target-policy FILE` and explicit `--no-build-target`.
The web capability selects this step for enrollment or removal, including on
the lite server profile. When selected, it replaces the legacy app-server
SSH/sudo setup. Omission keeps ordinary local deployment behavior.

## API

`parse_build_target_enrollment(value)` accepts a decoded mapping or bounded
UTF-8 JSON bytes. Raw JSON rejects duplicate object keys. The returned mapping
is detached and has exactly `schema`, `policy`, `keys`, `max_records`, and
`max_bytes`. Schema is integer `1`.

`policy` follows [`parse_target_policy`](build-target-policy.md). `keys` maps
each principal to one public key and must match exactly the union of principals
across applications. Initial enrollment labels use the builder's conservative
slugs: lowercase ASCII letters/digits with interior hyphens, at most 32
characters. Public-key validation reuses the builder's canonical Ed25519
validator: exactly one uncommented `ssh-ed25519` line, a canonical base64 SSH
wire blob containing the key type and one 32-byte public key. Private keys,
options, comments, duplicate keys, transport-name collisions, extra fields and
unsupported limits are rejected.

Limits match the [entrypoint](build-target-entrypoint.md): 1–4096 records and
256 MiB–8 TiB. Enrollment JSON is bounded to the policy's 4 MiB plus 256 KiB.
Deployment roots cannot overlap the protected source, private state,
configuration, SSH or sudoers trees. This check does not provision application
roots or permit a client to choose a different root.

`configure_build_target(config)` reads `config.build_target_enrollment` as an
already-decoded mapping. `None` is a no-op. Only an explicitly boolean
`config.disable_build_target=True` requests de-enrollment and takes precedence
over enrollment. Other disable types are rejected. Omitted enrollment never
revokes existing authority. There is no runtime `SetupConfig` import.

## Installation and authority

This slice supports root setup on Debian 13 x86-64 VM/physical hosts only. It
checks `can_manage_system_services(config.machine_type)` and refuses containers,
including privileged LXC. Dry runs validate enrollment and machine selection
without reading installed code or changing the host. The installed tree must
already be trusted at `/opt/basaltwater`; setup checks the entrypoint and its
complete importable package tree before installing packages or granting access.
It does not copy code from a setup checkout or user home.

Only missing `openssh-server`, `sudo` and `nginx` prerequisites are installed
and then verified. Python 3, system services and service runtime libraries come
from the existing OS. This installer does not edit SSH daemon configuration,
open firewall ports, install runtime dependencies or qualify service binaries.

Managed root:root resources are:

- `/etc/basaltwater/build-target` mode `0700`, with `config.json`,
  `.managed.json` and the stable `.maintenance.lock`, each mode `0600`.
- `/var/lib/basaltwater/build-target` and its `admission`, `artifacts`,
  `releases`, `verify` directories, all mode `0700`.
- `/var/lib/basaltwater/build-target-ssh` mode `0711`; each hashed principal
  home mode `0555`, `.ssh` mode `0755`, and `authorized_keys` mode `0644`.
- One mode-`0440` `/etc/sudoers.d/bw-target-<12-hex-id>` per enrolled principal.

Each principal gets a distinct locked system account and same-named group with
prefix `bw-target-`, a SHA-256-derived 12-hex suffix, the managed home and
`/bin/sh`. UID and GID must be positive and exclusive, including primary-group
users and group aliases; supplementary groups and explicit group members are
refused. No root SSH key is enrolled. The root-owned public SSH layout lets
sshd and the transport account read the key without permitting writes.

Before bootstrap or any installation mutation, enrollment checks every existing
ancestor of the SSH root for transport search access, in addition to the root
ownership, no-symlink and safe-mode checks. Other execute permission is required;
ancestors with a non-root group also require group execute permission because
that group could match a transport identity. An existing shared parent such as
root:root mode-`0700` `/var/lib/basaltwater` is refused without changing it.
Missing shared parents are created root:root with exact mode `0755`, independent
of umask; the SSH root retains exact mode `0711`. Explicit disable can still
revoke authority when an ancestor has become inaccessible.

Every key has `restrict` and exactly this forced command for its fixed slug:

```text
/usr/bin/sudo -n /usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_target.py --serve --principal alice
```

Sudoers permits only the exact Python command and arguments, preserves
`SSH_ORIGINAL_COMMAND`, and is checked with `visudo -cf` before publication.
There are no wildcards, `SETENV` grants, general sudo commands or interactive
access. The entrypoint independently checks the fixed protocol command.

## Coordination, updates and disable

`lib.build_target_maintenance.target_lease()` opens the stable root-owned,
single-link lock without following symlinks. Every entrypoint request holds a
nonblocking shared flock **before its first configuration load through its
response**. Setup takes a nonblocking exclusive flock before role mutations.
An active request causes enable/update/disable to refuse without modifying the
installation. Incoming requests during setup also refuse immediately. Setup
does not stop, cancel or wait for active requests.

The public `lib.build_target_maintenance.target_maintenance()` context manager
gives a future local administrative refresh wrapper the same nonblocking
exclusive ownership. Enter it **before** loading target configuration and keep
it around the complete `TargetReleaseActivator.reconcile_installed()` call and
response. It accepts no arguments and yields no value. Missing, unsafe, held or
disabled enrollment refuses; an active typed request or another maintenance
owner also refuses. It never bootstraps, changes the durable serving gate,
replaces/deletes the lock or changes enrollment. Exceptions release ownership;
the local action retains responsibility for its admission/activation recovery
guards. The installer separately uses `target_lease(installer=True)` because
explicit enrollment must also be able to inspect a disabled gate.

The sole first-install bootstrap exception creates the private configuration
directory, an incomplete ownership marker and the stable lock. Exclusive
directory creation prevents two initial installers from adopting each other's
resources. No account, key, sudoers, runtime configuration, package or data-root
change happens before the exclusive lease. A partial bootstrap is retained for
operator inspection. Later runs never recreate, replace or delete the lock.

Before changing a managed installation, setup checks the complete transport
namespace, recorded UID/GID identities, exact directory modes, single-link
regular-file ownership and SHA-256 inventory. Unmarked accounts, groups,
directories, SSH-home contents, configuration files or sudoers are refused.
Missing/modified owned files, symlinks and unsafe ancestors are also refused;
setup does not repair or adopt them. Retired identities remain in the marker.

Under the exclusive lease, setup durably closes the serving gate and records
an `updating` marker with the desired enrollment digest and file hashes before
mutations. Successful enrollment writes the final `enabled` inventory before
opening the gate. Explicit policy/key updates rotate keys and remove retired
principal authority; unchanged enrollment is idempotent. Disable removes only
the recorded configuration, sudo grants and forced-command key files and writes
`disabled`, leaving the gate held. A successful explicit re-enrollment can
reopen it.

Accounts, groups, SSH homes, the marker, lock, all target data and admission
evidence, application releases, services and Nginx publication are retained on
disable. Packages are not removed. De-enrollment revokes future transport access;
it does not uninstall applications or reconcile uncertain deployments.

## Failure and qualification limits

This is a deliberately fail-stop installer, not an automatic rollback or replay
engine. A package, account, validation, publication or interruption failure
after the durable hold keeps serving closed even after flock is released.
An incomplete marker rejects both enrollment retries and disable. Inspect the
recorded old inventory, desired hashes, accounts, files, application state and
admission/activation guards as a root operator before reconciling the marker.
Do not delete the stable lock or marker to bypass recovery. An enabled marker
with a held gate after a final gate-write failure can be retried explicitly
after its complete inventory passes preflight.

The lease coordinates this installer, the installed entrypoint and callers
that explicitly enter `target_maintenance()`. It does not coordinate unrelated
local deploy commands, root edits or external
replacement of installed code. Existing admission and activation uncertainty
guards still apply after a killed connection. There is no automatic garbage
collection of retired identities and no migration/adoption of manual target
installations. Controller builder selection and native Debian VM/SSH qualification
remain separate work. Mocked commands and temporary-directory/flock tests do not
establish native qualification.
