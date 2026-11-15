# Native Headscale hosting foundations

The target-side implementation is `web.headscale_steps.configure_headscale`,
wired through the native-network plugin with root administration and recovery
commands. See the
[implementation plan](../plans/headscale-implementation.md) and
[network contract](../plans/headscale-networking.md) and the selected
[qualification record](../testing/headscale.md). Offline tests alone do not
qualify deployment or client platforms.

## Installation contract

The step requires Debian, root, APT, persistent systemd services, active Nginx,
Certbot and an active project-managed UFW firewall. The parent setup plan must
provision those prerequisites first. Official native packages support amd64 and
arm64; this implementation pins 0.29.7 and verifies SHA256 and DEB metadata.

| Architecture | SHA256 |
| --- | --- |
| amd64 | `d230c5fc8db06cf1868f4ec0077b89e5e2ccf3b22a3259efa6d080db2d5f921c` |
| arm64 | `a68f258a2a13a87321671f8cc613500986ffc40d0d2eacfdbf0b890b8b94227e` |

Artifacts use the official release URL pattern
`https://github.com/juanfont/headscale/releases/download/v0.29.7/headscale_0.29.7_linux_ARCH.deb`.
The [tagged config example](https://github.com/juanfont/headscale/blob/v0.29.7/config-example.yaml)
is the configuration reference; the package supplies its native service account
and hardened systemd unit. Installation persistently masks the service before
APT runs and holds the package against unattended upgrades. The mask remains
after failed or interrupted installation, including across reboot. Activation
unmasks only after configuration validation.

The capability is independent of client enrollment. `headscale=None` performs no
work. `True` validates complete configuration and preflights declared and actual
conflicts before installation. `False` stops and disables a managed service,
retaining its endpoint binding, data, keys, configuration, policy and Nginx site.
Dry runs validate supplied settings without package, filesystem or service writes.

Provide a direct public control hostname such as `vpn.example.com`, a distinct
MagicDNS suffix such as `mesh.example.net`, and explicit relay IPv4 and/or IPv6
addresses reachable by the intended clients. Internet deployments need publicly
routable addresses; private deployments can use their own reachable addresses.
Public DNS, NAT and external firewalls must deliver
TCP 80/443 and UDP 3478 to this server. Address syntax cannot prove reachability;
that requires disposable-system qualification. Nginx listens on both families;
dual-stack STUN behavior also needs platform qualification. The control host
cannot join its own network. Candidate `headscale_resolvers` remain unverified
controller metadata and are never advertised.

The generated native config is JSON (valid YAML), with SQLite, loopback control
8080, metrics 9090 and gRPC 50443. The native management socket has mode 0600:
root and the isolated `headscale` service account can access it; ordinary users
and portal processes cannot. Config and policy are root-owned, readable by the
service group; data and keys are private to the service account. The manifest
and operation state under `/var/lib/basaltwater/headscale` are root-only.

Embedded DERP verifies clients, advertises explicit relay addresses and uses
direct STUN UDP 3478. Remote maps, local external maps and map auto-updates are
disabled. Logtail and update checks are disabled. Fresh networks receive an
explicit all-to-all ACL policy: approving enrollment grants access to the shared
network subject to host firewalls and service authentication. MagicDNS starts
with no global resolver override. Verified DNS adoption belongs to a later step.

Nginx terminates trusted TLS using existing Certbot webroot and renewal patterns,
forwards native control/WebSocket upgrades and replaces forwarding headers with
observed values. Access and error logging for this virtual host are disabled to
avoid recording native registration Auth IDs. Existing usable certificates are
reused. There is no browser login gate, SSO, route advertisement, exit node or
Cloudflare route for the control endpoint. Unrelated Cloudflare services remain
supported. Existing conventional Cloudflare YAML ingress files and effective
Nginx hostname bindings are checked; external/provider-managed routes require
operator reconciliation during qualification.

## Reruns, adoption and interrupted operations

Managed reruns retain native config (including later verified DNS), policy,
database and keys. A changed endpoint, base domain, version or inconsistent
manifest is refused. Ordinary setup does not upgrade an existing database.
The currently pinned setup renderer must be updated/requalified before ordinary
setup can reconcile a server upgraded beyond that pin.

Existing unmanaged directories, binaries, systemd units, occupied listeners or
conflicting ingress fail closed. Automatic adoption is intentionally unavailable.
Explicit root-reviewed adoption is available through `lib.headscale_adoption`
for the bounded subset below. Do not fabricate a managed manifest. The restore
adapter below accepts managed snapshots only; adoption creates and verifies a
compatible snapshot before publishing ownership.

### Explicit reviewed adoption

`inspect_adoption(config: SetupConfig, package: Path) -> dict` inspects the
explicitly requested network. `adopt_headscale(config, package, review_file,
backup, *, dry_run=False) -> ServerManifest` consumes its reviewed private JSON.
Neither API discovers authority, installs packages, changes native ownership,
rewrites config/policy/DNS, rotates keys, checkpoints data, or starts the daemon.
The only production writes are private journal, backup and ownership metadata.
The package input must be the pinned official DEB above, retained privately for
recovery; extraction is confined to temporary scratch storage.

Supported adoption requires all of these existing conditions:

- Debian root/service/firewall capabilities; pinned 0.29.7 amd64/arm64 package,
  installed package metadata and package hold. `/usr/bin/headscale` and
  `/usr/lib/systemd/system/headscale.service` must match the verified DEB bytes.
  The loaded unit must use that fragment (the Debian `/lib` alias is accepted),
  have no drop-ins or environment inputs, require no daemon reload, and be
  disabled and stopped. Adoption leaves it disabled and stopped.
- Exact default native paths, permissions and ownership described above;
  no symlink components, hardlinked files, writable ancestors or foreign owners.
  Config/policy are 0640 root:headscale; database/keys are 0600 headscale:headscale;
  config directory is 0750 root:headscale and data directory 0700 headscale:headscale.
- Strict JSON config semantically identical to `render_config(config)`, including
  explicit endpoint, relay addresses, prefixes, loopback admin listeners,
  native socket, DERP, policy file path and baseline DNS. Formatting is preserved.
  File policy contents can differ from the fresh default and are preserved
  byte for byte. Existing resolver overrides, split DNS, extra records, foreign
  settings, YAML syntax and database-backed policies require manual migration.
  Adoption does not reset DNS to make an unsupported server fit.
- SQLite integrity and exact schema equality with a fresh database initialized
  by the pinned native scratch validator. No WAL, SHM or rollback journal may
  exist. Native key formats and policy are checked by the existing isolated
  configtest. Native validation never runs against the production database;
  integrity/schema inspection uses read-only SQLite connections.
- Existing Nginx site bytes equal `render_nginx(domain)` at the managed site
  path, successful Nginx validation and active firewall with explicit public
  `80/tcp`, `443/tcp` and `3478/udp` ALLOW rules. Ordinary managed
  preflight also checks actual listeners, competing effective Nginx bindings
  and conventional Cloudflare ingress. Cloud tunnels remain unsupported for
  control hosting. External routing, TLS trust and reachability require explicit
  operator review; local inspection cannot establish those facts.

The schema-1 review has exactly the inspection output's fields: `schema`,
`authority`, `binding`, `files`, `package`, and `firewall`. `binding` is the exact managed
manifest (schema/version/endpoint/base_domain). `files` contains config, original
policy, both native keys, database and Nginx identities: SHA256, device, inode,
UID, GID, mode and size. `package` contains architecture, pinned DEB digest,
installed package identity, exact observed systemd fields, binary/unit identities.
`firewall` retains the exact inspected UFW status for review and comparison.
Treat the document as private operational evidence. Store it as a root-owned
0600 regular file in a root-owned 0700 directory. Review all identities and the
intended same-network authority independently, then change only `authority`
from `false` to JSON `true`. An endpoint lookup or reachable health URL is not
authorization. Extra fields, changed binding or changed observations refuse.

Adoption holds the shared Nginx and native operation locks, journals before
backup, verifies an exact private stopped snapshot, validates config/schema,
and rechecks all reviewed identities before publishing `adoption.json` and
`manifest.json`. The new backup destination must have a root-private parent.
Failures and uncertain publication retain a blocking operation marker and any
completed backup. Preserve these and reconcile actual state before recovery;
never clear the marker and blindly retry. The snapshot includes the original
config/policy/keys/database, a prospective manifest, Nginx and approved review.
It is compatible with the existing managed backup validator/restore API.

Activation is separate: after reviewing the completed backup and external
ingress, ordinary setup with the exact same settings can enable/start the adopted
server through its managed rerun path. That path retains original config, DNS,
policy, keys and data. Dry run checks private review authority and explicit
binding only; it creates no locks, scratch files, backup or metadata and does
not claim native validation or freshness of the review.

For unsupported servers, keep native administration or register an external
endpoint. Stop and disable the native service using its own operating procedure,
take a consistent upstream-native backup of config, policy, keys and database,
retain matching binary/package and independently verify recovery. Review upstream
schema/version migration requirements and explicitly convert on a disposable
copy, preserving endpoint, policy, DNS and identity. Requalify ingress and data
before cutover. Do not delete sidecars or reinitialize data to satisfy adoption.

Use the root-local maintenance commands with a private explicit configuration:

```json
{"version":1,"headscale_domain":"vpn.example.com","headscale_base_domain":"mesh.example.net","headscale_public_ipv4":"192.0.2.10","headscale_public_ipv6":null}
```

All five fields are required; at least one relay address must be supplied.
No endpoint discovery or unrelated setup options are accepted in this document.

```bash
sudo basaltw headscale-maintenance adopt-inspect --configuration /root/recovery/hosting.json --package /root/recovery/headscale.deb --destination /root/recovery/new-inspection.json
sudo basaltw headscale-maintenance adopt --configuration /root/recovery/hosting.json --package /root/recovery/headscale.deb --review /root/recovery/approved-inspection.json --backup /root/recovery/new-adoption-snapshot --dry-run
sudo basaltw headscale-maintenance adopt --configuration /root/recovery/hosting.json --package /root/recovery/headscale.deb --review /root/recovery/approved-inspection.json --backup /root/recovery/new-adoption-snapshot
```

Inspection writes only the explicitly requested new file, mode 0600, in an
existing root-owned mode-0700 directory; it refuses to overwrite any file.
Review a private copy and change only its authority field as described above.
Stdout contains completion metadata only. Both actions accept `--dry-run`, which
checks private input syntax without native calls or file/lock writes; adoption
also checks the approved binding. Adoption never runs automatically from setup
and is unavailable through portal HTTP actions.

Operations share a private lock and durable `operation.json` marker. Failure or
interruption leaves the marker to block blind retries. The shared Nginx lock
excludes ordinary Nginx reconciliation. Config is tested with actual
`headscale configtest` before atomic replacement. That command initializes
SQLite and private keys, so validation redirects its state into a private
temporary directory and copies existing keys there for native key validation.
It uses a fresh scratch database and cannot migrate or change ownership of the
production database. Headscale environment overrides are removed for the check.
The daemon initializes or migrates production state under its service account
during activation; health verification and the matching-data recovery procedure
cover that separate boundary. Failed native activation
restores the prior config when available and checks native health. First-install
failure stops the service and restores the prior Nginx file; usable certificates
and additive firewall allowances remain. Package installation and firewall rules
are outside config rollback.

After interruption, inspect the marker and actual package/service/config state
through root SSH. Preserve private state before recovery. Reconcile the
persistent service mask, config and database version before removing a marker;
do not merely delete the marker and retry. Restore writes are atomic per file,
not across the entire snapshot: an interrupted restore remains stopped and must
be completed from the same validated snapshot. No automatic purge is provided.

## Backups and recovery

The caller must stop the service and observe it stopped before backup, restore
or upgrade. These APIs do not stop a running service implicitly. This freezes
keys/config/policy with the database; SQLite's backup API consolidates committed
WAL content. Snapshots contain config, policy, Noise key, DERP key, manifest and
SQLite, plus version/endpoint binding, SHA256 integrity metadata and recovery
instructions. Directories are 0700 and files 0600. Store snapshots on trusted
private storage; digests detect corruption and do not authenticate an attacker
who can rewrite the entire snapshot. Backups contain credentials and device
identities and must never enter Git or public artifacts.
The bounded native adapter accepts individual files up to 64 MiB; larger
databases require an independently reviewed native backup procedure.

Restore requires an explicit exact `ServerManifest`, matching installed binary
version, consistent endpoint and native layout. All files and hashes, SQLite
integrity and a staged native configtest are validated before restore writes.
It stays stopped unless `activate=True`; activation failure stops it again.
Replacement hosts must first have the matching native package/account/unit,
direct endpoint routing, Nginx/TLS and firewall prepared independently. TLS keys
and Nginx are not included in these private network snapshots. Restore those
through their owning web/TLS recovery procedure and retain root SSH access.
Reconcile revocations made after the snapshot before reopening public ingress.

DNS-node identity, Unbound records/configuration and client enrollment have
[separate recovery procedures](headscale-recovery.md). These snapshots alone do
not recover those services or establish their network binding.

## Controlled upgrades

Follow the [upstream upgrade requirements](https://headscale.net/stable/setup/upgrade/):
review release notes, use latest patches and advance sequentially through minor
versions. `validate_upgrade` checks an explicitly supplied reviewed release
inventory; it does not discover releases or claim a caller's inventory is fresh.
The integration must obtain and review current upstream release metadata and
checksums before presenting an upgrade. Only the installation baseline above is
currently pinned/verified by this implementation.

`upgrade_headscale` requires verified target and matching recovery DEBs, stopped
service, reviewed release inventory and a new private backup destination. It
captures the preupgrade snapshot before installing the held target package,
tests config and native health, then updates the manifest. Failure stops the
target, installs the verified prior package, restores its matching database,
config, policy and keys, then checks health. A binary-only downgrade is never the
recovery path. Recovery failures retain the backup and blocking operation marker.
Stable native version observations normalize SemVer build metadata such as
`0.29.6+dirty` to `0.29.6`; prerelease labels remain unsupported. This does not
replace the reviewed package digest and exact DEB version/architecture checks.
Native migrations, fresh/replacement installs and recovery still need disposable
Debian qualification on both advertised architectures before release.

## Reusable Python interfaces

These APIs support the [root maintenance commands](headscale-recovery.md).
Paths use `pathlib.Path`.

| Module | Interfaces |
| --- | --- |
| `web.headscale_steps` | `configure_headscale(config: SetupConfig) -> None` |
| `lib.headscale_server` | `ServerPaths`, `ServerManifest(schema, version, endpoint, base_domain)`, `Release(version, architecture, sha256)`, `VERSION`, `SHA256` |
| `lib.headscale_server` | `render_config(config, paths=ServerPaths()) -> str`, `render_nginx(domain, *, tls=True) -> str`, `manifest_for(config, version=VERSION) -> ServerManifest`, `load_manifest(paths) -> ServerManifest \| None`, `preflight(config, paths) -> ServerManifest \| None` |
| `lib.headscale_server` | `configtest(path)`, `health(paths)`, `stopped()`, `installed_version() -> str`, `verify_package(package, release)`, `install_deb(package, release)`, `publish_config(content, paths, *, gid)` |
| `lib.headscale_server` | `require_capabilities(*, firewall=False)`, `runtime_operation(paths, kind)`, `private_directory(path, *, uid=0, gid=0, mode=0o700)`, `regular_bytes(path) -> bytes`, `version_tuple(version) -> tuple[int, int, int]` |
| `lib.headscale_backup` | `snapshot_files(paths) -> dict[str, Path]`, `backup_headscale(destination, *, paths=ServerPaths()) -> ServerManifest` |
| `lib.headscale_backup` | `validate_backup(source, *, expected, paths=ServerPaths()) -> dict[str, bytes]`, `restore_headscale(source, *, expected, paths=ServerPaths(), activate=False)` |
| `lib.headscale_backup` | `validate_upgrade(current, target, *, reviewed_releases)`, `upgrade_headscale(target_package, recovery_package, backup, *, current, target, reviewed_releases, paths=ServerPaths())` |
| `lib.headscale_adoption` | `inspect_adoption(config, package) -> dict`, `adopt_headscale(config, package, review_file, backup, *, dry_run=False) -> ServerManifest` |

Adoption alone supplies `expected_database_schema=paths.data / "db.sqlite"`
to `configtest`; other callers retain its existing behavior. This optional
read-only comparison against scratch schema is the narrow server-helper change
needed to reject foreign databases without migrating production data.
The scratch comparison reads its committed WAL; production schema inspection
remains immutable and never checkpoints or reconstructs production state.

Low-level mutation helpers require root and caller-held `runtime_operation`;
`configure_headscale`, backup, restore and upgrade enforce their own capability
checks/operation scope. Do not expose bytes returned by backup validation to a
portal or logs. The focused modules are included in the network test suite.
