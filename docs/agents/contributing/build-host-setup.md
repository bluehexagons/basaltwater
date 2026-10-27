# Build-host setup

`common.build_host_steps` owns target-side installation and explicit
de-enrollment of the on-demand build-host service. Configuration parsing and
plugin selection are owned by the setup configuration and plugin layers.

## Python API

`parse_build_host_enrollment(value)` accepts either a decoded mapping or
bounded UTF-8 JSON bytes. It returns a detached mapping with exactly
`schema`, `policy`, `keys`, `max_records` and `max_bytes`; schema is integer `1`.
Use the [complete enrollment template](../../examples/builds/build-host.example.json)
and its [replacement instructions](../../examples/builds/README.md#enrollment-templates).
The template requires your canonical public key before validation or use and
provides explicit project grants, all seven resource ceilings and a matching
principal/key set. Size the total quota for complete reservations, including
scratch, artifacts, source, descriptor and metadata allowances. Default requests
alone permit 8 GiB scratch and 2 GiB artifact payloads; a 2 GiB total quota
cannot admit them.

The shared build-host policy parser validates `policy`. `keys` must have the
same principal set as that policy. Initial principal labels are lowercase
ASCII slugs of at most 32 characters: letters and digits, with optional
interior hyphens. Each value must be one canonical, uncommented
`ssh-ed25519` public-key line whose SSH wire blob contains one 32-byte public
key. Key options, comments, private keys, other key types, duplicate keys,
extra fields, and out-of-range limits are rejected.

`configure_build_host(config)` reads the already-decoded
`config.build_host_enrollment` mapping. `None` is a no-op unless
`config.disable_build_host` is explicitly `True`. The disable flag takes
precedence over enrollment; it is intended for an explicit setup transition,
not an inference from omitted policy. The function does not import
`SetupConfig` at runtime.

## Supported host and installed resources

An enabled build host must be Debian 13 x86-64 on a VM or physical machine.
Unprivileged and privileged LXC are unsupported even when a container can
start persistent services. The source must already be installed as a trusted,
root-controlled tree at `/opt/basaltwater`; setup does not copy code from a
user home directory. A requested role on an unsupported machine type fails
before package, account, or configuration changes once service capability and
dry-run handling permit setup to continue.

The installer creates root-owned mode-0700 configuration and data
directories:

- `/etc/basaltwater/build-host`, with a mode-0600 runtime `config.json` and a
  mode-0600 ownership marker and `ssh-admission` inventory.
- `/var/lib/basaltwater/build-host/{state,inputs,scratch,artifacts,logs}`.
- `/var/lib/basaltwater-build-host-ssh` is root-owned mode 0711 so transport
  accounts can traverse the known path without listing other homes. Each
  `/var/lib/basaltwater-build-host-ssh/<12-hex-id>` home is root-owned mode
  0555; `.ssh` is root-owned mode 0755 and `authorized_keys` is root-owned
  mode 0644. The transport account can read the public key but cannot write
  any path component or key file.

The public transport root is a fixed sibling of the private
`/var/lib/basaltwater` tree. Ordinary setup keeps that private parent mode
0700; enrollment and refresh neither widen it nor change unrelated ACLs or
state permissions. Before any installation mutation, enrollment checks all
existing SSH ancestors for root control, safe modes, no symlinks and transport
search access. Other execute permission is required; a non-root group also
requires group execute permission. Blocked or unsafe ancestors are refused
before packages, identities, keys or sudo grants are changed. Newly created
shared ancestors receive exact root:root mode 0755 regardless of umask.
Existing transport directories and keys must have the exact public modes
above. Explicit disable can revoke authority even if an ancestor loses search
access, provided its ownership and safety checks still pass.

Installer ownership markers now use schema 2; enrollment and runtime
configuration remain schema 1. Schema-1 markers and any existing legacy
`/var/lib/basaltwater/build-host-ssh` path (including a symlink) refuse both
enrollment and disable before mutations, with operator guidance. There is no
automatic migration, account-home rewrite or adoption of old files. An operator
must reconcile the old installation or perform a clean reinstall: preserve the
marker, accounts, queue, ledger and other evidence, drain/check active work and
revoke old key/sudo/service authority under operator control before replacing
the enrollment. Do not widen the private parent, move homes or edit/delete the
marker to bypass the refusal. Native lab migration is separate operator work.

The `basaltwater-build` worker account and group use `/nonexistent` and
`nologin`. Each principal gets a separate locked transport account and
same-named private group. Its shell is `/bin/sh`, it has no supplementary
groups, and its UID/GID must be exclusive. The enrolled public key is installed
with OpenSSH `restrict` and a forced command that invokes the trusted installed
entrypoint with that principal. Per-account sudoers permits only the exact
entrypoint arguments for that principal and preserves
`SSH_ORIGINAL_COMMAND`; `visudo` validates the file before publication. No
worker `authorized_keys` file is created, and the transport identity receives
no interactive or general sudo access.

The shared [SSH admission contract](../../operations/ssh.md#managed-build-transport-admission)
adds only the exact enrolled transport primary-group names to managed
`AllowGroups`, alongside `remoteusers`. It grants no worker SSH, supplementary
group or RDP access. Enrollment records the group list in the root-owned
`ssh-admission` file and its hash in `files.ssh`. While drained, with the worker
lease held and the unit inactive, setup refreshes only the existing managed
hardening drop-in's `AllowGroups` line and validates/reloads SSH. On a host
without that drop-in it records admission for subsequent hardening, leaving
ordinary SSH policy intact. Hardening reruns validate and preserve the inventory.

Existing schema-2 markers without `files.ssh` migrate on an explicit guarded
enrollment rerun. Hardening alone refuses an active enrollment without this
inventory; it cannot migrate outside the role's idle ownership. Schema-1
markers still require operator reconciliation. SSH validation/activation
failures retain the drain and existing reconciliation evidence.

The schema-2 marker's optional `ssh_pending` field records an exact `create`
or `remove` action and inventory `sha256` before the corresponding file change.
Creation is tied to the pending enrollment's exact primary groups; removal
requires the disabled state. Only these recorded transitions permit an absent
inventory with a recorded hash. Publication, unlink or subsequent marker-save
failure can therefore resume under the same drain, worker lease and inactive
unit checks. Retry the identical enrollment after interrupted creation, or
explicitly disable it. After interrupted removal, finish explicit disable
before re-enrollment. Completed installs with missing inventory still refuse
enrollment and disable for operator review; an ordinary pending enrollment
alone does not authorize recreating a missing inventory. Older schema-2 markers
without `ssh_pending` normalize to no transition; schema-1 adoption stays refused.

The independent root unit is `basaltwater-build-host.service`. It uses the
installed entrypoint in isolated `--worker` mode, `UMask=0077`,
`KillMode=control-group`, and `Restart=no`. Disabling automatic restart avoids
replaying a job whose claim outcome is uncertain. Required packages are
`git`, `e2fsprogs`, `openssh-server`, and `sudo`; `nodejs` is added only when
Node installation is selected. Go installation remains with the common
setup flow, and Python uses the operating-system Python 3.
Prerequisite installs use the shared 300-second APT lock timeout.

## Drain, update, and de-enrollment

Before changing policy, tools, or the unit, setup sets the job store to
draining, acquires the worker lease, and checks the retained ledger for a
running claim. It never stops an active worker to force an update. If the lease
is busy, the prior drain setting is restored when no reconciliation is
pending. If a running claim or incomplete reconciliation makes state
uncertain, setup leaves the store drained and refuses the update. On a
successful idle enable or update, the prior drain setting is restored; an
explicit re-enrollment after de-enrollment releases the intentional hold.
Queued jobs remain queued through handoff.

Only `disable_build_host=True` requests de-enrollment. It drains and checks the
worker lease and retained running claims before removing the managed service.
If the worker is active or a running claim remains, it refuses teardown and
does not stop the worker. A successful de-enrollment removes only the
marker-owned service, runtime configuration, enrolled forced-command key, and
per-principal sudoers grant. The ownership marker, accounts, groups, homes,
queue, ledger, inputs, scratch files, artifacts, logs, and other evidence are
retained. The store stays drained until a later successful re-enrollment.
Disable also removes the managed primary groups from SSH admission and removes
the recorded `ssh-admission` file. The shared SSH policy lock is retained.

The installer never adopts unmarked accounts, paths, keys, sudoers files, or
units. A missing marker with build-host resources, or a modified managed file
whose ownership hash no longer matches, is refused for operator review. An
ordinary setup with no enrollment and no explicit disable flag does not remove
anything. Package installation is additive; de-enrollment does not remove
packages or delete managed identities and data.

Focused tests mock host commands and use temporary paths. They do not execute
project commands or qualify a real Debian host; native VM qualification is a
separate deployment check.
