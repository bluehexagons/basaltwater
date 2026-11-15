# Private Headscale and DNS recovery

Use independent root SSH access on the service host. The standalone adapter is
available as `python3 -m lib.headscale_maintenance_cli` and through the root CLI
as `basaltw headscale-maintenance`. These are local operations, with no SSH transport,
release discovery, enrollment, automatic service stop or backup scheduler.
See [hosting](headscale-hosting.md), [native clients](headscale-clients.md) and the
[network contract](../plans/headscale-networking.md).

All examples use fictional paths. Create a trusted root-owned mode-0700 recovery
directory first. Private JSON inputs must be root-owned, single-link regular files
with no group/other access (normally 0600); symlinks and writable ancestors are
refused. Inputs are bounded at 256 KiB. Keep snapshots, release inventories and
actual identities outside Git. SHA256 detects corruption, not malicious rewriting
of a complete snapshot: storage and transport must be trusted and confidential.
Stdout reports action and completion/dry-run booleans only. Errors suppress private
inputs and native diagnostics. Preserve private evidence when an operation fails;
do not infer success from a missing response or retry a mutation blindly.

Every action accepts `--dry-run`. It validates argument syntax and reviewed JSON
inputs only, with zero writes, locks or native calls. It does not validate snapshot
contents, package bytes, native configuration, privileges or live service state.

## Headscale snapshot and restore

Stop Headscale explicitly and verify it stopped before backup, restore or upgrade.
The adapter refuses a running service. Retain exact matching official DEBs and
independently reviewed SHA256 digests before any upgrade. Back up:

```bash
sudo systemctl stop headscale
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance backup --destination /root/recovery/new-headscale-snapshot
```

The new destination contains config, policy, Noise/DERP private keys, managed
manifest, SQLite (including committed WAL contents), integrity metadata and
recovery guidance. Each native file is limited to 64 MiB. TLS/Nginx, certificates,
firewall, Unbound and native Tailscale state require separate recovery.

Prepare `manifest.json` privately with the exact reviewed snapshot binding:

```json
{"schema":1,"version":"0.29.7","endpoint":"https://vpn.example.com","base_domain":"mesh.example.net"}
```

```bash
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance validate --source /root/recovery/new-headscale-snapshot --manifest /root/recovery/manifest.json
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance restore --source /root/recovery/new-headscale-snapshot --manifest /root/recovery/manifest.json
```

Validation performs digest, binding, native-layout, SQLite integrity and staged
native config checks; it creates private temporary staging. Restore requires the
exact installed version and matching managed endpoint. Prepare the native service
account/package/unit, direct endpoint routing, web/TLS and private firewall on a
replacement host independently. Restore stays stopped; add `--activate` only after
reviewing access and restored DNS settings. Failed activation stops Headscale.
Reconcile revocations after the snapshot before reopening ingress; a restored
database can resurrect revoked nodes or grants. Restored DNS settings also need
independent identity and peer-health verification before exposing the server.

## Controlled upgrade

Privately review upstream release notes, compatibility and official checksums.
`current-release.json` and `target-release.json` each contain exactly
`version`, `architecture` (`amd64` or `arm64`) and lowercase 64-digit `sha256`.
`releases.json` is a reviewed JSON array of those objects (1–512 entries). It is
operator evidence, not automatic discovery or a guarantee of freshness.

```bash
sudo systemctl stop headscale
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance upgrade --target-package /root/recovery/target.deb --recovery-package /root/recovery/current.deb --backup /root/recovery/new-preupgrade --current-release /root/recovery/current-release.json --target-release /root/recovery/target-release.json --release-inventory /root/recovery/releases.json
```

The API requires increasing patches or sequential minor upgrades on one
architecture, and latest reviewed patches when crossing minors. It verifies both
DEBs, captures a new preupgrade snapshot, installs the held target and checks
configuration/health. Success starts Headscale. Failure attempts recovery using
the verified prior package **and its matching database/config/keys**, then checks
health. Never downgrade a migrated database by replacing only its binary.
Failure retains the private backup and blocking operation marker, even when
recovery appears successful; reconcile actual version, database and service state
before resolving the marker. See [transaction recovery](../operations/transaction-recovery.md).

## Unbound snapshot and restore

Keep Tailscale online for native network/identity/interface observation, while
stopping Unbound explicitly. Backups capture managed fragment, local records,
defaults, service drop-in, manifest and exact `DNSBinding` including publication
state as evidence. They exclude cache, native client secrets, packaged fragments,
firewall, runtime service enablement and trust-anchor data. The complete encoded
snapshot must fit 256 KiB. Native files use their existing safe permission modes.

```bash
sudo systemctl stop unbound
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-backup --destination /root/recovery/new-unbound.json
```

Save the exact reviewed snapshot binding in a private `binding.json`:

```json
{"version":1,"network_url":"https://vpn.example.com","node_id":"7","addresses":["100.64.0.2"],"advertised":false,"publication_id":null}
```

For an advertised snapshot retain its actual `advertised: true` and publication
UUID in this reviewed input. Snapshot validation requires exact equality.

```bash
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-validate --source /root/recovery/new-unbound.json --binding /root/recovery/binding.json
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-restore --source /root/recovery/new-unbound.json --binding /root/recovery/binding.json
```

Validation is read-only and checks integrity, canonical managed config, records,
identity and exact native layout. Prepare distribution packages and their stock
main/control/trust-anchor fragments independently on the recovery host. Restore
accepts only this layout, validates the candidate with native `unbound-checkconf`
in staging before replacement, and never copies caller-selected paths.

Before restoring, withdraw/replace the resolver using the owning DNS publication
workflow and verify withdrawal independently. An advertised or reserved current
binding blocks restore under the same lease used by publication and client
teardown. Never delete a binding file to bypass this guard. Restore always saves
an **unadvertised** binding, even if the snapshot was advertised, and leaves
Unbound stopped. Files are replaced atomically one at a time; a failure after
writes begin leaves a blocking `operation.json`. Preserve snapshot, marker and
actual state, then reconcile through independent administration before retrying.
Keep the service disabled or masked through independent administration during
recovery so a reboot cannot activate partially restored state.

After restore, reconcile the native unit and private UFW rules through managed
Unbound configuration with the intended network settings/local records. This is
required before activation, especially following an address change. Debian's
packaged trust-anchor helper must maintain a current DNSSEC root key; do not
restore a stale anchor from backup. Verify UDP/TCP health identity, recursion and
valid/bogus DNSSEC from another enrolled client, then explicitly republish through
the DNS adoption workflow. There is no automatic publication or public fallback.

## Failed fresh Unbound package setup

A fresh APT attempt is never replayed by recovery. Setup refreshes APT metadata
with a five-minute bound and installs with a ten-minute bound, without retries.
Any failed fresh package attempt or later activation retains `operation.json`
and `recovery.json`, even after successful file/unit rollback. The operation
records the observed unpublished identity, original snapshot SHA256 and package
presence before the attempt. Package effects and trust-anchor data remain outside
file rollback. A verified not-found/inactive fresh unit needs no stop or disable.

Use `unbound-recover-fresh` only to confirm an independently restored **absent**
installation. It never installs, removes, adopts or restores packages or native
identity. If Unbound is installed, partially configured, running or has remaining
configuration, preserve evidence and reconcile through independent administration
first. Review APT/dpkg logs and the retained package inventory. Stop and disable
the candidate, verify quiescence, and preserve any new configuration and package
data privately before an explicitly reviewed package removal. Never purge data,
autoremove dependencies, delete native client identity, or discard unrelated
configuration to satisfy the guard. Restore each transaction file to its recorded
baseline; null means absent. Preserve unrelated files. If this baseline cannot be
established safely, keep the operation blocked for further review.

After package reconciliation, reload systemd and verify `unbound.service` has
`LoadState=not-found`, `ActiveState=inactive` (or `failed`) and empty
`UnitFileState`. Unbound must have no dpkg record (or status `not-installed`), stock main
configuration or `*.conf` fragments. All managed files and the manifest must be
absent, matching the retained fresh snapshot. Residual `config-files` package
state also blocks recovery: deleting conffiles while retaining dpkg's old
conffile decisions can prevent a later fresh install from restoring them.
Reconcile that package metadata independently while preserving package data;
this adapter never runs a purge. There must be no managed firewall
rules, conflicting resolver listeners or DNS binding/publication reservation.
Do not erase a binding to bypass publication withdrawal. Auxiliary packages and
unrelated files are preserved. Keep other package/configuration writers quiesced
through review and recovery; the locks exclude cooperating Basaltwater writers,
not independent root administration or APT processes.

Privately create `/root/recovery/fresh-review.json`, mode 0600, containing exactly
three keys: `operation` (the **complete, unchanged** retained operation JSON,
including its UUID, phases, timestamps and context), `backup_sha256` (lowercase
SHA256 of the exact retained `recovery.json` bytes), and `binding` (the reviewed
unpublished DNSBinding for the actual enrolled native node, in the format above).
Review these independently rather than guessing an operation ID or copying an
old review. Keep copies of both original evidence files outside Git.

```bash
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-recover-fresh --review /root/recovery/fresh-review.json --dry-run
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-recover-fresh --review /root/recovery/fresh-review.json
```

The command holds publication and operation exclusion, checks the exact reviewed
marker and snapshot, observes native identity, and verifies absence twice before
completion. It archives the snapshot as `recovery.json.<operation-uuid>.resolved`
and saves the completed marker in `operation.json.last.json`. It then removes the
blocking evidence from its active paths. Changed evidence, uncertain package
status, a live owner, identity drift or publication state refuses completion.
A legacy fresh failure whose context contains only the backup path can also be
resolved at this strict absent baseline; its reviewed identity is verified live,
but the older marker cannot establish the identity observed before failure.
The API is `recover_fresh_unbound(reviewed=review, paths=UnboundPaths())`.

Only after confirmed recovery may the operator start a new normal setup attempt.
That is a new transaction, not replay of the uncertain install. Native enrollment
and peer verification/publication remain separate. An existing managed resolver
uses its original rollback path; this command refuses its non-null snapshot.

## Native identity recovery or new enrollment

`node_id` metadata is evidence, not a credential or portable identity. Preserve
`/var/lib/tailscale` and `/var/lib/basaltwater/tailscale` together using a separately
reviewed native, stopped-daemon backup procedure and private encrypted storage.
Native state contains machine/node secrets. Do not print it, put it in Git or
copy a node ID into client metadata and call that recovery. Restoring native
state to another machine is not qualified here; prevent concurrent use of the
same identity, follow platform-native recovery guidance and verify the actual
network, observed node ID and addresses after recovery. The maintenance adapter
never reads or writes native client state and cannot guarantee portable identity.

If private native state is unavailable or native recovery is unsuitable, revoke
the lost node administratively and perform explicit new enrollment using the
[client workflow](headscale-clients.md). Observe and independently review the new
native identity and addresses. To recover local records onto that new identity,
write a private unpublished `DNSBinding` document for the observed new node:

```bash
sudo python3 -m lib.headscale_maintenance_cli headscale-maintenance unbound-restore --source /root/recovery/new-unbound.json --binding /root/recovery/binding.json --reviewed-rebind /root/recovery/new-binding.json
```

Rebind requires the same network, an unadvertised current binding (or no binding
on a prepared replacement), and an exact match to the live native identity. It
re-renders listening addresses/health identity from the reviewed new binding;
it does not restore the old advertised lease. Complete private firewall/unit
reconciliation, peer verification and explicit DNS republishing as above.

Offline mocked tests establish ordering, corruption/identity rejection, publication
guard behavior, private permissions, dry-run isolation and failure markers.
Disposable Debian qualification remains required for package migrations,
replacement-host recovery, trust-anchor maintenance, native identity portability,
peer connectivity, reboot and DNS republishing on each advertised architecture.
