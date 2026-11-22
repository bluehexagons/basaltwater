# Web panel overhaul: baseline, decisions, delivery, and acceptance

[Plan index](web-panel-overhaul.md) · [Completion record](web-panel-overhaul-completion.md#completion-and-qualification-record)

## Baseline audit findings

This audit describes revision `b2d916cd`, before the implementation below.

The production panel is a Python standard-library HTTP service. Its threaded
handler routes requests and owns shared panel state in
[`web_panel_service.py`](../../common/service_tools/web_panel_service.py). The
dashboard and most detail pages render HTML on the server; feature renderers
are split among `common/service_tools/web_panel_*.py`, and
[`web_panel_templates.py`](../../common/service_tools/web_panel_templates.py)
provides the shared document, theme, and navigation. There is no general
read-only status API for the dashboard to poll. Responses are `no-store`, and
the HTML policy starts from `default-src 'none'` with narrowly allowed scripts.

The current update paths have different owners and freshness limits:

| Area | Current behavior | Design consequence |
| --- | --- | --- |
| Dashboard host overview | Rendered on page load; host overview and local service health use a 30-second in-process cache. The header links to a full dashboard refresh. | Poll a shared cached snapshot and show its observation time; do not collect once per browser or replace the document. |
| Dashboard service discovery | When the web tools are installed, each dashboard render runs local JSON commands to discover published services; configured HomeBox entries make a loopback health request with a one-second timeout. Certificate-trust discovery can also invoke a local command or probe. These results are not covered by the host-overview cache. | Measure and cache each source separately before polling it. Do not implement polling by re-rendering the whole dashboard. |
| Proxmox | Reads a separate exported snapshot that the page labels as refreshed every minute. | Follow the exporter's cadence and expose the snapshot time; page polling must not invoke the collector. |
| Audit and storage inventory | A root-only exporter refreshes the shared snapshot every five minutes. | Show source age and stale state; faster browser requests cannot make this data newer. |
| Notifications | Accepted notifications are appended to a validated, bounded log; the dashboard renders recent receipts and unresolved alert summaries on page load. The summary is not a live health check. | Consider polling a small log revision or receipt count, preserving deduplication and recovery semantics. Do not label receipts as unread or imply overall host health. |
| Jobs, logs, service inspection, runner status, and storage scans | Several views collect only after an explicit load or scan action. Collection has bounded commands, output, or concurrency. | Preserve explicit actions for expensive or broad inspection. Poll only a cheap status backed by existing state. |
| Active work | Agent run timing and storage scan progress are rendered from current state, but their pages ask the operator to refresh for updates. Agent detail pages may also contain prompts, output, and workspace information. | Poll a small status summary only; do not repeatedly transfer detailed reports or trigger work. |
| Forms and approvals | Mutations use POST routes, CSRF checks, validation, and redirects. Some review and approval tickets expire or are single-use. | Never poll by replaying an action, replacing a form, or silently refreshing a page with user input. |

Production pages do not have a general automatic-refresh loop. The source
watcher and browser refresh in `panel-dev` support code editing and are not a
production live-data mechanism. The local Playwright suite covers navigation,
responsive layout, themes, forms, host-inspection flows, and synthetic
development reloads; it does not currently qualify production status polling.

The existing UI already provides a useful base: shared responsive navigation,
semantic forms, text labels alongside status colors, explicit collection
notices, and working navigation/forms without JavaScript. Keep these contracts.

## Product and technical decisions

1. **Poll only read-only status.** Keep write actions, diagnostics, journal
   searches, filesystem scans, and other intentionally on-demand work behind
   their current explicit controls.
2. **Separate source health from browser transport.** A source record reports
   `fresh`, `stale`, `unknown`, `incomplete`, or `unavailable`; the browser
   separately reports `updating`, `paused`, `retrying`, `connection-error`, or
   `authorization-error`. Show the source observation/export time, not the last
   fetch time, and never turn missing data into a healthy zero. A user-paused
   poll or failed request is not itself evidence that the source is stale.
3. **Share work across browser clients.** Reuse existing snapshot files and
   in-process caches; coalesce concurrent collection when a new read model
   needs a collector. Polling must not multiply `systemctl`, network probes, or
   other host work by the number of open tabs. Bound status-request and
   collector concurrency so waiting browser threads cannot grow without limit.
4. **Keep server-rendered HTML authoritative.** The first response remains
   complete and useful without JavaScript. A small same-origin enhancement may
   update named status regions. Preserve the strict content-security policy;
   do not add broad inline-script permissions to make polling work.
5. **Prefer brief requests over persistent connections.** Polling should have
   one in-flight request per region, stop while the page is hidden, and resume
   with a current read when visible. Enable it by default for eligible passive
   regions; let the operator pause it for the current tab session and keep
   manual refresh available. Use source-driven cadences with initial jitter,
   request timeouts, bounded backoff, and `Retry-After` handling rather than
   one global timer. Do not poll settings, credential, or write forms.
6. **Do not replace whole pages.** Update only status values and freshness
   metadata. Preserve focus, open disclosures, selected filters, and unsaved
   form fields. Show a relative age with an accessible absolute timestamp.
   Announce material value or health changes through a concise status region,
   not every successful poll, and never move focus.
7. **Keep the transport private and bounded.** Read endpoints stay behind the
   existing authenticated panel proxy, return fixed schemas and bounded data,
   remain `no-store`, and never expose raw credentials, unrestricted paths,
   command output, or private operational details.

## Implementation sequence

### 1. Establish source contracts and a baseline

- Inventory each proposed live region, its current data owner, refresh source,
  source cadence, failure semantics, and the cost of obtaining a new value.
- Include the uncached dashboard service discovery, HomeBox health probe, and
  certificate-trust check in that inventory. Decide which are eligible for
  background refresh, which need a separate cache, and which should remain
  manual.
- Measure representative dashboard render time, snapshot reads, collector
  duration, response size, concurrent request behavior, and the effect of
  several open tabs on a constrained host. Keep raw host evidence private.
- Establish a per-resource request and collector concurrency budget, including
  behavior when the budget is full. Prefer returning the last known snapshot
  or a bounded `429`/`503` with `Retry-After` over holding an unbounded number
  of request threads behind a slow collector. Start with per-tab jitter; add
  cross-tab coordination only if measured request volume needs it.
- Set initial polling limits from those measurements. Candidate starting
  cadences are 30 seconds for the cached host overview, 60 seconds for the
  Proxmox export, five minutes for audit/storage exports, and 5–10 seconds for
  an already-running operation's cheap state. Do not poll a source faster than
  it can produce a new observation.

### 2. Add a bounded status read model

- Introduce a focused status module and a small authenticated `GET` API, for
  example under `/api/panel/v1/`. Keep HTML rendering and mutation handlers out
  of this module; route only the status resources that have an identified
  owner and freshness contract.
- Return a schema version, resource name, observation/export time, freshness
  state, and bounded data. Keep source health separate from request status;
  include a stable revision or validator so unchanged snapshots can avoid
  transferring the full payload. Use UTC timestamps, define missing and
  future-dated observations, and preserve collection warnings and partial
  results.
- Reuse the current 30-second dashboard cache, Proxmox snapshot, and
  five-minute audit/storage export. Cache service discovery, TLS trust, and
  service probes independently, with their own timestamps and failure state.
  Where new collection is necessary, add shared caching, single-flight
  behavior, explicit time/output/payload limits, and a documented concurrency
  bound before enabling browser polling.
- Keep conditional requests private and explicit: a client may hold a revision
  in memory and send it on the next authenticated request; an unchanged result
  should return no snapshot body. Ensure validators do not bypass feature
  gating or authentication.
- Add focused unit coverage for response validation, timestamps, stale and
  partial states, feature gating, redaction, and collector concurrency. Assert
  that reading status cannot mutate the host or run an expensive collector.

### 3. Build the browser update layer and refresh the dashboard

- Add a small panel refresh controller with an explicit registry of eligible
  status regions and their intervals. Keep the implementation local to the
  panel and compatible with its CSP; do not enable arbitrary scripts or
  third-party assets.
- Start passive-region polling automatically when JavaScript is available.
  Keep the pause preference for the current tab session, so it survives page
  navigation without adding server-side user settings. Initial requests
  should be jittered to avoid all regions firing together after a page load.
- Add “Updated … ago,” “Updates paused,” and “Status delayed” feedback, a
  manual refresh action, and a pause/resume control. A failed request keeps the
  last good value, reports the request failure separately, and lets the
  displayed source age continue increasing. Retry with bounded jittered
  backoff, honor `Retry-After`, and stop or surface a clear error for
  authorization failures. Use a request timeout so a stuck connection cannot
  block the next update indefinitely.
- Start with the dashboard host overview, web-service summary where its probe
  result can be cached safely, and Proxmox snapshot age. Keep the current
  initial HTML useful and provide working links when JavaScript is disabled.
- Test hidden-tab suspension, returning to a visible tab, request failure and
  recovery, no overlapping requests, pause/resume, keyboard operation,
  accessible announcements, and unchanged forms/disclosures during updates.
- Test that unchanged revisions transfer no snapshot body, and that polling
  overload receives a bounded retry response instead of tying up request
  threads. Announce only a changed value or source-health transition; update
  relative-time labels locally without fetching a new snapshot.

### 4. Extend polling to selected detail views

- Add passive status summaries for active agent work and selected service/job
  state only where the backend can read existing state cheaply. Poll an active
  operation more often while it runs, then return to its ordinary cadence.
- Restrict agent updates to run state, elapsed time, and a short safe summary;
  do not include prompts, logs, credentials, or workspace paths in a frequent
  status response. Keep approval decisions and active review forms manual. A
  later read-only queue count may update, but a changed or expired review must
  ask the operator to inspect it again rather than silently replacing its form.
- If notification polling is included, update only the latest-receipt summary
  from the bounded event log. Preserve duplicate-event suppression and
  recovery clearing; identify receipt time separately from sender-reported
  occurrence time. Do not create unread state or turn notifications into a
  host-health signal.
- Keep journal queries, broad service inspection, runner collection, and
  storage diagnostics on demand unless a separate cached status contract is
  demonstrated. Poll scan or action progress from its existing state store;
  never start a second operation to get progress.
- Show source timestamps, age, and collection warnings consistently across the
  selected views. Preserve existing filters and deep links.

### 5. Reduce coupling and qualify the rollout

- As status work lands, extract read-only route dispatch and snapshot assembly
  from the central handler into clear owners. Keep this incremental so mutation
  routes, approval semantics, and the current standard-library deployment
  remain stable.
- Update the web-panel operator guide and reference with what updates
  automatically, the source cadence, pause/manual controls, stale meanings,
  JavaScript-disabled behavior, and which inspections remain on demand.
- Add deterministic browser scenarios to the local `make browser-test` suite.
  Exercise current Chromium and Firefox paths, JavaScript-disabled fallback,
  desktop and narrow layouts, light/dark themes, failed/recovered sources, and
  multiple tabs. Cover source clocks with missing, stale, and future timestamps;
  authentication expiry; request-budget exhaustion; and a changed approval
  queue while a review form remains open. For notifications, cover duplicate
  receipts, recovery, and log-retention boundaries. Keep live deployment checks
  separate from synthetic evidence.
- Roll out first for passive dashboard regions. Review request rate, server
  threads, collector time, and stale/error reports before enabling additional
  pages. Require a focused browser run for each page group and the repository's
  normal checks at integration; do not add the intensive browser suite to CI
  implicitly.

## Acceptance criteria

- Supported passive status regions update without a full document navigation,
  within their source cadence plus one polling interval, and show when their
  data was actually observed.
- Hidden tabs generate no periodic status traffic. Visible tabs do not overlap
  requests for the same region; errors back off and do not erase last-good
  values. Polling starts automatically for eligible regions, honors the
  current-tab pause control and server retry hints, and has a bounded request
  timeout.
- Polling does not launch additional host commands per tab, duplicate
  collectors, change mutation behavior, or reset a form, filter, focus, or open
  disclosure.
- Stale, missing, incomplete, and unavailable data remain distinguishable from
  healthy values; browser pause, connection errors, authorization errors, and
  retries are not confused with source health. Timestamp labels do not claim
  freshness newer than the source.
- Repeated tabs or slow sources cannot create unbounded request-thread or
  collector growth. Requests stay within the reviewed budget, unchanged
  revisions avoid transferring snapshot bodies, and overload has a clear
  retryable response.
- Existing no-JavaScript navigation and forms continue to work. Responsive
  layout, keyboard access, text status labels, light/dark themes, and the
  authenticated/no-store response boundary remain covered.
- Polling cost is recorded against the baseline and stays within a reviewed
  request and collector budget on the constrained fixture. Revisit the
  cadence or disable a region if it exceeds that budget.
