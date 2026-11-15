# Native Headscale hosting foundations

The target-side implementation is `web.headscale_steps.configure_headscale`.
Plugin ordering and the root administration commands must still be integrated
before this is an advertised setup capability. See the
[implementation plan](../plans/headscale-implementation.md) and
[network contract](../plans/headscale-networking.md). No live deployment or
client/platform qualification is claimed by the offline tests.

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
MagicDNS suffix such as `mesh.example.net`, and explicit globally routable relay
IPv4 and/or IPv6 addresses. Public DNS, NAT and external firewalls must deliver
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
For an existing native server, stop and take an upstream-native backup of
`/etc/headscale` and `/var/lib/headscale` before considering migration. Keep it
under native administration and register it as an external endpoint through the
saved-reference integration. Do not fabricate a managed manifest. Migration to
this layout needs an explicit reviewed conversion preserving endpoint, policy,
keys and matching database/version; that converter is a remaining integration
gap. The restore adapter below accepts managed snapshots only.

Operations share a private lock and durable `operation.json` marker. Failure or
interruption leaves the marker to block blind retries. The shared Nginx lock
excludes ordinary Nginx reconciliation. Config is tested with actual
`headscale configtest` before atomic replacement. Failed native activation
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

Restore requires an explicit exact `ServerManifest`, matching installed binary
version, consistent endpoint and native layout. All files and hashes, SQLite
integrity and a staged native configtest are validated before restore writes.
It stays stopped unless `activate=True`; activation failure stops it again.
Replacement hosts must first have the matching native package/account/unit,
direct endpoint routing, Nginx/TLS and firewall prepared independently. TLS keys
and Nginx are not included in these private network snapshots. Restore those
through their owning web/TLS recovery procedure and retain root SSH access.
Reconcile revocations made after the snapshot before reopening public ingress.

DNS-node identity, Unbound records/configuration and client enrollment recovery
are separate later deliverables. These snapshots alone do not recover those
services or establish their network binding.

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
Native migrations, fresh/replacement installs and recovery still need disposable
Debian qualification on both advertised architectures before release.

## Reusable Python interfaces

These are Python APIs, not new root CLI commands. Paths use `pathlib.Path`.

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

Low-level mutation helpers require root and caller-held `runtime_operation`;
`configure_headscale`, backup, restore and upgrade enforce their own capability
checks/operation scope. Do not expose bytes returned by backup validation to a
portal or logs. New test modules must be registered in the appropriate parent
test suites when the plugin/CLI integration lands.
