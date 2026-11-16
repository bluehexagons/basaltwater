# Web panel UX and live status overhaul

## Purpose

Make current host and service state easier to trust and follow without asking
operators to reload pages. Add a small, well-bounded status interface and
progressive browser polling while retaining the panel's server-rendered pages,
existing authentication and authorization boundaries, and no-JavaScript
navigation and forms.

Start with polling. Consider server-sent events or another push transport only
if measurements show that short requests cannot meet a real latency or load
need. Do not move the panel to a single-page application or add a web framework
as part of this work unless a measured requirement justifies that change.

## Audit findings

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
| Proxmox | Reads a separate exported snapshot that the page labels as refreshed every minute. | Follow the exporter's cadence and expose the snapshot time; page polling must not invoke the collector. |
| Audit and storage inventory | A root-only exporter refreshes the shared snapshot every five minutes. | Show source age and stale state; faster browser requests cannot make this data newer. |
| Jobs, logs, service inspection, runner status, and storage scans | Several views collect only after an explicit load or scan action. Collection has bounded commands, output, or concurrency. | Preserve explicit actions for expensive or broad inspection. Poll only a cheap status backed by existing state. |
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
2. **Use source freshness, not fetch time.** Each status record should identify
   when its source was observed or exported, its freshness limit, and whether
   it is current, stale, unavailable, or incomplete. Do not turn missing data
   into a healthy zero or imply a new observation just because the browser
   fetched an old snapshot.
3. **Share work across browser clients.** Reuse existing snapshot files and
   in-process caches; coalesce concurrent collection when a new read model
   needs a collector. Polling must not multiply `systemctl`, network probes, or
   other host work by the number of open tabs.
4. **Keep server-rendered HTML authoritative.** The first response remains
   complete and useful without JavaScript. A small same-origin enhancement may
   update named status regions. Preserve the strict content-security policy;
   do not add broad inline-script permissions to make polling work.
5. **Prefer brief requests over persistent connections.** Polling should have
   one in-flight request per region, stop while the page is hidden, back off on
   errors, and resume with a current read when visible. Add a pause control and
   retain a manual refresh path. Use source-driven cadences rather than one
   global timer.
6. **Do not replace whole pages.** Update only status values and freshness
   metadata. Preserve focus, open disclosures, selected filters, and unsaved
   form fields. Announce a concise update or stale/error change through an
   accessible status region without moving focus.
7. **Keep the transport private and bounded.** Read endpoints stay behind the
   existing authenticated panel proxy, return fixed schemas and bounded data,
   remain `no-store`, and never expose raw credentials, unrestricted paths,
   command output, or private operational details.

## Implementation sequence

### 1. Establish source contracts and a baseline

- Inventory each proposed live region, its current data owner, refresh source,
  source cadence, failure semantics, and the cost of obtaining a new value.
- Measure representative dashboard render time, snapshot reads, collector
  duration, response size, concurrent request behavior, and the effect of
  several open tabs on a constrained host. Keep raw host evidence private.
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
  state, and bounded data. Include a stable revision or validator so unchanged
  snapshots can avoid transferring the full payload. Use UTC timestamps and
  preserve collection warnings and partial results.
- Reuse the current 30-second dashboard cache, Proxmox snapshot, and
  five-minute audit/storage export. Where new collection is necessary, add
  shared caching, single-flight behavior, explicit time/output limits, and a
  documented concurrency bound before enabling browser polling.
- Add focused unit coverage for response validation, timestamps, stale and
  partial states, feature gating, redaction, and collector concurrency. Assert
  that reading status cannot mutate the host or run an expensive collector.

### 3. Build the browser update layer and refresh the dashboard

- Add a small panel refresh controller with an explicit registry of eligible
  status regions and their intervals. Keep the implementation local to the
  panel and compatible with its CSP; do not enable arbitrary scripts or
  third-party assets.
- Add “Updated … ago,” “Updates paused,” and “Status delayed” feedback, a
  manual refresh action, and a pause/resume control. A failed request keeps the
  last good value and marks its source stale; retry with bounded backoff.
- Start with the dashboard host overview, web-service summary where its probe
  result can be cached safely, and Proxmox snapshot age. Keep the current
  initial HTML useful and provide working links when JavaScript is disabled.
- Test hidden-tab suspension, returning to a visible tab, request failure and
  recovery, no overlapping requests, pause/resume, keyboard operation,
  accessible announcements, and unchanged forms/disclosures during updates.

### 4. Extend polling to selected detail views

- Add passive status summaries for active agent work and selected service/job
  state only where the backend can read existing state cheaply. Poll an active
  operation more often while it runs, then return to its ordinary cadence.
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
  multiple tabs. Keep live deployment checks separate from synthetic evidence.
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
  values. Users can pause polling and manually refresh.
- Polling does not launch additional host commands per tab, duplicate
  collectors, change mutation behavior, or reset a form, filter, focus, or open
  disclosure.
- Stale, missing, incomplete, and unavailable data remain distinguishable from
  healthy values. Timestamp labels do not claim freshness newer than the
  source.
- Existing no-JavaScript navigation and forms continue to work. Responsive
  layout, keyboard access, text status labels, light/dark themes, and the
  authenticated/no-store response boundary remain covered.
- Polling cost is recorded against the baseline and stays within a reviewed
  request and collector budget on the constrained fixture. Revisit the
  cadence or disable a region if it exceeds that budget.

## Out of scope

- A wholesale SPA, JavaScript framework, web-server framework, or frontend
  dependency migration.
- WebSockets or SSE for ordinary status, background scans triggered by page
  views, or automatic journal/filesystem collection.
- Changes to approval authority, action replay/retry behavior, panel access
  policy, or the independent agent/publishing schedulers.
