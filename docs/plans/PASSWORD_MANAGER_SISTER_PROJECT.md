# Password manager sister project

Status: proposed implementation plan; repository name and schedule unselected.
Basaltwater is the deployment platform. The password manager will have its own
repository and release cycle. This plan does not create or deploy the project.

## Product and ownership

Build a small open-source personal password manager with offline access,
encrypted synchronization, and explicit browser filling. Start with one person
using several devices; add organizations and sharing only as separate projects.

Proposed baseline: a TypeScript browser-extension client and a Go server with
SQLite. No required desktop application, external database, Redis, or container
runtime. First evaluate an existing vault format and maintained library before
committing to a custom format. Do not implement both paths in parallel.

The sister project owns vault security, clients, synchronization, recovery,
application migrations, packaging, and vulnerability response. Basaltwater
owns provisioning and deployment. It must never need the master password,
recovery key, or decrypted vault to operate the service.

Select the public name, repository slug, extension identity, and license before
distributing persistent installations. Proposed license: AGPL-3.0, subject to
dependency compatibility. The name is deliberately left open.

## Initial scope

- One vault owner per server installation, with multiple enrolled devices.
- Login entries: title, username, password, approved origins, and notes.
- Secure notes, local search, random password generation, and explicit CRUD.
- Encrypted local persistence, offline edits, and synchronization.
- Encrypted export/import, device revocation, and documented recovery.
- Chromium desktop extension: full extension page for management, toolbar
  popup for selection and filling. Qualify Linux first.
- User-triggered filling of ordinary top-level HTTPS login forms; no automatic
  submission. Explicit manual copy is the fallback for unsupported forms.
- Tested deployment, backup, restore, and upgrade through Basaltwater.

Defer Firefox qualification, Safari, native mobile apps, desktop GUI, sharing,
attachments, TOTP and passkey storage, SSO, Bitwarden compatibility, automatic
capture, inline suggestions, cross-origin iframe filling, and password-change
automation. Mobile autofill and browser passkey-provider support are separate
integration projects.

## Architecture

```text
Extension-owned UI
  -> vault core: unlock, search, edits, encrypted local persistence
  -> sync client -- HTTPS --> Go service -- SQLite

User selects a login
  -> trusted extension coordinator
  -> content script in the selected document
  -> selected login fields

Basaltwater -> service account, systemd, Nginx/TLS, releases, backups
```

The server stores opaque encrypted snapshots, revision metadata, and device
authorization records. It never renders an unlocking web client. Bundle all
client code and cryptographic dependencies with the extension; no remote
scripts, analytics, or favicon-fetching service. Server visibility still
includes IPs, request times, device records, payload sizes, and revision counts.

Separate client vault operations, synchronization, UI, and browser messaging.
Use a maintained browser-compatible cryptographic/format library, including
locally bundled WebAssembly if necessary. Do not implement primitives.

Persist ciphertext and pending encrypted edits transactionally in IndexedDB.
Keep plaintext out of logs, browser sync storage, crash reports, and persistent
UI state. Choose one authoritative unlocked context. Do not assume a Manifest
V3 service worker stays alive: if the in-memory owner disappears, lock and
require another unlock instead of persisting unwrapped keys to survive restart.

Define explicit lock, inactivity, browser restart, and extension-page closure
behavior. Check deadlines on requests, not only with timers. Do not promise
workstation-lock detection until qualified. JavaScript cannot guarantee complete
memory erasure; describe reference cleanup as best effort.

## M0: design and feasibility

Deliver architecture decision records, a threat model, and disposable prototypes
using synthetic credentials. Resolve these before implementing the product:

1. **Format:** evaluate KDBX 4.1 through a maintained library. Test browser
   performance, independent-client round trips, unknown-field preservation,
   corruption handling, dependency maintenance, and license. Follow the format
   exactly if selected. A published format does not certify an implementation.
2. **Recovery:** specify recovery for that format. Do not assume KDBX provides
   an alternate recovery-key slot. If recovery needs a separate artifact,
   define its contents, freshness, and effect on interoperability.
3. **Custom fallback:** select a custom format only for a documented unmet
   requirement. Specify and review it before implementation; budget additional
   protocol-review work. Avoid silently expanding the product to support both.
4. **Browser feasibility:** measure unlock latency and bounded KDF memory on
   minimum hardware; prove CSP compatibility, worker suspension behavior, and
   explicit filling with narrow permissions.
5. **Authentication:** specify independent server-access and vault-unlock flows,
   device enrollment, and recovery onto a fresh browser profile.

Threat model: stolen server database/backups, malicious synchronization server,
hostile web pages, stolen locked browser profile, replayed requests, malformed
vaults, and interrupted writes. Document limits for compromised unlocked
endpoints, malicious extension updates, weak master passwords, and credentials
already released to a destination page.

Exit criteria: one selected format/library, written recovery and authentication
flows, measured browser feasibility, and no unresolved key-lifecycle assumptions.
If no suitable library or review path exists, reconsider integration with an
existing manager before proceeding.

## M1: local vault and recovery

Implement the vault core and extension management page before networking:
create/unlock/lock, CRUD, search, password generation, encrypted export/import,
and recovery. Commit encrypted state before marking edits saved; interrupted
or full-storage writes must preserve the previous readable version.

For a custom format, the starting design is a random vault key wrapped by a
password-derived key, Argon2id, and established authenticated encryption.
Specify salts/nonces, domain separation, authenticated identity/version fields,
KDF minimums and resource ceilings, serialization, unsupported-version handling,
and recovery wrapping. This is a design to review, not a complete protocol.
For KDBX, use its specified construction instead of mixing in these mechanisms.

Distinguish master-password changes, vault-key rotation, and replacement of
recovery material. Old snapshots may remain decryptable with old credentials;
changing a password cannot revoke retained copies. Losing both the master
password and recovery material must not have an administrator bypass.

Exit criteria:

- Restore an export and exercise recovery on a clean profile without a server.
- Wrong credentials, altered ciphertext, malformed data, excessive KDF settings,
  and unsupported versions fail without destroying existing data.
- Crash and storage-exhaustion tests preserve committed edits.
- Independent fixtures/test vectors and parser fuzzing supplement round trips.

## M2: authentication and encrypted synchronization

Build one Go HTTP process with SQLite transactions. Keep encrypted snapshots
in SQLite initially, so a consistent database backup includes application state.
Set payload, request-rate, history, and disk-use limits.

Proposed initial authentication: local owner bootstrap and independent random
per-device bearer credentials over TLS. Store only token digests on the server,
support revocation, and redact authorization headers. Enrollment codes are
short-lived, single-use, and consumed atomically. Additional-device enrollment
requires an authorized device; lost-device server-access recovery uses a local
operator procedure. Neither flow decrypts the vault. Never use the master
password or its derived encryption key as an API token.

Decide whether device credentials persist across browser restarts. A stolen
authorized profile may allow ciphertext download or destructive synchronization
even with a locked vault. Document that tradeoff, provide revocation and
independent backups, and do not describe bearer authentication as MFA.

Define a small versioned API for enrollment, device management, fetching the
current snapshot, conditional replacement, and bounded history retrieval.
Authorize every route; disable open registration and public administrative
HTTP endpoints. Origin checks supplement authentication; they do not replace it.

Synchronization requirements:

- Uploads include an expected base revision and retry identifier. Atomically
  commit data and revision; stale writes return conflict.
- Persist an encrypted pending edit before upload. Retries must not duplicate
  commits or silently overwrite another device's work.
- Preserve both conflicting versions and reconcile explicitly in the unlocked
  client. Whole-vault conflict copies are acceptable initially; silent last-write
  wins is not. Show unsynchronized state clearly.
- Authenticate vault/revision context through the selected format or a reviewed
  envelope. Server-generated revision numbers alone do not prove freshness.
- Retain a last-seen client checkpoint. A fresh client without an independent
  checkpoint cannot prove a malicious server supplied the newest snapshot;
  complete fork detection is outside the first release.
- Document history retention, deletion, and the persistence of deleted records
  in backups. Server history is not an independent backup.

Exit criteria: two profiles edit offline, reconnect, reconcile, and converge
without losing either edit. Test enrollment races, revocation, unauthorized
reads/writes, retries, stale snapshots, network failure, full disk, and process
termination during writes. Inspect server state/logs for plaintext test secrets.

## M3: browser filling

Use toolbar selection, temporary access such as `activeTab`, and script
injection. Request optional access to the configured sync-server origin.
Review the actual manifest permissions; initial explicit filling should not
require persistent access to every website.

Match entries against an explicit HTTPS origin allowlist, including scheme and
effective port, using a URL parser. Do not infer trust from substrings, titles,
form labels, or a shared registrable domain. Additional subdomains require
approval. Display the actual destination clearly in extension-owned UI,
including an unambiguous representation of internationalized domains.

The coordinator obtains tab/frame/document identity from browser APIs, validates
message senders, and binds the selection to that document. Revalidate immediately
before delivery. Send only the selected credential to the intended document,
never the full vault or master key. Reject changed documents, expired requests,
unexpected frames, HTTP/opaque origins, and arbitrary page requests for secrets.

Start with visible ordinary username/password inputs in top-level pages. Do
not submit automatically or fill hidden fields. Treat DOM data and content
scripts as untrusted. The destination page can read a credential after filling;
the extension cannot prevent that.

Exit criteria: real-browser fixtures cover correct filling, lookalike domains,
subdomains, navigation races, malicious messages, hidden fields, frames, worker
termination, lock deadlines, and unsupported forms. Assert hostile documents
receive no credential, not merely that an input remains empty. Include manual
keyboard and screen-reader checks for unlock/select/fill.

## M4: Basaltwater deployment and recovery

Ship a root `basaltwater.json` with an explicit service component. Use the
documented `binary`, `port: "auto"`, `health`, `runtime_env`, `sqlite_backup`,
and `backup_retention` fields. Set the database under `{{data_dir}}` rather than
hardcoding a host path. Honor the managed loopback listener settings; implement
graceful shutdown, `/healthz`, and database/schema-aware `/readyz` with no vault
data in responses. Record the minimum qualified Basaltwater release.

Use `basaltw` for deployment instructions. The current platform provides
dedicated service users, Nginx/TLS integration, persistent state outside release
directories, health-gated activation, and declared SQLite deployment backups.
Validate against these contracts and track missing behavior as explicit
integration work. A deployment snapshot is not a recurring off-host backup.

Deliver:

- Recurring consistent backups, an off-host destination, retention, and a restore
  runbook covering the database, configuration, and authorization state.
- A fresh-VM restore drill followed by actual client unlock/synchronization.
  Check restored device authorization state and revoke/rotate as needed.
- Schema migration and rollback rules. Never restore stale database state over
  newer writes merely to make a binary rollback succeed. Initial migrations
  should remain readable by the previous supported server where practical.
- Health, free-space, backup-age, and update-failure observations without entry
  titles, URLs, credentials, tokens, or recovery material in diagnostics.
- Pinned releases, verified artifacts, and an urgent security-update procedure
  that accounts for Basaltwater's freshness-delay policy.

Exit criteria: clean deployment, idempotent redeployment, failed-update recovery,
backups under writes, and fresh-VM restoration using synthetic vaults. Verify
removal preserves state unless separately requested. Basaltwater must complete
all operational flows without vault-decryption access.

## M5: reviewed public release

Obtain independent review of the format/library integration, recovery,
authorization, extension boundaries, and release process before recommending
important credentials. Resolve findings and publish scope and limitations;
dependency audits do not certify the product. Seek design feedback in M0/M1
before investing in the complete implementation.

Publish the threat model, privacy statement, support matrix, recovery guide,
security-reporting channel, and update policy. Build extension packages and
server binaries from tagged source with locked dependencies, checksums, license
notices, and an SBOM. Protect release/store accounts with strong authentication;
keep publication credentials inaccessible to pull-request CI.

Distribute through the chosen browser store; unpacked loading is development
only. Test upgrades and define a client/server compatibility window because
store updates and server upgrades are asynchronous. Unsupported combinations
must fail without rewriting vault data. Preserve a documented offline export
and recovery path if the server or store becomes unavailable.

Exit criteria: findings resolved, clean-machine install/update/restore qualified,
and a security-fix release rehearsal completed. Assign continuing ownership for
dependency triage, browser changes, security reports, and recovery regressions.

## Repository scaffold and initial backlog

```text
README.md                 purpose, limits, developer quick start
LICENSE                   selected open-source license
SECURITY.md               reporting and supported versions
AGENTS.md                 contributor rules; synthetic test credentials only
basaltwater.json           explicit server deployment
go.mod
cmd/server/               synchronization server
internal/                 authorization, HTTP, storage, migrations
client/core/              chosen format adapter, vault operations, sync
client/extension/         UI, coordinator, content scripts
tests/fixtures/           synthetic vaults and hostile pages
tests/integration/        sync, browser, and recovery tests
deploy/                   configuration examples and restore instructions
docs/adr/                 format, recovery, authentication, client lifecycle
docs/                     threat model, API/format specs, operator guide
.github/workflows/        checks, release builds, dependency review
```

Keep extension builds separate from server deployment; the VM needs only the
server runtime and applicable server build tools. Do not build a general
application framework or a second provisioning engine inside this repository.

Implementation order: M0 -> M1 -> M2 -> M3 -> M4 -> M5. Write tests and recovery
documentation with each milestone. Suggested first issues:

1. Choose identity/license and scaffold CI with synthetic fixtures.
2. Evaluate KDBX/library/recovery feasibility and record the format decision.
3. Prove browser KDF, unlock, permissions, and worker lifecycle behavior.
4. Specify authentication, enrollment, and both recovery boundaries.
5. Build the local vault/export/recovery vertical slice.
6. Add conditional encrypted sync and conflict preservation.
7. Add document-bound filling and adversarial browser tests.
8. Qualify Basaltwater deployment, backup, upgrade, and fresh-VM restore.
9. Complete independent review and public distribution qualification.

Estimate effort after M0. This is a multi-milestone product with ongoing
maintenance; the largest early uncertainties are library suitability, recovery
correctness, and browser behavior. Team sharing would introduce its own key
distribution, membership, revocation, and recovery design and needs a new plan.

## References

- [KDBX specification](https://keepass.info/help/kb/kdbx.html)
- [Libsodium password hashing](https://libsodium.gitbook.io/doc/password_hashing/default_phf)
- [Libsodium authenticated encryption](https://libsodium.gitbook.io/doc/secret-key_cryptography/aead)
- [Chrome messaging and security](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)
- [Chrome activeTab permission](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab)
- [Chrome worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Native messaging, for a later desktop client](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)
- [Basaltwater deployment contracts](../DEPLOYMENTS.md)
- [Basaltwater deployment recovery](../DEPLOYMENT_SAFETY.md)

These specifications inform the design; they do not certify the implementation.
Recheck browser and deployment contracts when implementation starts.
