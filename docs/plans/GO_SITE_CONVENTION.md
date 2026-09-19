# Go Site Deployment Convention

Status: proposed P1 implementation plan. This convention should land before
the next fresh production web VM is provisioned.

This project defines a versioned `go-site/v1` contract for building and
running small Go web services with Basaltwater. Its purpose is to make the safe
path require little or no repository-specific deployment configuration while
keeping multi-service repositories, public non-HTTP listeners, secrets, and
data migrations explicit.

The convention must compile into the existing validated manifest and
transactional activation path. It is not a second deployment engine.

## Decision summary

- A root Go module with one conventional server entry point can use
  `go-site/v1` without a `basaltwater.json` file.
- Nested Go modules are discovered and reported, but never activated
  implicitly. Activating one requires an explicit component selection.
- Apps consume one standard environment contract for their primary listener,
  public URL, proxy trust, and managed directories.
- Basaltwater owns TLS termination, service users, ports, systemd hardening,
  health gating, release activation, and supported backups.
- The primary HTTP listener is loopback-only. Public UDP, raw TCP, direct TLS,
  and additional listeners are explicit capabilities rather than inference.
- `/healthz` is liveness and `/readyz` is deployment readiness. Activation is
  gated on `/readyz` returning 2xx.
- SQLite databases in the managed data directory receive online backups by
  convention. Other mutable files require an explicit backup policy.
- Sibling `cmd/*` main packages are built as operator tools but are never run
  automatically. Application and data migrations remain deliberate actions.
- Resolved convention output is previewable, versioned, and recorded with the
  deployment so a Basaltwater upgrade cannot silently reinterpret a project.

## Evidence from current projects

The design is based on the three Go services expected on the next server, not
on an abstract framework.

| Project | Repository shape | Listener/config today | Persistent state | Deployment-specific exception |
| --- | --- | --- | --- | --- |
| `goclick` | Root module; `cmd/server`; sibling `cmd/migrate-rails` | `GOCLICK_ADDR`; `/up`; loopback proxy trust | One SQLite database | One-time Rails database import; no recurring migration hook |
| `antistatic-server` | Root module; root `main.go`; package.json is protocol tooling | CLI flags for HTTP, TLS, proxy, STUN, and profile; `/health` | Optional append-only JSONL reports; optional admin credentials | Public UDP STUN and file-set backup are outside the primary web convention |
| `bluehexagons` | Root Node static site; nested `server` Go module | Backend uses `LISTEN_ADDR`; `/api/health`; env-only secrets/config | SQLite database plus uploaded shop assets | Backend is intentionally disabled and must not be inferred from the nested module |

These projects establish several constraints:

1. A repository-level `package.json` does not prove that Node is needed to
   build its Go service. Antistatic uses Node only for protocol generation and
   checks.
2. Discovering a nested `go.mod` must not imply deployment. Doing so would
   activate the unfinished bluehexagons backend.
3. SQLite is common but not universal. Antistatic stores bounded JSONL files,
   while bluehexagons combines SQLite with uploaded assets.
4. Auxiliary commands are useful release artifacts, but automatically running
   them would make a data migration an unsafe side effect of deployment.
5. Direct application TLS is useful for standalone/manual operation but should
   be disabled when Basaltwater and Nginx own the public endpoint.

## Goals

- Make a conventional standalone Go site deploy with no repository manifest.
- Reduce a composed static-site-plus-Go-service manifest to component
  selection and genuine exceptions.
- Give every Go site the same listener, health, state, secret, service, and
  rollback expectations.
- Keep inferred behavior inspectable and stable enough for later CI/CD artifact
  builds to use the same contract.
- Make the fresh-VM goclick cutover reproducible without carrying Ruby or Rails
  into the new server baseline.

## Non-goals

- Inferring or executing database migrations.
- Activating every executable or nested Go module found in a repository.
- Automatically exposing public UDP or raw TCP ports.
- Guessing required secrets from source code or environment-variable names.
- Treating arbitrary files as safely backed up because they live in a data
  directory.
- Replacing application-level configuration or feature flags.

## `go-site/v1` discovery and selection

### Zero-configuration root service

A repository qualifies for implicit `go-site/v1` only when all of these are
true:

1. `go.mod` is at the repository root.
2. Exactly one conventional primary entry point is found, in this order:
   `./cmd/server` or a root `main` package.
3. No explicit deployment manifest selects a different topology.
4. The deployment target supplies a public domain.

If both entry points exist, or multiple plausible server commands are found,
inference fails with an explanation instead of picking one. Library-only
modules are not deployable sites.

The implicit component defaults to:

- convention: `go-site/v1`;
- component name: `app`;
- domain: the deployment domain;
- public path: `/`;
- internal port: stable automatic assignment;
- service entry point: `./cmd/server` or `.`;
- readiness path: `/readyz`; and
- state root: the component's Basaltwater-managed shared directory.

### Nested modules and monorepos

Basaltwater should recursively discover runtime markers for planning, while
ignoring dependency and generated directories. A nested Go module is reported
as a candidate with its source path, entry points, Go version, and likely
operator commands. It is not included in the desired deployment until an
explicit component selects it.

The compact composition form should be versioned rather than copying expanded
build commands and systemd details into each repository. The target schema is:

```json
{
  "version": 2,
  "components": [
    {
      "name": "site",
      "use": "node-static/v1"
    },
    {
      "name": "shop-api",
      "use": "go-site/v1",
      "source": "server",
      "path": "/api"
    }
  ]
}
```

`domain` defaults to `{{domain}}`, `source` defaults to `.`, and `path`
defaults to `/`. The second component is simply omitted while the backend is
disabled. An `enabled: false` repository declaration should not be required to
prevent automatic activation.

Version 2 is intentionally proposed rather than overloading the current strict
version-1 component schema. Both versions resolve to the same internal
`Manifest`/`Component` model before deployment.

### Explicit manifests and overrides

An explicit low-level version-1 component remains available for unusual
services. A version-2 convention component may override only application-level
facts such as source directory, public path, readiness path, arguments,
environment values, backup policy, and additional listeners. It may not
replace the generated systemd unit or weaken mandatory hardening.

## Build contract

For each selected `go-site/v1` component, Basaltwater should:

1. Read the component module's `go.mod` and install a compatible current patch
   release for its declared Go major/minor line.
2. Build under the application's persistent, isolated build account.
3. Run `go test ./...` in the selected module before activation.
4. Build the primary service with `go build -trimpath` into
   `.basaltwater/bin/server`.
5. Discover other immediate `cmd/*` main packages, build each into
   `.basaltwater/bin/<command>`, and record them as operator tools.
6. Validate that every declared output exists and is executable before stopping
   the active service.

The default does not force `CGO_ENABLED=0`, a target architecture, or stripped
debug information. Those choices can break otherwise valid Go projects and
belong in an explicit build policy. Target-VM builds naturally compile for the
target. Later CI/CD builds must record GOOS, GOARCH, Go version, checksums, and
the resolved convention in artifact metadata.

Only selected component builds determine runtime installation. An unrelated
root `package.json` must not install Node for an Antistatic server deployment.
If a Go test or generation step genuinely requires Node, that dependency is an
explicit build capability.

## Runtime environment contract

Basaltwater supplies these values to every `go-site/v1` process:

| Variable | Contract |
| --- | --- |
| `APP_ENV` | `production` on a production deployment |
| `HOST` | `127.0.0.1` for the primary HTTP listener |
| `PORT` | Stable automatically assigned primary HTTP port |
| `LISTEN_ADDR` | `127.0.0.1:<PORT>`; preferred single listener setting |
| `PUBLIC_URL` | External HTTPS URL including any configured base path |
| `BASE_PATH` | `/` or the component's public path |
| `DATA_DIR` | Durable, service-owned application state |
| `CONFIG_DIR` | Read-only operator-managed configuration |
| `CACHE_DIR` | Writable, disposable application cache |
| `TRUST_PROXY` | `loopback`; trust proxy headers only from loopback peers |
| `COOKIE_SECURE` | `true` when Basaltwater owns the HTTPS public endpoint |

Applications should prefer `LISTEN_ADDR`, use `DATA_DIR` for durable writes,
and treat an absent optional config file as a normal state when that feature is
optional. Existing application-specific variables may remain as compatibility
overrides during migration, but the generic names become the documented
production contract.

`TRUST_PROXY=loopback` is deliberately not a broad boolean. Apps must reject
forwarded headers from non-loopback peers, while Nginx must overwrite
`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `Host` rather than
passing client-supplied values through unchanged.

Basaltwater writes an optional mode-`0600` environment file outside the release
tree. Secret references and required-secret preflights remain owned by
[Deploy secrets](DEPLOY_SECRETS.md); convention inference never commits or
copies secret values from the repository.

## HTTP, proxy, and health contract

- The application serves plain HTTP on `LISTEN_ADDR`.
- Nginx owns certificates, HTTPS policy, public compression, and routing.
- The application does not start autocert, bind public port 80/443, or expose a
  second TLS listener in a Basaltwater deployment.
- Graceful SIGTERM shutdown is required so activation and rollback do not
  corrupt state.
- `GET /healthz` is a cheap liveness check and must not expose sensitive data.
- `GET /readyz` checks dependencies required to serve normal requests. It
  returns 2xx only when ready and 503 when unavailable.
- Health endpoints require no authentication, do not redirect, and are queried
  directly over loopback. Deployment success is gated on `/readyz`.

Projects may keep richer project-specific health pages. The conventional
endpoints should be stable aliases with a minimal response. A service mounted
under `/api` still exposes `/healthz` and `/readyz` on its direct listener;
public routing need not expose those paths.

## Managed directories and backup policy

Each service receives separate directories under its existing shared root:

- `data/`: durable state, owned by the service user;
- `config/`: operator-managed, read-only to the service;
- `cache/`: service-writable and safe to delete; and
- `backups/`: root-managed deployment backups, unreadable by the service.

The release remains read-only to the service. Systemd grants writes only to
`DATA_DIR` and `CACHE_DIR` unless an explicit capability adds another path.

For `go-site/v1`:

- Files in `DATA_DIR` with a valid SQLite header are backed up through SQLite's
  online backup API before release replacement. WAL and journal sidecars are
  not copied independently.
- Retention comes from a host deployment policy, with a conservative default
  such as 14 successful deployment backups.
- Non-SQLite files are inventoried and reported as unprotected until the
  component selects a supported file/snapshot backup policy.
- File-set backups must state whether a live copy is safe, requires service
  quiescence, or is supplied by a filesystem/volume snapshot.
- Backup creation is not considered recovery until restore verification exists;
  that remains part of the P2 recovery roadmap.

This gives goclick and the bluehexagons database automatic online backups while
making Antistatic's JSONL files and bluehexagons uploads explicit instead of
pretending SQLite handling protects them.

## Additional listener capabilities

The primary convention owns exactly one loopback TCP HTTP listener. Any other
socket is declared with:

- protocol (`tcp` or `udp`);
- bind scope (`loopback`, private network, or public);
- stable or fixed port policy;
- application argument/environment mapping;
- firewall intent;
- proxy/no-proxy behavior; and
- a verification probe where possible.

Antistatic's STUN responder is the first required extension: public UDP 3478,
mapped to its `-stun-host`/`-stun-port` settings and verified with a STUN probe.
It must not be smuggled into the HTTP `port` field or opened merely because the
binary supports it.

## Resolution, preview, and drift

Add a read-only command such as:

```text
basaltw manifest explain /path/to/repository
```

It should print or emit JSON containing:

- selected convention and version;
- discovered but inactive components;
- entry point and operator tools;
- build and verification commands;
- required target runtimes;
- resolved listener, domain, and route intent;
- managed directories and backup coverage;
- health path;
- secret/config requirements that remain unresolved; and
- reasons inference was refused.

Deployment metadata records the resolved manifest, convention version, source
commit, and a digest. A subsequent deployment shows a diff when resolution
changes. The meaning of `go-site/v1` must not drift incompatibly; changed
semantics require `go-site/v2` or an explicit migration.

## Project migration plans

### goclick

Target state: zero repository deployment manifest.

1. Add standard-variable fallbacks: `LISTEN_ADDR`, `DATA_DIR`,
   `COOKIE_SECURE`, and `TRUST_PROXY=loopback`. Keep `GOCLICK_*` overrides for
   local and transition compatibility.
2. Add minimal `/healthz` and database-aware `/readyz` aliases. Keep `/up`
   during the transition.
3. Let convention build `cmd/server` and the sibling `cmd/migrate-rails`
   operator tool.
4. Verify the resolved convention reproduces the current loopback listener,
   service user, SQLite location, health gate, and 14-backup policy.
5. Remove `basaltwater.json` only after an explicit-versus-inferred parity test passes.

The Rails importer is never a deployment hook. It is run once, under the
goclick service identity, while goclick is stopped and against the copied Rails
primary SQLite database.

### antistatic-server

Target state: zero config for the primary HTTP service plus small declarations
for state backup, admin secrets, and optional STUN.

1. Add standard `LISTEN_ADDR`, `DATA_DIR`, and `TRUST_PROXY=loopback` support;
   retain CLI flags for standalone/manual use.
2. Add `/healthz` and `/readyz` aliases while preserving the rich `/health`
   and `/health.html` views.
3. Run behind Nginx without application TLS/autocert flags.
4. Use the bundled Antistatic game profile unless an operator config file in
   `CONFIG_DIR` is selected.
5. Map optional admin credentials through deploy-secret references.
6. Declare JSONL backup policy. A stopped-service copy is consistent; the
   application already tolerates an incomplete final line from a live copy,
   but encrypted/access-controlled storage is still required.
7. Add the public UDP STUN listener only through the explicit listener
   capability and verify it separately.
8. Ensure its protocol `package.json` does not cause Node installation unless a
   selected build verification step needs it.
9. Inventory any existing report data before the VM move. Stop the old service
   or take a consistent filesystem snapshot, transfer the JSONL set with its
   `0700` directory and `0600` file protections, and compare per-collection
   record counts after startup. Re-provision admin secrets through the new
   secret path rather than embedding them in the data copy.

### bluehexagons

Target state: composed `node-static/v1` site with an explicitly selected nested
`go-site/v1` backend when that backend is ready.

1. Keep production static-only during development. Discovery may report
   `server`, but it must remain inactive.
2. Adapt the backend to `DATA_DIR`, `CONFIG_DIR`, `PUBLIC_URL`,
   `COOKIE_SECURE`, and `TRUST_PROXY=loopback`; retain current env names as
   compatibility overrides.
3. Add direct `/healthz` and database-aware `/readyz` endpoints independent of
   the public `/api` prefix.
4. Put both `bluehexagons.db` and uploaded shop assets under documented managed
   state paths. SQLite gets online backups; uploads require a file/snapshot
   backup policy.
5. Resolve Stripe and bootstrap-admin settings through the deploy-secrets
   design before enabling payments or account creation.
6. Enable the backend by adding the compact `source: "server", path: "/api"`
   convention component. Do not infer it from the nested `go.mod`.
7. Remove the obsolete Basaltwater-specific custom systemd template once the
   generated-unit path is authoritative. The manual Caddy/systemd examples may
   remain only if they are clearly documented as a separate unsupported path.

## Fresh production VM and Rails cutover

The next server should be provisioned without Ruby, Bundler, Rails, or the
rails_test application. The cutover sequence is:

1. Land and test `go-site/v1`, convention preview, and goclick's standard
   runtime contract before provisioning production.
2. Rehearse a complete import from a copy of the Rails primary SQLite database
   into an empty goclick database. Record source checksum, row counts, conflict
   behavior, representative account/password checks, and elapsed time.
3. Provision the fresh VM with only the runtimes selected by active components.
   Deploy goclick, Antistatic, and the static bluehexagons site; leave the nested
   bluehexagons backend inactive.
4. Restore any retained Antistatic report data through its declared file or
   snapshot policy, verify ownership/modes and health counts, and verify its
   HTTP and optional STUN endpoints before moving traffic.
5. Freeze writes to rails_test, create a SQLite-consistent source backup, copy
   it to a mode-`0600` staging path on the new VM, and verify its checksum.
6. Stop goclick, preserve any target database, run `migrate-rails` as the
   goclick service user, then restart and require `/readyz` success.
7. Verify preserved IDs, bcrypt password login, counters, timestamps, custom
   archived entities, and expected forced reauthentication. Refresh-token
   digests are not migrated.
8. Switch traffic only after verification. Do not allow both Rails and goclick
   to accept writes; rollback after new writes requires explicit data
   reconciliation rather than a DNS flip.
9. Retain encrypted source and pre-import backups according to the recovery
   policy. Keep the old VM offline/read-only for a bounded rollback window,
   then decommission it.

## Ruby and Rails retirement from Basaltwater

Removal happens after the goclick migration is accepted and its rollback window
closes. It is a deliberate breaking cleanup, not part of the import operation.

Remove:

- `--ruby`, `install_ruby`, Ruby update timers, and saved setup fields;
- Rails project detection and project-root heuristics;
- Rails build, asset, migration, seed, persistent-state, and service branches;
- Rails-specific systemd generation and service naming;
- interactive/setup defaults that select Ruby;
- CI/CD assumptions that only Rails needs a reverse proxy;
- Rails and Ruby tests, service scripts, and user documentation; and
- rails_test examples from workspace/deployment fixtures.

Before deleting compatibility parsing, scan saved configurations. A saved
`--ruby` or rails_test deployment must produce an actionable unsupported-config
error or an explicit one-time migration, never be silently ignored. The fresh
production VM should generate new saved state without either field.

Legacy service cleanup may remain as a narrowly named migration utility for one
release if existing machines still need it. It must not retain the ability to
build or deploy Rails applications.

## Delivery sequence

### Phase 1: Resolver and contract tests

- Implement a versioned convention resolver that returns the existing
  `Manifest` model.
- Add root-entry ambiguity checks and inactive nested-module discovery.
- Add `manifest explain` text/JSON output and resolved-manifest snapshots.
- Add fixtures representing all three repositories.

### Phase 2: Go build and runtime behavior

- Select Go versions per component module.
- Run module tests, build the server, and build sibling operator commands.
- Supply standard environment variables and managed directory layout.
- Add optional external env-file wiring without weakening systemd hardening.

### Phase 3: Health, backup, and listener capabilities

- Gate activation on `/readyz` and retain rollback behavior.
- Discover and online-backup SQLite databases under `DATA_DIR`.
- Inventory unprotected non-SQLite state.
- Add explicit additional-listener modeling, beginning with UDP STUN.

### Phase 4: Project adaptations and parity deploys

- Adapt goclick and remove its manifest after parity testing.
- Adapt Antistatic and test HTTP-only, admin/state, and STUN variants.
- Keep bluehexagons static-only, then test its backend as an inactive candidate
  and an explicitly selected composed component.

### Phase 5: Fresh VM cutover and retirement

- Provision the fresh production VM and complete the rehearsed Rails import.
- Close the rollback window with documented verification evidence.
- Remove Ruby/Rails deployment support and its saved-config surface.

## Acceptance criteria

- Goclick deploys from a fresh checkout with no `basaltwater.json`, receives a stable
  loopback port and managed data directory, passes database readiness, and
  creates restorable SQLite backups.
- `cmd/migrate-rails` is available as an operator tool but is never executed by
  deploy or health logic.
- Antistatic deploys its primary HTTP service without installing Node merely
  because protocol tooling has a package manifest.
- Antistatic STUN is closed by default and opens only from an explicit reviewed
  listener declaration.
- Bluehexagons' nested backend is reported but remains absent from production
  until explicitly selected.
- A composed bluehexagons deployment routes `/` to the static site and `/api`
  to the backend without duplicate routes or fixed-port coupling.
- Every convention deployment can show and persist its fully resolved manifest
  and convention version.
- Unknown or ambiguous layouts fail with actionable diagnostics.
- The fresh VM contains no Ruby/Rails runtime or rails_test deployment.
- Ruby/Rails removal does not silently reinterpret existing saved configs.

## Remaining design dependencies

- Generic deploy-secret references and required-secret preflight are owned by
  [Deploy secrets](DEPLOY_SECRETS.md).
- Off-host file/snapshot backup and restore verification are owned by the P2
  recovery roadmap.
- CI/CD must consume the same resolved convention and artifact metadata defined
  here; see [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md).
- Exact additional-listener firewall application must follow Basaltwater'
  reviewed network policy rather than issue ad-hoc firewall commands from the
  deployment engine.
