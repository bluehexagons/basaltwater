# Game publishing from managed VMs

Status: initial implementation delivered, live qualification open, 2026-10-05.
This plan owns Butler and SteamCMD management through the
existing web panel, VM-local authentication, and manual or unattended uploads.
The companion [release and communications plan](GAME_RELEASE_COMMUNICATIONS.md)
extends it with build promotion, posts, translations, human review, and
publication. Steam default-branch release remains manual on Steamworks.
Existing installation support remains documented in [Godot](../GODOT.md) and
[CachyOS software](../CACHYOS_SOFTWARE.md). Priority: unscheduled; this work
does not displace the roadmap's reliability priorities. Implementation is now
authorized; live account login, uploads, and provider qualification require
operator-selected test destinations. See [the operator guide](../GAME_PUBLISHING.md)
for the delivered capabilities and remaining qualification boundaries.

## Delivered scope and remaining work

The [operator guide](../GAME_PUBLISHING.md) is the current executable contract.
The design below preserves the broader feature direction; it does not claim
that every acceptance item has been qualified.

| Area | Initial implementation | Next boundary |
| --- | --- | --- |
| Tool setup and maintenance | Independent repeatable Debian `--publishing-tool`; legacy Godot selection retained; one shared updater and provider leases across registered owners. | Live Debian reruns/ARM64 checks and separate CachyOS qualification. |
| Authentication and panel | Same-panel Publishing page, bounded native PTY login, HTTPS-only mutations, VM-local sessions, local disconnect and terminal fallback. | Remote native login/prompt and cached-session qualification; bounded online identity checks. |
| Builds and jobs | Completion records, private retained snapshots, fixed upload commands, durable dispatch ledger, cancellation/reconciliation, deterministic panel-owned interval jobs. | Live receipt/schema qualification, richer inventory/progress and sanitized notifications; schedule editing/removal and explicit safe re-upload. |
| Promotion and releases | Exact itch.io channel re-upload, optional observed Steam beta API promotion, manual Steamworks release/rollback records and dependent gates. | Live beta API response qualification; coordinated multi-depot recipes and stronger external-concurrency observations. |
| Writing and translations | Language manifest extension, agent prompt preparation, immutable plain-text drafts, side-by-side English/Spanish reviews, exact human approval and timed exports/handoffs. | Provider rendering, rich assets, terminology/placeholder checks and Steam localization CSV round trips. |
| Posts | Steam/itch.io reviewed exports and operator-confirmed published URLs; automatic submission is not advertised. | Qualified post adapters, remote schedule ownership, external edit detection and additional website/blog/social destinations. |

Current snapshots are retained explicitly (100 maximum, 10,000 files and 30 GiB
per artifact). Status selects up to 200 records per kind, prioritizes actionable
runs/drafts/releases, and includes their project and translation-source references.
History, reviews and deduplication records remain durable. Automatic 30-day record
pruning and removal of successful staging are future policy work, not current
behavior. Only exact existing reviewed text can be reused by an upload schedule.
One-depot Steam recipes create separate app builds and cannot compose a
multi-depot release. Steam's beta API has no conditional update guarantee;
the local lock cannot prevent a concurrent Steamworks change by another actor.

## Implementation review findings

The review identified missing executable contracts for completed artifacts,
review authority, scheduler ownership, interrupted workers, and post delivery.
Resolve these as follows:

- Completion records are atomically written UTF-8 JSON with exactly `version: 1`,
  repository-relative `path`, internal `build_id`, and `digest`. The digest is
  SHA-256 of compact, key-sorted UTF-8 JSON describing the path-sorted file list:
  each entry has `path`, `size`, file `sha256`, and boolean `executable`.
  `basaltw publish complete` produces the record after a successful build.
  Keep it outside the artifact; a changed source fails snapshot verification.
- Authoritative reviews live in private VM control state, outside repository
  drafts. Approval exists only through the authenticated panel action and binds
  the exact project configuration, destination, language, text, source revision,
  timing window, and release gate. Translation/source edits invalidate reviews.
  This enforces the managed workflow, not isolation from unrestricted same-user code.
- The existing panel scheduler owns publishing polling. It uses a separate
  deterministic callback so long agent prompts cannot delay publication polling.
  Only one panel scheduler holds the process lease. Upload supervisors detach
  from the panel, share provider locks with logins, and preserve dispatch intent.
  Provider lock descriptors stay with native processes if a supervisor exits.
- Steam and itch.io post adapters initially advertise reviewed plain-text export
  and human-editor handoff, not automatic delivery. Provider formatting/imports
  require a final human check in the editor. Do not invent a write API or claim
  browser qualification from mocked tests. Additional automated adapters remain
  gated on live qualification.
- Publication times use an explicit UTC instant; the default allowed lateness is
  60 minutes, configurable from 1 minute to 24 hours per reviewed revision.
  Expired jobs are held. Upload success does not satisfy a Steam release gate.
- Bundled release notes must match an approved text body byte for byte. Projects
  remain responsible for declaring/reviewing other public writing in game assets;
  filename detection is not a proof that an arbitrary binary contains no text.
- Translation sources must match the current destination/language configuration.
  Public itch.io uploads and automatic Steam beta promotion recheck bundled
  writing's approval, timing and release gates. Steam uploads may privately stage
  reviewed writing before release; default release review stays manual.
  Failed scheduled preparations are removed before queueing, while retention
  protects active and uncertain work. Steam success receipts identify the requested
  AppID. Status prioritizes recovery and retains referenced sources/projects, so
  completed history cannot hide pending comparisons and recovery controls.


## Direction and decisions

The operator should open the VM's web panel, see whether its publishing tools
are usable, authenticate on that VM, and upload a prepared game build without
copying credentials through the controller, a repository, or an agent prompt.
The workflow must work on an SSH-only VM and be independent of T3 Code and the
game engine.

Confirmed product direction: use the same web panel as other machine features,
include authentication and build uploads, and support unattended or scheduled
uploads, build promotion, and reviewed public communications. This is a section
of the existing panel, with its existing navigation
and sign-in, not a separate management site or desktop application. The local
CLI supports automation and recovery. Other entries below are implementation
defaults rather than separately requested product requirements.

| Decision | First version |
| --- | --- |
| Management interface | A Publishing page within the existing web panel, sharing a backend with a local automation CLI; SSH remains a recovery path. |
| Delivery scope | Tool/authentication management, destination configuration, manual and scheduled uploads, promotion, reviewed posts/translations, publication, and result inspection. |
| Agent use | Agents and deterministic scheduled jobs may upload under configured publishing authority using the VM's local session, without per-run human approval. Login challenges return control to the human through the panel. |
| Release controls | Support explicitly authorized beta promotion and itch.io destination-channel uploads. Steam default/public release and rollback stay manual on Steamworks. |
| Public writing | Agents may draft/translate; every public text revision and translation requires human review. Automation publishes only the exact approved payload. |
| Content destinations | Steam announcements and itch.io posts first; website/blog and social adapters are planned next. |
| Languages | Project declaration in the accepted `basaltwater.json` extension, default English only. Initially test English and Spanish; allow additional language tags with explicit qualification limits. |
| Initial host | Managed Debian x86_64 VM with one non-root publishing owner; Butler-only operation on supported ARM64 installations. |
| Accounts | One active account per provider per publishing owner; many projects/destinations. Multiple simultaneous provider identities are deferred. |
| Installation | Reuse existing installers and add an engine-independent selection; retain the Godot bundle as a convenience. |

No product-direction question remains blocking this plan. Provider behavior
still needs the qualification in slice 1. The first release consumes completed
build artifacts; automated game exports can feed that interface without making
this feature own a build system.

## Repository integration points

Primary integration points are `common/godot_steps.py`,
`common/service_tools/auto_update_godot.py`, `plugins/common.py`,
`common/web_panel_steps.py`, and `common/service_tools/web_panel_*.py`.
The device-pairing service supplies useful interaction patterns, but its T3
pairing contract is not a generic game-publisher authentication protocol.

## Provider facts that constrain the design

References checked on 2026-10-05; qualify the actual installed CLI versions
before implementing adapters.

- Butler supports browser login from a remote terminal, including returning a
  redirect URL to that terminal. Its Linux default credential file is
  `~/.config/itch/butler_creds`. Local logout and provider-side key revocation
  are separate operations. See [Butler authentication](https://itch.io/docs/butler/login.html).
- A Butler push targets `owner/game:channel`. Updating a channel can make the
  new build available to players immediately; a channel named `beta` is not
  inherently private. See [Butler uploads](https://itch.io/docs/butler/pushing.html).
- Valve documents an initial password/Steam Guard login followed by reuse of
  a local login token in `config/config.vdf`. It recommends a dedicated build
  account with the documented application permissions. SteamPipe build VDFs
  describe the app, depots, content, and output. `SetLive` can promote a beta
  branch; default-branch promotion is handled in App Admin. See
  [Uploading to Steam](https://partner.steamgames.com/doc/sdk/uploading).

These flows do not imply a common OAuth/device-code implementation. Provider
website sessions, Steam Web API keys, and T3 pairing sessions do not substitute
for publishing-tool authentication.

## User experience

The dashboard gains a Publishing summary and sidebar entry. Opening either
reads saved observations only; page views never run login, start SteamCMD,
update binaries, contact providers, or upload content. Missing installations
remain discoverable with a link to setup instructions.

| Surface | Information and actions |
| --- | --- |
| Tools | Provider, installed version where safely observable, architecture support, installation/update owner, local account, last check, and relevant documentation. |
| Authentication | Separate local credential presence, last successful online check, and current challenge/expiry state; Sign in, Check connection, Disconnect locally, and provider revocation guidance. |
| Projects | Saved destination names, itch.io project/channel or Steam AppID/DepotIDs, content location, and provider dashboard/build links. |
| Upload review | Exact destination, artifact inventory and digest, version/description, expected visibility effect, and any unavailable checks. |
| Automation | Saved publishing jobs, artifact source, destination, cadence, enabled/paused state, next run, last result, and Run now. |
| Releases and communications | Build promotion, manual Steam default-release handoff, posts, translations, and human review; see the [companion plan](GAME_RELEASE_COMMUNICATIONS.md). |
| Activity | Active operation, progress summary, cancellation, last result, provider build ID when available, and a link to inspect it. |

Prefer fixed official destinations: the [Butler manual](https://itch.io/docs/butler/),
[itch.io dashboard](https://itch.io/dashboard),
[itch.io API-key management](https://itch.io/user/settings/api-keys),
[Steamworks dashboard](https://partner.steamgames.com/), and the upload reference
above. Generate per-project links only from validated identifiers and known
provider origins. Account administration stays on those provider sites.

The ordinary panel may run over HTTP today. Publishing authentication and
mutation controls require the managed HTTPS endpoint or a qualified loopback
SSH-tunnel workflow. An HTTP panel shows information and directs the operator
to the secure route; it must not offer a password form.

### Authentication journey

1. Select a provider and **Sign in**. The backend starts a fixed native login
   command as the publishing owner, with that user's verified home and runtime.
2. Butler presents its provider authorization link and a private input for the
   returned redirect when needed. SteamCMD presents its native password and
   Steam Guard challenges. The UI renders recognized structured prompts rather
   than a general-purpose web terminal.
3. Browser submissions go directly to the VM over the protected route. Secrets
   are passed through the child process's input, never argv, panel URLs,
   prompts sent to an agent, or persistent operation metadata. No controller secret import
   or export is added.
4. Completion records only the provider/account label, outcome, and time.
   Native tools own their reusable credentials. A bounded online check is a
   separate action; local file presence alone is never labelled authenticated.
5. An unknown challenge, timeout, cancelled operation, or service restart ends
   the interactive flow with recovery guidance. SSH can perform native login
   using the same user and installation. No guessed challenge responses.

Authentication output and user responses are ephemeral and excluded from
journals, proxy request logs, audit exports, notifications, and agent history.
Use a 15-minute login deadline, bounded output, a single active login per
provider, and short-lived form/session binding. Reopening a page cannot replay
a secret response. Explicitly disable terminal echo and verify error paths do
not reproduce submitted values. A provider that cannot satisfy these rules
keeps its SSH login fallback until its web adapter is qualified.

### Upload journey

1. Register a destination and choose an already-built artifact on the VM. The
   manager does not build projects, run repository hooks, or infer a production
   destination. Exports and tests remain the project's responsibility.
2. Prepare a private snapshot and inventory of the selected output. Validate
   the complete tree, destination, free space, and provider configuration.
   Show file count, bytes, content digest, version/description, and effects.
3. Submit that prepared operation through the panel or CLI. The reviewed
   snapshot and destination are bound to the operation; changed inputs require
   a fresh preview. The panel uses a review-and-submit action for manual work.
   CLI callers and scheduled jobs prepare and submit within their configured
   authority without per-run human approval for the upload itself. Public text
   supplied with the artifact or provider metadata must already have human
   review bound to that revision; an upload schedule cannot approve new writing.
4. The provider adapter uploads with the existing local session. Missing or
   expired authentication yields **Sign-in required** and a management link;
   an upload worker never waits for a password in an agent run.
5. Report accepted/uploaded, processing, verified, failed, cancelled, or unknown
   as evidence allows. A zero exit status alone is insufficient to claim the
   build is downloadable. Preserve the provider build ID and inspection link
   when obtainable without exposing credentials.

For Steam, generate an upload-only VDF from validated app/depot mappings.
First-version VDF support is a defined subset; reject unsupported fields,
external includes, out-of-root mappings, and nonempty `SetLive`. Do not pass
arbitrary uploaded VDFs or Steam console commands through the panel. Leave
promotion to a separate explicit operation described in the
[release plan](GAME_RELEASE_COMMUNICATIONS.md#promotion-workflow), with the
default/public branch always handed off to a human on Steamworks.

For itch.io, require an explicit channel and show that uploading may replace a
player-visible build. Test with a private project during qualification. Do not
promise Steam-like staging or treat a channel name as a visibility control.

### Unattended and scheduled publishing

Publishing jobs are deterministic tasks managed in **Publishing → Automation**,
with links from the existing Scheduled jobs view. Enabling a job authorizes
uploads for its saved provider, destination, artifact source, and visibility
effects. Changing those settings creates a new configuration revision and
invalidates queued work for the previous revision. No password, generic shell
command, or LLM prompt belongs in a publishing job.

Each job selects an already-built release from a configured local artifact
directory. The producer finishes an immutable release directory and atomically
writes a small completion record containing its relative path, version, and
content digest. The publisher validates that record, confines all paths to the
selected source, and prepares its own snapshot. An incomplete build, missing
completion record, or mismatched digest is not eligible for upload. Define the
versioned completion-record schema and document an example in slice 6. Manual
uploads can prepare a selected directory without that producer integration.

Provide **Run now**, interval schedules, **Pause**, **Resume**, **Edit**, and
**Remove**, plus unattended CLI submission for external build runners. Start
with the panel's existing interval model rather than adding calendar/cron
semantics. Extend the existing scheduling infrastructure with a publishing job
type and executor; preserve agent-task behavior and avoid a competing scheduler
or one system timer per project. Upload execution uses the publishing worker,
not an LLM. The worker owns running uploads independently of browser requests;
scheduled dispatch depends on the panel service being available.

One job may have at most one active or queued run. After downtime, inspect the
latest completed artifact once rather than replaying every missed interval.
Do not overlap native operations for the same provider/account. Skip artifacts
already uploaded successfully to the same destination and configuration;
explicit re-upload remains possible. Jobs record artifact identity, schedule
revision, and dispatch identity so restart recovery cannot silently duplicate
an upload. Each provider result is independent; uploading to both stores is
not one atomic transaction.

Keep the last successful artifact identity and unresolved dispatches per
destination independently of bounded run-history retention. An unresolved
upload blocks automatic resubmission of that artifact through other jobs too;
clearing old logs must not erase duplicate-detection or reconciliation state.

Authentication rejection immediately pauses the affected provider's jobs and
shows **Sign-in required** with a panel link. Successful sign-in offers an
explicit **Resume paused jobs** action. Unknown remote outcomes pause the
affected job until the operator reconciles the provider result. Ordinary
failures follow the existing configurable consecutive-failure limit (default
three), with no tight retry loop; an artifact not yet ready is a reported skip.
Pause prevents future dispatch; cancelling an active run is a separate action.

Send bounded, sanitized failure/authentication notifications through existing
notification channels and keep progress/results in the panel. Automated
itch.io jobs must clearly show their potential player-visible effect when
enabled; automated Steam upload jobs retain the upload-only contract. Promotion
jobs have separate branch authority; post jobs additionally require exact human
approval. These boundaries are defined in the
[release and communications plan](GAME_RELEASE_COMMUNICATIONS.md). Agents may
invoke the CLI within the job or user's standing instructions, and must report
authentication needs without soliciting or reading credential values.

## Runtime, credentials, and authority

Implement one local publishing library with small Butler and SteamCMD adapters.
The panel and CLI share validation, observations, locks, and operation records.
The proposed namespace is `basaltw publish` with `status`, `auth`, `projects`,
`prepare`, `upload`, `jobs`, `runs`, and `cancel` actions. These commands are
implemented; the operator guide documents exact arguments and limits. JSON
output contains sanitized results.
Promotion, release records, and editorial actions extend this namespace when
their slices land; no machine-facing action can grant human text approval.

Run provider processes as the configured non-root publishing owner. Initially
this is the same account used by the non-root panel and coding tools. A separate
process supervisor owns long uploads so closing the browser or restarting
the panel does not terminate them. Reuse existing bounded-process and atomic
state primitives; do not execute uploads through the panel's LLM task runner.

| State | Proposed ownership and lifecycle |
| --- | --- |
| Tool installation | Existing managed paths and installer ownership; extract shared publishing installation/update helpers without creating a second updater. |
| Native credentials | Existing provider-native locations under the publishing user's home; private files/directories, never in Basaltwater controller credentials or project manifests. |
| Optional promotion/post credentials | VM-only publisher API key or separately authenticated provider website session where needed; no controller transfer, manifest secret, or reuse of SteamCMD tokens as website credentials. |
| Project settings | Private VM-local SQLite records under `~/.local/share/basaltwater/publishing/`; project paths and destination IDs, no passwords or tokens. |
| Publishing jobs | Non-secret records in the same database, integrated with the existing scheduler; source, destination, cadence, configuration revision, failure count, and enabled state. |
| Run records | Private durable records in the same database; identity, artifact digest, destination, timestamps, outcome and provider reference. Status reads are bounded; retention pruning is deferred. |
| Prepared content | Private per-run snapshots outside repositories and credential roots; retained while active/uncertain, with explicit cleanup and a bounded retention policy. |
| Interactive state | VM memory and private runtime resources only; discarded on cancellation, expiry, or restart. |

Verify native SteamCMD credential paths and any additional authentication files
against the managed Linux installation; do not assume one documented config
file is the entire local logout inventory. Existing sessions must be discovered
without silently copying or relocating them. Refuse unsafe ownership/symlinks
and explain repairs before starting authentication.

Same-user processes and root can access provider-native sessions. This plan
avoids passing secrets around; it does not isolate them from an unrestricted
agent running as that user. Destination restrictions in jobs constrain the
managed workflow; they do not sandbox native CLI access by that account.
Use provider-side permissions and a separate VM/account for a different trust
boundary. The selected scope permits unattended use of the local session.

Disconnect waits for or explicitly cancels active provider work, clears only
qualified native authentication state, and invalidates cached readiness. It
must not erase build content or the tool installation. Where safe native
disconnect cannot be verified, report that limitation and provide provider
revocation instructions. Local deletion never claims remote revocation.

Setup reruns, refresh, and upgrades preserve native sessions. Removing the
publishing capability disables its jobs, worker, and panel controls while
retaining user data; the rest of the panel remains available. Active uploads
must finish or be explicitly cancelled before removal proceeds;
credential removal is a distinct explicit operation. VM retirement guidance
includes provider-side revocation. Rebuilt VMs authenticate again; no credential
cloning workflow is added. VM snapshots/backups may contain native credentials
and need the same protection as the original VM; support bundles exclude them.

## Validation and operation guarantees

- Validate filesystem/configuration inputs with `lib.validation` and local
  usernames/hosts with `lib.validators`. Add provider-specific validation for
  itch.io destinations, Steam numeric IDs, and constrained VDF mappings.
  Invoke fixed executable paths with argument arrays, not shell strings.
- Accept only an explicitly selected build-output root. Exclude credential
  locations; reject symlinks, special files, traversal, and unsafe hard links
  in the initial snapshot format. Explain unsupported outputs rather than
  silently changing them. Bound enumeration and check disk space before copy.
- Never execute preparation scripts with publishing credentials. Snapshot
  preparation must detect concurrent source changes; verify the inventory
  against the finished snapshot. Repository content cannot authorize uploads.
- Use one exclusive provider lock per publishing owner across login, logout,
  checks that invoke the native client, upload, and managed self-update.
  Scheduled maintenance skips a busy provider and reports deferral. Explicit
  updates report busy rather than terminating work. Manual native invocations
  outside the manager remain an operator coordination responsibility.
- Bound processes and logs. Start with a six-hour upload deadline, configurable
  per operation with a finite limit; cancellation terminates the process group.
  Show when a provider update is part of its native startup. Sanitize provider
  output before rendering or persistence; retain no raw authentication stream.
- Persist operation intent before dispatch and claim each operation once.
  Repeated submissions return the existing operation. After a crash or network
  failure with ambiguous remote effects, record **Unknown; inspect provider**;
  never automatically retry an upload or promise transactional remote rollback.
- Keep authentication observations distinct: missing tool, unsupported host,
  no local session, session present/unverified, last check succeeded, sign-in
  required, busy, and check unavailable. A network outage is not proof of logout.
- Use authenticated same-origin POST actions with expiring anti-CSRF tokens,
  bounded bodies, replay protection, and login throttling. Escape all provider
  text and validate authorization-link origins. Secret-bearing pages use
  no-store caching and no-referrer behavior. No arbitrary command or URL proxy.
- Retain at most 100 completed run records per owner for 30 days by default;
  cap sanitized logs per run. Remove successful staging after result recording;
  failed/unknown staging requires explicit cleanup or an operator-selected
  retention policy. Never purge active artifacts to meet a quota.

## Delivery sequence and acceptance

Each slice gets focused mocked tests and a documentation update. Implementation
is authorized. Live account login and uploads remain separate qualification on
operator-selected destinations; development tests do not mutate provider state.

| Slice | Work | Exit criteria |
| --- | --- | --- |
| 1. Provider qualification | Check supported CLI versions, Linux auth/challenge behavior, session locations, safe disconnect, bounded identity checks, and upload-result evidence. | Record actual commands, supported prompts, sanitized fixtures, and limitations; keep SSH fallback where necessary. |
| 2. Tool ownership and discovery | Extract publishing helpers from Godot ownership; add a repeatable engine-independent `--publishing-tool` selection for Debian, preserving existing bundle behavior. Share update registration and locks. | No duplicate installers/timers; old saved Godot selections reconcile unchanged; dry-run never logs in; ARM64 and package-managed installations report correct limits. |
| 3. Local authentication management | Shared backend, CLI status/login/check/disconnect, private state, process bounds, and update coordination. | Native sessions survive reruns, expired auth is explicit, secrets do not enter argv/logs, and corrupt state fails with recovery guidance. |
| 4. Panel management | Publishing page, fixed links, local/online status distinction, protected interactive forms, and SSH recovery instructions. | Headless VM login works from another device; HTTP views cannot submit secrets; refresh/back/replay and unknown challenges are safe. |
| 5. Prepared uploads | Project registration, artifact snapshot, review/submission, provider adapters, durable runs, cancel, and inspection links. | Preview has no upload side effects; exact reviewed destination/content is sent; no Steam promotion; itch.io effects are visible; duplicate/uncertain runs are not retried. |
| 6. Unattended jobs | Completion-record contract, CLI submission, publishing job type in the existing scheduler, panel schedule controls, deduplication, and notification integration. | Completed artifacts upload without human attendance; missing auth and unknown outcomes pause correctly; restart/missed intervals do not duplicate builds; agent schedules retain existing behavior. |
| 7. Promotion and release records | Separate beta promotion, retained itch.io artifacts, exact BuildID evidence, and default-release handoff. | Automatic paths reject Steam default/public targets; dependent work waits for observed or explicitly operator-confirmed release state. |
| 8. Writing, translation, and review | Manifest language declarations, English/Spanish qualification, drafts, destination previews, immutable human approvals, and provider-editor exports. | Default English only; other language tags accepted; every public text/locale revision reviewed; editing invalidates affected approvals and queued publication. |
| 9. Posts and publication jobs | Steam/itch.io adapter qualification, handoffs, approved-payload delivery, one-off schedules, release gates, and result inspection. | No unreviewed text publishes; manual and automated capabilities are distinguished; uncertain/partial results do not produce duplicate posts. |
| 10. Qualification and guidance | Disposable VM checks, agent instructions, operator docs, maintenance/removal/recovery verification. | Record provider/tool versions and evidence for each supported path; only then mark its specific capabilities delivered. |

Slices 7–9 follow the detailed [release and communications plan](GAME_RELEASE_COMMUNICATIONS.md).
Its website/blog and social delivery section defines subsequent adapter work;
those channels need not block the initial Steam/itch.io release. The public-text
review contract applies from the first upload slice, including supplied public
release metadata and documents; an earlier upload-only delivery must use reviewed
inputs even before the full editorial UI exists.

Proposed setup-option spelling is subject to normal CLI review, but installer
ownership and saved-selection compatibility are required. Setup parsing belongs
in the existing configuration/validation layers; target mutations go through
`remote_setup.py` and the relevant `plugins/*.py` builder. Do not add a second
provisioning route through panel forms.

Tests must use temporary directories and mocked provider/system calls. Cover
wrong users, unsafe paths, injected destinations/VDF fields, challenge echo,
timeouts, stale checks, wrong-origin forms, duplicate dispatch, interruptions,
concurrent maintenance, and authentication failure during upload. Also cover
incomplete artifacts, digest mismatch, unchanged-build skips, edited schedules,
missed intervals, provider lock contention, job pause/resume, restart recovery,
and absence of interactive prompts during unattended execution. Register new
test modules in the appropriate `TEST_SUITE_PATTERNS` domain in `run_tests.py`.
Keep tests focused on these behavioral boundaries, not page wording snapshots.

Live qualification later requires an operator-provided private itch.io test
project and an authorized Steamworks test app/depot plus build account. Verify
login from a remote browser, reuse after reboot, explicit logout/revocation,
small uploads, remote build inspection, cancellation, disk exhaustion, lost
network, update contention, and a scheduled upload while no browser or agent
session is connected. Use non-production destinations; production
publication is not an acceptance test. Missing provider access is recorded as
unqualified coverage, never replaced by mock-only claims.

At delivery, update the CLI reference, Godot/CachyOS ownership guides as
applicable, web panel guide/reference, credentials overview, maintenance,
agent-environment capability discovery, and managed agent guidance. The
capability guidance must distinguish local HTTPS game hosting from storefront
uploads and direct agents to human authentication without requesting secrets.

## Deferred scope

CI credential distribution, multiple provider identities per owner,
cross-VM session copying, fleet account management,
native desktop UI, store-page editing,
pricing/release administration, signing/notarization, DRM integration, and
engine build pipelines are outside the recommended first release. Broader
CachyOS management and non-Linux upload hosts need separate qualification;
their current tool installation support is unaffected by this plan.

Steam default-branch release remains manual by product direction; automatic
default promotion is excluded, not a later implementation milestone.
