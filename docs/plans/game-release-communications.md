# Game releases, posts and translations

Status: VM-local release records, optional Steam beta promotion, reviewed
drafts/translations and provider-editor handoffs are available. Live provider
account behavior and rendering remain unqualified. Automatic post adapters,
rich assets and broader destinations are follow-up work, after the current
[shared build/CI host](cicd-manifest-reuse.md).

Use the [publishing operator guide](../workstations/game-publishing.md) for
tools, authentication, language configuration, uploads, retention and schedules.
The [publishing status](game-publishing.md) summarizes current support; this plan
defines remaining delivery and acceptance.

## Boundaries to preserve

- **Every public-facing text requires human review**, including translations,
  edited posts, build descriptions and supplied release notes. Upload authority
  does not authorize new writing. Agents create drafts, not approvals.
- **Steam default/public release and rollback remain human Steamworks actions.**
  API adapters, scheduled jobs and browser automation must not perform the final
  step. Automatic promotion rejects implicit/default aliases and repository
  `SetLive` values; beta promotion requires a separate explicit grant.
- Builds, promotion, content approval and publication have separate outcomes.
  An upload, draft export or opened editor link does not prove a release is live.
  Unknown submissions require inspection, not blind replay.
- Provider credentials and browser sessions remain private on the VM.
  Upload login does not establish post-writing capability. A beta channel is
  not assumed private; show its audience or report it unknown.
- Keep exact artifact/source/build/content identities and per-destination
  receipts. Missing artifacts require the matching digest, never a silent
  rebuild. A compensating upload is not transactional rollback.
- The managed workflow is not isolation from root or arbitrary same-user code.
  Protect authoritative review state from draft jobs and disclose that boundary.

## Human review contract

Bind approval to the final content revision, destination/project, locale,
author, assets, links, formatting, visibility, publication time/window and release
dependency. Convert destination formatting before review; escape active content.
Resolve templates and final public URLs before approval.

Each translation needs its own human review. A source change makes translations
stale; any relevant payload, asset, destination or timing change invalidates the
affected approval and pending submission. Publishing an explicitly approved
subset is allowed; unreviewed fallback translations are not.

Only the authenticated, CSRF-protected human panel action records approval.
Imported files, CLI/scheduler APIs and agents cannot set approval state; agents
must not click the review action through browser tools. A shared panel login
identifies that account, not a uniquely proven person. Recheck approval under
dispatch/cancellation ownership.

Externally edited text requires review and reconciliation as a new revision.
Do not autosave unreviewed corrections to live posts. Project language
declarations stay in `basaltwater.json`; credentials, approvals and schedules
stay operational state. The default is English only. English/Spanish editorial
checks do not qualify every accepted language or imply game-content localization.

## Delivery order

### 1. Qualify existing handoffs and promotion

Use authorized test apps/projects to verify upload identity, explicit beta
promotion, stale-target refusal and provider readback. Keep Steam default
release as a handoff with exact BuildID, evidence and human completion.
A local lock cannot exclude an external Steamworks change.

Qualify Steam announcement and itch.io project-devlog editor handoffs with
reviewed English/Spanish text, localization and edits. Record operator-reported
completion separately from provider-verified state.

**Acceptance:** selected builds and final rendered text match reviewed inputs;
unknown or changed targets stop dependent work. No public/default automation
runs. Use a qualified Spanish reviewer; other languages initially receive
structural checks only.

### 2. Add assets, previews and translation validation

Bind images, captions and alt text by digest. Preview the final destination
format without executing imported scripts. Validate terminology, placeholders,
links, length, Unicode and provider locale mappings; use actual Steam export
templates for CSV round trips.

**Acceptance:** conversion/truncation and stale translations are visible before
review. Changed assets or mappings invalidate approval. Unsupported mappings
remain unavailable with a human-editor handoff.

### 3. Add one qualified publication adapter

Start with Steam announcements and itch.io project devlogs. Verify supported
authentication, write/inspect operations, limits and idempotency against current
official provider documentation before selecting an API or browser adapter.
Do not invent an undocumented write endpoint. Keep a reviewed editor export
when reliable automation is unavailable.

An adapter must submit the exact approved revision, distinguish draft from
live save, bind account/project and retain its remote identity. Browser
challenges or unexpected UI changes pause work; upload tokens are not website
cookies. Detect external edits before changing published text.

**Acceptance:** changed approvals, missing credentials, ambiguous results and
partial destination success cannot publish unreviewed or duplicate content.
Opening a handoff never marks publication complete.

### 4. Extend publication scheduling and destinations

Use one scheduler owner per post: local dispatch or provider scheduling. Pin an
explicit UTC instant, displayed timezone, allowed lateness window and release
gate. Ambiguous/nonexistent local times refuse. Runs outside the approved window
require human rescheduling.

Provider schedules are remote commitments: pausing locally does not cancel
them. Cancel/replace through a supported adapter or human handoff; report when
it is too late. Release-dependent posts require verified release state or a
labelled exact-BuildID operator attestation.

Website/static-blog adapters reuse reviewed content and authorized deployment;
verify the public page after build. CMS adapters retain draft IDs and detect
drift. Social variants review every segment, audience, media and final URL;
reconcile partial threads before sending unsent segments.

**Acceptance:** restart, revocation, remote schedule cancellation, release gates
and partial success retain independent outcomes and never repost completed
destinations. No silent deletion of live posts as rollback.

## Verification and exclusions

Use temporary directories and mocked providers for schema, approval, stale-state,
duplicate, interruption and timing tests. Live provider/rendering claims require
authorized destinations and exact reviewed test posts. Preserve approvals and
receipts separately from worker logs; record coverage in place rather than
appending run histories.

Community replies, campaigns, store-page editing and broader editorial
collaboration need separate acceptance. Pricing, sales, DRM and full game-content
localization are outside this feature.
