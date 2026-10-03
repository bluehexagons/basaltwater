# T3 Code Agent VM Improvement Plan

Status: active qualification; phases 1 through 3 and the phase 4 restart and
durable-readiness boundaries are code-complete. The T3 v0.0.45 compatibility
and phase 5 skill-delivery slice landed on 2026-10-02. Post-setup checks on an
active stable host and private-gateway native preview passed on 2026-10-03.
Disposable-VM setup coverage and the remaining client boundaries are pending.
Stable T3 releases remain the deployment target. Forward compatibility for
orchestration V2 was checked against an isolated
v0.0.46-nightly.20261003.2623 artifact on 2026-10-03; stable was v0.0.45.

## Objective

Make Basaltwater-managed T3 Code VMs easier to operate during long agent
sessions without turning the base profile into an implicit collection of every
runtime and automation tool. Preserve the existing user-scoped service,
credential, repository, HTTPS, and pairing boundaries.

## Evidence and design decisions

A representative active `agent_code_vm` passed both the T3 Code and Playwright
doctor checks while operating with 1.8 GiB of guest memory, 1 GiB of used swap,
and a T3 service memory peak above 3.5 GiB. Its 32 GiB root filesystem was 73%
used. The largest rebuildable or bounded areas included a 3.4 GiB npm cache,
1.3 GiB of Playwright browser data, and more than 600 MiB of T3 logs.

The T3 client also supplied collaborative preview automation in the same
session. A second VM-local Playwright runtime is still useful for SSH-only
sessions, standalone Codex or OpenCode, and reproducible headless checks, but
it does not need to be installed on every T3-focused VM.

These observations lead to four rules:

1. Service readiness and operating headroom are separate diagnostic results.
2. T3-native preview is the preferred interactive path; Playwright remains an
   explicit `--browser-automation playwright` fallback.
3. Automated cleanup may touch only allowlisted, rebuildable, or clearly
   rotated data. Vendor-managed active versions and agent session state are
   not generic caches.
4. Concurrent tasks need repository and port isolation without overwriting a
   dirty checkout.

## Delivery phases

### Phase 1: profile and diagnostic baseline

Completed in `6c050dc`.

- Remove Playwright from the implicit `agent_code_vm` defaults while retaining
  the existing explicit browser-automation flag.
- Add a stable `agent doctor --capability host` text and JSON result covering
  memory, swap, disk, bounded agent storage, T3 cgroup pressure, maintenance
  timers, and pending reboots.
- Keep advisory capacity issues as warnings; fail only for critical filesystem
  pressure or a recorded maintenance failure.
- Extend the managed T3 skill to choose native preview when available and the
  explicit Playwright fallback for SSH-only sessions.

### Phase 2: bounded storage lifecycle

Implemented with conservative ownership boundaries. npm `_npx` workspaces and
numbered T3 log rotations are now bounded by the user maintenance job.
Playwright already records every consuming installation in its shared `.links`
registry and performs reference-aware garbage collection; Basaltwater will not
add a competing directory-deletion mechanism that could remove another
client's browser. Codex standalone releases remain diagnostic-only vendor
rollback state.

- Bound stale npm `npx` workspaces independently of npm's content cache.
- Prune only rotated T3 provider and trace logs, retaining current log files
  and recent diagnostic history.
- Preserve Playwright's reference-aware shared-browser lifecycle and verify its
  garbage collection during pinned version upgrades.
- Report extra Codex standalone releases without deleting vendor-managed
  rollback state automatically.
- Add dry-run and real-VM coverage for every cleanup boundary.

### Phase 3: task isolation and operator evidence

Implemented with a local managed-worktree lifecycle, redacted JSON support
snapshot, and focused workspace, deployment-smoke, and VM-triage skills. Port
allocation stays with the existing `basaltwater-web preview start` and `forward add
--listen auto` paths so the VM has one source of truth for loopback processes,
HTTPS listeners, UFW policy, and cleanup.

- Add managed per-task Git worktree creation, listing, status, and explicit
  cleanup with safe repository and branch validation.
- Allocate loopback preview ports without exposing them; use the existing
  HTTPS gateway when a user requests a shared preview.
- Add a redacted support-bundle command composed from the stable doctor
  results, versions, service state, and bounded logs.
- Install focused workspace, deployment-smoke, and VM-triage skills alongside
  the existing T3 and HTTPS-gateway skills.

### Phase 4: disruptive maintenance and version policy

- Implemented a private 1–72 hour agent-host maintenance hold and taught the
  existing automatic-restart policy to defer for active agent, build, Git,
  managed-worktree, and terminal-multiplexer workloads. Only category names
  are recorded; command lines and repository paths are never recorded.
- The managed T3 workflow now records the existing composite T3 and host
  readiness audit after a reboot or deliberate T3 update. Deliberate terminal
  agent updates record a tools-and-host audit automatically and include T3
  when installed. The private record carries a boot ID so prior-boot evidence
  fails closed. An automatic post-boot service remains gated on a disposable-VM
  lifecycle test; until then, the operator explicitly records the post-reboot
  audit.
- Finish desired-versus-observed version channels and artifact-level rollback
  for agent tools only where upstream distribution contracts support them.
  Terminal agents retain the deliberate update, verified executable backup,
  and automatic rollback path already implemented. T3 retains its upstream
  explicit service updater rather than adding a competing version manager.

The phase 4 restart behavior reuses the existing restart job and forced
deadline rather than introducing a parallel scheduler. Real-VM validation is
still required before the phase is considered complete.

### Phase 5: current T3 features and agent capability delivery

Delivered on 2026-10-02:

- Verified T3 v0.0.45 against the v0.0.44 service, pairing-scope, runtime-layout,
  and persistence contracts. No T3 database migration is required for that
  upgrade. Standalone native-addon checks use the archive's embedded Node;
  host npm is reserved for older npm-backed runtime repairs (`20a68fa`).
- Added per-thread configuration reload, deliberate bulk provider update,
  native PR linking, and preserved scratch-folder guidance. Host inventory
  counts projectless scratch files without cleaning user data (`c42432a`).
- Installed the capability-selected managed catalog for Claude Code in
  `~/.claude/skills`, alongside the Codex/OpenCode shared catalog. Complete
  bundles now include supporting files with tracked refresh/retirement,
  personal-file preservation, and resource readiness checks (`9e83cd1`).
  Setup refreshes guidance after provider configuration copying, which can
  otherwise seed older Claude skills over the current catalog.
- Updated collaborative recording guidance for environment-local transfer
  and documented when native environment-port navigation is appropriate.
  The pinned upstream resolver remains a hostname/port rewrite; public relays
  require the future authenticated preview gateway. Retain Basaltwater's
  existing HTTPS resolver for remote VM loopback services.

Verification evidence: the published standalone archive passed an isolated
native-addon probe, server startup/database initialization, administrative
pairing and bootstrap-credential revocation. The agent/Godot/wheel regression
suite passed 736 tests after bundle integration, and the wheel build/install
smoke verified the shipped T3 update reference. These are source/artifact
checks, not a deployed-host launcher update or full connected-client test.
The initial headless session had no automation host. The subsequent post-setup
check below exercised private-gateway rendering and recording transfer;
native environment-port rendering remains unexercised.

Remaining qualification: rerun a saved Debian VM setup with Claude-only and mixed
providers, restart the affected agent session to discover refreshed skills,
and exercise direct local/private environment-port and relay preview boundaries
with an attached desktop client. Evaluate Claude Playwright registration as a
separate capability change when a concrete standalone Claude browser workflow
needs it; current registration still supports Codex and OpenCode only.

### Stable host post-setup qualification

Checked on 2026-10-03 after the operator reran setup:

- The installed and running upstream user service selected stable v0.0.45
  with service-state protocol 3 and the standalone executable. It was active
  and boot-enabled, with the Basaltwater output-filter drop-in present.
- T3 and host doctor checks passed without warnings; browser and development
  capabilities were healthy as well. The managed skill entrypoint and update
  and future-migration references were installed. A fresh T3/host readiness
  record was saved on the VM.
- The VM reported about 2.5 GiB available RAM, idle swap activity, 49% disk
  use, and about 29 GiB free. Recurring maintenance reported successful runs
  and no reboot was pending. These are point-in-time operating readings.
- A live eight-scope administrative pairing request passed. Its temporary
  link and bootstrap session were revoked, with no credential output.
- The managed T3 HTTPS resolver and doctor passed. Connected-client preview
  navigation and rendering used the existing private gateway with certificate
  verification intact. Snapshot, pointer focus, verified text input/clear,
  and recording transfer passed. The 25,058-byte VM-local recording was
  readable and had an MP4 header. No service restart or route change was needed.
- An unpaired browser's WebSocket authentication failures were kept separate
  from readiness. Clerk cloud-account requests on the private origin failed
  the production-key domain check; native pairing and preview interaction
  passed. Added operator/agent guidance for that distinction. Full browser
  enrollment and cloud-account sign-in were not exercised.

No Basaltwater runtime defect was found in these checks. Stable remains the
deployment target; the nightly evidence below is advance compatibility work.

Android implementation is explicitly deferred. Potential Android and SSH-hosted
iOS workflows, prerequisites, ownership, and rollout are in the
[mobile agent support proposal](MOBILE_AGENT_SUPPORT.md); no device setup or
agent-device access is enabled by these improvements.

### Forward compatibility for the next stable release

Reviewed the isolated nightly `v0.0.46-nightly.20261003.2623` at
`fed41fa88bb27cb4325cb208d571393850bc63c2` on 2026-10-03:

- The service-state protocol remains 3, the standalone layout is unchanged,
  and the existing eight-scope administrative pairing flow passes against
  the published Linux x64 archive. Its SHA-256 was verified before execution.
- The application protocol changes from 1 to 2. The matching client/server
  update is required; service readiness alone cannot verify that connection.
- Upstream copies `state.sqlite` to `statev2.sqlite` once and owns migrations
  through 56, including reconciliation for earlier V2 preview migration IDs.
  A temporary v0.0.45 database passed pinned-thread and full transcript import,
  source-database preservation, pairing, and a subsequent V2 restart.
- Shipped agent references now cover a private recovery copy, disk headroom,
  scheduled interruption, automatic import, and the fresh provider session
  used to continue migrated threads. Upgrades are forward-only. No downgrade
  flow, competing database importer, or migration-ID rewrite is added.
- The managed skill describes history retrieval for portable handoffs and
  ACP Registry provider ownership. Registry agents remain opt-in and do not
  implicitly expand Basaltwater's managed terminal-agent or policy catalog.

Validation: 88 focused runtime, pairing, readiness, skill-bundle, maintenance,
and wheel tests passed. The isolated wheel build/install smoke verifies that
the new migration reference ships with the managed skill; artifact validation
now requires that reference. No live service was updated during these checks.

Next qualification: when V2 reaches stable, recheck the published stable
migration contract and exercise the forward service update on a disposable
Debian VM with a matching stable V2 client. Verify representative long/archived
threads and attachments, and confirm preview and provider continuation.
Preview-to-current migration numbering is reviewed in source, not exercised
by the v0.0.45 artifact smoke. Setup remains on npm's `t3@latest` stable
channel; nightly checks are advance compatibility testing only. Evaluate native T3
scheduled tasks against maintenance holds and recorded readiness before
recommending unattended operation; no additional scheduler is introduced.

## Acceptance criteria

- A new `agent_code_vm` does not download Chromium unless the operator supplies
  `--browser-automation playwright`.
- Existing saved setups that explicitly selected Playwright retain it.
- Host diagnostics never read or emit credential contents, repository file
  contents, prompts, or agent conversation/session contents.
- Storage cleanup rejects symlinks and paths outside the configured user's
  home, preserves active vendor versions, and has useful dry-run output.
- Workspace cleanup cannot remove the main checkout, a dirty worktree, or an
  unmerged branch without a separate explicit destructive authorization.
- Unit tests pass and each mutation-heavy phase receives a real Debian VM smoke
  test before being considered complete.
