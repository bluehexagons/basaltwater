# Game releases, posts, and translations

Status: planned extension of [game publishing](GAME_PUBLISHING.md), 2026-10-05.
Documentation only; no implementation or provider changes are authorized by
this planning work. The parent plan owns installation, authentication, uploads,
jobs, and delivery ordering. This document owns build promotion, release
coordination, writing, translation, human review, and post publication inside
the same web panel.

## Product contract

Support preparing and promoting game builds, drafting release notes and posts,
translating them, reviewing them, and publishing them. Agents can prepare work
and run authorized automation. **All public-facing writing requires human
review before publication**, including translations and edits to existing
published material. Standing permission to upload builds is not permission to
publish new writing.

**Steam default-branch release remains a human action on the Steamworks site.**
The panel prepares a handoff and records the result. Agents, scheduled jobs,
browser automation, and API adapters must not perform that final step.

Confirmed delivery order: Steam announcements/events and itch.io posts first;
website/blog and social publication are also planned, with later adapters.
Start itch.io qualification with project devlogs; distinguish account-level
posts from project posts before extending that adapter. Project languages
default to English only. English and Spanish are the initial tested editorial
languages; other languages can be configured without being claimed as tested.
Store-page copy and other public text must follow the same review model when
supported, but arbitrary store administration is not part of the first adapters.

## Provider capabilities and boundaries

References checked on 2026-10-05. These are capability inputs, not evidence
that Basaltwater already implements or has live-qualified them.

| Operation | Proposed integration | Boundary |
| --- | --- | --- |
| Steam upload | Planned SteamCMD upload adapter; upload-only by default. | An uploaded build is not a promoted build. |
| Steam non-default branch promotion | Select an existing BuildID and an explicitly allowed beta branch; qualify the documented `SetAppBuildLive` API with `GetAppBuilds`/`GetAppBetas` observations. | Uses a separate publisher API key, not the SteamCMD login token; optional capability with its own setup. |
| Steam default/public branch | Open the app's Steamworks Builds page with the exact BuildID and release checklist. | Human completes the change on Steamworks. Never call the default/public API path or simulate the click. |
| itch.io release channel | Upload a verified retained artifact to the explicitly selected destination channel. | Treat as a new channel upload; do not promise remote move/copy/promotion primitives or identical provider build IDs. |
| Steam announcements | Prepare reviewed text, assets, and localized import files for the provider editor; track publication. | Automated submission requires a separately qualified adapter. |
| itch.io devlogs | Prepare reviewed text/assets and a provider-editor handoff; track publication. | Butler authentication does not establish a devlog editor session or post-writing API. |

Valve documents build/branch inspection and `SetAppBuildLive` with a server-side
publisher key. Its default/public path includes confirmation behavior; this
project intentionally excludes that path and retains the requested manual
Steamworks release. See [ISteamApps](https://partner.steamgames.com/doc/webapi/ISteamApps).

A publisher API key, when needed for beta promotion or observation, is entered
once through the protected panel and stored privately on the VM. Scope it to
the relevant applications where the provider permits, show presence/last check
only, and support replacement/removal without revealing it. It is a separate
optional credential capability; never copy it through the controller, embed it
in a project manifest, or require it for ordinary SteamCMD uploads. Provider
website sessions for posts are separately authenticated as well.

Steam's event editor supports drafts, scheduled visibility, localization, and
CSV imports. Changes saved to a published event can become visible immediately.
Use its exported localization template when preparing imports. See
[Events and Announcements](https://partner.steamgames.com/doc/marketing/event_tools).

The reviewed [ISteamNews reference](https://partner.steamgames.com/doc/webapi/ISteamNews)
describes retrieval, and the reviewed
[itch.io server API](https://itch.io/docs/api/serverside) does not document a
devlog-writing endpoint. We have not established a supported public post-write
API for either destination. Do not invent one or rely on undocumented private
endpoints. The provider editor is a supported handoff until an automation
adapter is qualified.

## One release record, separate outcomes

Add **Releases**, **Posts**, **Translations**, and **Review queue** within the
panel's Publishing section. A release record links immutable artifact digests,
source revision, provider build IDs, test evidence, selected branches/channels,
content revisions, human reviews, and per-destination outcomes. It is a small
coordination record, not a second build system or cross-provider transaction.

| Item | States and evidence |
| --- | --- |
| Build | Prepared → uploaded → verified, with artifact digest and provider BuildID. |
| Promotion | Prepared → queued or awaiting human action → dispatched → observed complete; failed/unknown remain explicit. |
| Content revision | Draft → needs review → approved or changes requested; edits produce a new revision. |
| Publication | Approved → scheduled or awaiting provider-editor action → submitted → published/verified; failed/unknown are separate. |

The view should answer what is uploaded, what players can access, which text
is approved, and what still needs a person. Opening a provider link or exporting
a draft never marks an operation complete. An operator-reported result is
labelled as such until a supported provider observation verifies it.

Example: upload build 123, validate it on a beta branch, draft an announcement
and translations, have a human approve the final versions, then show
**Awaiting Steamworks default release**. Once a human releases build 123 and
its state is recorded, the exact approved announcement can be published through
an available adapter or its provider-editor handoff. Upload completion alone
must not trigger a post saying that the update is live.

## Promotion workflow

1. Select the exact existing build/artifact and target branch/channel. Display
   the currently observed target build, its observation time, and the proposed
   change. Never choose "latest" again during execution.
2. Validate app/depot ownership, artifact identity, applicable test evidence,
   and the configured promotion policy. Record the expected previous target
   build to detect another release occurring between preparation and dispatch.
3. A manual panel action or authorized job may promote to an explicitly allowed
   non-default Steam branch. Keep promotion authorization separate from upload
   authorization. Fail on stale target state and require a new preparation;
   do not overwrite a newer release silently.
4. For Steam default, create a handoff with the app Builds link, exact BuildID,
   evidence, previous build, and any release blockers. A person acts on the
   Steamworks site, then records completion; optional read-only API inspection
   verifies the selected default build. An API confirmation response, browser
   navigation, or checkbox alone is not provider verification.
5. Read back the destination after a supported automated promotion. An ambiguous
   result blocks dependent jobs and requires reconciliation. Rolling back is
   a new promotion to a known previous build, with the same branch policy;
   Steam default rollback is also manual on Steamworks.

Reject `public`, `default`, empty/implicit branch targets, and any provider alias
resolving to the default branch in automatic promotion adapters. Validate this
in the shared backend, not only the panel. Upload VDFs remain upload-only;
repository `SetLive` values cannot smuggle promotion into an upload job.
Serialize promotion per app/branch and share provider/session locks where
needed. Beta branch names do not prove restricted access; surface the observed
audience or mark it unknown.

For itch.io, promotion reuses the exact retained artifact and reviewed public
metadata for a destination-channel push. Retention of release artifacts is
explicitly selected before the upload worker removes its temporary staging.
If the artifact is missing, require recovery of the matching digest, not a
silent rebuild. Keep the prior artifact for a deliberate compensating upload
where supported; do not claim a transactional reversal of a public release.

## Project language configuration

Use `basaltwater.json` for non-secret project language declarations. Propose an
optional top-level `publishing` section, with a `languages` object:

```json
{
  "publishing": {
    "languages": {
      "source": "en",
      "supported": ["en", "es"]
    }
  }
}
```

This is a proposed fragment to merge into a complete manifest, not a currently
accepted manifest. Today's version-1 parser rejects unknown top-level fields.
Implement this as an explicitly documented additive version-1 extension across
`lib/project_manifest.py`, initialization/serialization, validation, and all
consumers; older Basaltwater versions will reject it and must be upgraded.
Existing manifests without the section remain unchanged. Allow a manifest with
`components: []` and valid publishing metadata even without CI so a game does
not need a fictitious web deployment. Deployment commands still reject such a
metadata-only project as having no deployable components.

| Setting | Contract |
| --- | --- |
| Missing `publishing.languages` | Effective `source: en`, `supported: [en]`; no automatic Spanish opt-in. |
| `source` omitted | English; it must belong to the effective supported list. |
| `supported` | Defaults to `[en]` when omitted; otherwise a nonempty list of validated, normalized language tags. Reject duplicates, malformed values, and a source absent from the list. |
| Spanish | `es` is the initial translation target under test. Regional choices remain explicit; do not silently equate every Spanish provider locale. |
| Other languages | Accept well-formed language tags beyond English/Spanish; label unqualified translation/rendering coverage and block unsupported provider mappings with guidance. |

The panel reads the selected project's manifest and displays its path/revision
and effective language settings. UI edits prepare an explicit manifest change
for the operator to save/review; never silently rewrite a repository from a
scheduled job. For projects without a manifest, keep the same English-only
default and offer to create one. Do not maintain a competing hidden language
list in VM configuration. Per-post language selection is a subset of the
project list, not another project default.

Source-language changes mark dependent translations stale. Removing a language
holds its pending publications and preserves drafts/history; adding one creates
no translation, approval, or publishing authority automatically. A job pins the
manifest/configuration revision and revalidates changes before dispatch.
Credentials, approvals, remote receipts, schedules, and permission to publish
remain VM-local operational state and never enter the manifest. The declaration
describes communication languages; it must not claim the game itself has been
localized into those languages.

## Writing and translation workflow

Drafts can come from an operator, repository release notes, an agent task, or
imported text. Generation is separate from the deterministic publisher and
creates drafts only. Use selected commits/issues/test evidence as inputs; do
not turn private issue details, raw logs, or unsupported claims into public
copy automatically. Record source references and unresolved claims for review.

Maintain one source revision plus destination-specific and language-specific
variants. Each variant includes the final title, summary, body, links, captions,
alt text, and text in attached images. Store asset digests with the revision.
Steam/itch.io formatting conversion happens before review so the reviewer sees
the final destination rendering and any truncation or conversion differences.
Escape unsafe markup and validate links; the preview must not execute imported
scripts or active embeds.

Use the manifest's source and supported languages. Additional project editorial
settings cover provider language mappings, terminology/glossary, style, and
text that must remain unchanged. Preserve placeholders and links; validate
missing strings, unexpected substitutions, length constraints, Unicode,
and directionality.
Use the current Steam CSV export schema for locale mapping and round-trip
checks, not a guessed table of language codes.

Show source and translation side by side, with changed passages, terminology
warnings, and an optional back-translation to assist review. Each translation
requires its own human review; source approval and machine quality checks do
not approve translations. A source revision change marks dependent translations
stale and cancels their pending publication until refreshed and reviewed.
An operator may publish an explicitly selected approved subset of languages;
show missing locales and provider fallback behavior. Never silently substitute
an unreviewed machine translation.

## Human review and publication authority

Review is an explicit human action in the existing authenticated panel. The
reviewer can approve a displayed group of destination/locale variants together,
but the approval records each exact rendered payload and asset digest. Record
the authenticated panel principal and time; a shared panel login identifies
that account, not a uniquely proven person. Unique reviewer accounts can be
added later without assuming the current panel has individual-user auditing.

An approval binds content revision, destination/project, language, author
identity, attachments, links, visibility/category, timing or allowed publication
window, and any release dependency. **Approve for publication** can also
authorize delivery at the specified time or after the specified release gate.
No second per-run review is necessary when those approved inputs are unchanged.

Any edit, new translation, changed destination/asset/link, formatting change,
or substantive timing/context change invalidates the affected approval and
queued submission. Resolve template variables before review. Approving a
template, an English source, or an automation job does not approve future text.
Corrections to live posts follow the same draft/review/publication sequence;
never autosave an unreviewed correction directly to a provider's live editor.

Draft-generating agents and scheduler APIs cannot create approval records or
set an `approved` flag in imported files. Human approval is a distinct panel
action using its authenticated, anti-CSRF-protected route; automation accepts
only a recorded approved revision. Check the approval at dispatch, under the
same lock as cancellation/revocation, rather than trusting queue-time checks.
Agent instructions prohibit using browser tools to click the human approval
action. Do not expose approval operations as agent tools.

This is an enforced contract of the managed workflow, not a claim of isolation
from root or arbitrary same-user code. The parent plan's shared-account
credential boundary still applies. Agent-editable JSON cannot be authoritative
review evidence; store authoritative reviews with the panel's trusted control
state and restrict draft jobs' writes to their own inputs. If deployment grants
an agent unrestricted access to that control state or provider sessions, report
that trust boundary explicitly rather than claiming tamper-proof human review.

The requirement covers all public writing produced by this feature: posts,
patch notes, public build descriptions/version labels, translated titles,
captions, and release documents included with uploaded builds. Upload recipes
must reference reviewed revisions for supplied public text, including bundled
release notes; internal build IDs and private operational logs are not editorial
content. Content hashes and declared text manifests connect those reviews to
the artifact. This is not an automatic proof that arbitrary game assets contain
no unreviewed text; the project owns review of its other player-facing content.

## Publishing, scheduling, and manual handoff

Each destination adapter reports independent capabilities: prepare, export,
create draft, publish, schedule, inspect, edit, and withdraw. Distinguish native
API, qualified browser integration, and human-editor handoff. Missing capability
must be visible before scheduling; a working upload login does not prove post
publication is available.

The baseline Steam workflow exports the approved localized payload and assets,
provides the relevant editor link, and records a human's published URL/event ID.
The itch.io workflow provides the approved devlog payload/assets and analogous
handoff. If a human edits text in the provider editor, it must be reviewed there
before publishing and reconciled as a new local revision; earlier approval does
not cover the changed text. Mark externally edited posts as drifted until that
reconciliation. Final rendering changes from a provider import require review.

Automatic delivery is enabled only for a qualified API or explicitly configured
browser adapter that can submit the exact approved version and identify the
result. Browser support must validate account/project identity, distinguish
draft from live save, handle login challenges by pausing, and stop on unexpected
UI changes. Keep its dedicated VM-local browser session out of agent transcripts
and exports. Do not reuse SteamCMD tokens as website cookies. Human handoff
remains available if automation cannot reliably verify these properties.

Use the existing scheduling owner for publication jobs. Build polling remains
interval-based; posts add a one-off absolute publication time with a displayed
timezone and an explicit UTC instant. Reject ambiguous/nonexistent local times
and define an allowed lateness window. A delayed run outside that window needs
human rescheduling, not an unexpected announcement after a restart.

Choose one scheduler owner per post: Basaltwater dispatch or provider-native
scheduling. Track a provider schedule as a remote commitment; pausing the local
job does not cancel it. Revocation or editing must also cancel/replace the remote
schedule through a supported adapter or an explicit human handoff. Once content
is submitted, cancellation may be too late; show that fact immediately.

At dispatch, require the exact approved payload, valid destination credentials,
publication window, and any release gate. A "now available" post linked to a
Steam default release waits for the human completion record and fresh readback
when supported. Where automated verification is unavailable, require an explicit
operator attestation with the BuildID and mark it operator-confirmed. Do not
schedule such a post independently on the provider if that bypasses the gate.
Advance announcements may have no release dependency when reviewed as such.

Retain content approvals, payload hashes, destination receipt/URL, and unresolved
publication outcomes separately from short-lived worker logs. Give each
destination/revision a durable dispatch identity. On ambiguous submission,
inspect by receipt where possible and otherwise request reconciliation rather
than repost. Destinations complete independently; show partial success without
republishing successful posts. Never silently delete a published post as rollback.

## Delivery and acceptance

Extend the parent plan in three slices after its upload/job foundation:

| Slice | Result | Required evidence |
| --- | --- | --- |
| Promotion and release coordination | Release records, allowed beta promotion, itch.io artifact re-upload, and Steam default handoff. | Correct BuildID/digest, stale-target rejection, exact branch observation, explicit default/public rejection through every automatic path, and manual default completion evidence. |
| Editorial preparation and review | Manifest language extension, agent/operator drafts, translations, destination previews, human review queue, immutable approvals, and provider-editor exports. | English-only defaults, English/Spanish review and rendering, other valid languages accepted with accurate capability limits, manifest round-trips, and every locale/public text field requiring human review. |
| Publication adapters and scheduling | Explicit adapter capabilities, human handoffs, qualified automated submission, one-off schedules, release gates, inspection, and reconciliation. | Exact approved payload only, no duplicate posts after interruption, late jobs held, remote schedules distinguished, external edits detected where inspectable, and manual actions never reported as automatic successes. |

Use mocked APIs/processes and temporary directories for tests. Include old
manifests, publishing-only manifests, language-list changes, extra configured
languages, invalid locale mappings, broken CSV round-trips, hidden text fields,
changed images, unreviewed translations, stale review tokens, queue/approval races, publication
window expiry, partial multi-provider success, beta/default aliases, provider
session expiry, and failure between remote acceptance and receipt persistence.

Later live qualification uses authorized test apps/projects and clearly reviewed
test posts, covering English-to-Spanish and Spanish-to-English translations,
English-only defaults, edits, accents, punctuation, and destination rendering
as well as first publication. Human
review of Spanish uses the operator's language competence; machine scoring is
not a replacement. Additional languages get structural tests initially, not an
unsupported claim of linguistic or provider-rendering qualification.
Record unsupported automation as a handoff capability, not a passing automated
test. Default-branch release is never automated as a qualification shortcut.

## Website/blog and social delivery plan

These channels are planned follow-on work after Steam/itch.io; they reuse the
same content revisions and release records rather than generating a separate
unreviewed announcement per channel. Choose the actual site/CMS and social
providers during adapter qualification, not by assuming every service has the
same API or scheduling support.

| Channel | Planned workflow | Acceptance boundary |
| --- | --- | --- |
| Game website/static blog | Render approved localized posts into the project's content format, prepare a repository change/preview, then invoke its existing authorized deployment workflow. | Bind the review to final output/assets, slug and locale routing; a Git commit or successful build is not proof that the public page is live. Read back the deployed page. |
| Hosted blog/CMS | A provider adapter creates a draft, maps reviewed locales/assets, then publishes or schedules the immutable approved revision. | Authenticate separately, preserve remote draft IDs, detect external edits, and distinguish local cancellation from a provider schedule. Fall back to export/editor handoff for missing API support. |
| Social posts | Prepare a platform-specific short variant or thread, translated variants, media, alt text, canonical links, and ordered submissions. | Human review includes truncation, each thread segment, media text, account and audience. Enforce provider limits before review; record each remote ID and resume only unsent segments after reconciliation. |

For each adapter, first record its supported authentication, scopes,
draft/schedule/edit/delete/inspect operations, rate limits, and idempotency
behavior from current official documentation. Then add format conversion and
previews, human approval binding, deterministic submission, and readback tests.
Finally qualify on an operator-selected test destination. No adapter may claim
unattended publishing while it still requires an untracked human editor action.

Public links and summaries cannot be regenerated after approval. If a destination
URL is only known after publication, reserve a stable reviewed URL beforehand or
hold dependent social variants for review once the real URL is available.
Separate language posts, localized URLs, and threads are explicit destinations;
do not broadcast every configured language to every feed by default.

Community replies, store-page changes, release-calendar campaigns, and richer
editorial collaboration need further destination capabilities and acceptance
criteria. They do not create exceptions to human review or justify an unbounded
"publish anything" action. Pricing, sales administration, DRM, and a full
game-content localization system remain outside this feature.
