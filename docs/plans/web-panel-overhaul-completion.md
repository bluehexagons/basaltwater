# Web panel overhaul: completion and qualification

[Plan index](web-panel-overhaul.md) · [Baseline, decisions, delivery, and acceptance](web-panel-overhaul-baseline-decisions-delivery-acceptance.md#baseline-audit-findings)

## Completion and qualification record

Completed on 2026-11-16. All five implementation stages are delivered:

1. Established source contracts and measured the original and enhanced panel
   on the same constrained fixture.
2. Added the authenticated, feature-gated, bounded read model in
   [`web_panel_status.py`](../../common/service_tools/web_panel_status.py).
   HTML and status requests share single-flight caches; failed collection
   preserves the previous observation time. Independently cached probe results
   keep their own source age.
3. Added the CSP-pinned, same-origin controller in
   [`web_panel_refresh.py`](../../common/service_tools/web_panel_refresh.py),
   with initial jitter, per-region requests, hidden-tab suspension, a 30-second
   timeout, bounded retries, `Retry-After`, conditional reads, and tab-session
   pause/manual controls. Named values update without replacing forms or
   documents; accessible source age and health remain separate from transport.
4. Enabled dashboard host/web summaries, Proxmox and audit export summaries,
   safe agent counts/timing, existing storage-scan metadata, and explicitly
   loaded service state. Detailed histories, activity cards, reports, filters,
   and review forms remain page-load snapshots with explicit inspection links.
   Notifications, jobs, journal queries, runner collection, and TLS trust remain
   manual or page-load observations. Storage status reads fixed metadata without
   copying the diagnostic report or starting a scan.
5. Moved passive API dispatch into the status owner, updated the
   [operator guide](../services/web-panel.md) and
   [reference](../services/web-panel-reference.md), and qualified synthetic
   browser behavior separately from a deployed HTTPS panel.

### Reviewed budgets and measurements

The panel admits at most eight concurrent status requests, two collectors
across all sources, and 32 HTTP handler threads. Cold contention returns `503`
with `Retry-After: 5`; warm followers immediately receive the saved reading.
Status bodies are limited to 24 KiB, discovery to 64 entries, and loopback
HomeBox probes to four per discovery cycle. Discovery commands have ten-second
and 256-KiB limits; each HomeBox probe has a one-second response deadline.
Deterministic tests exhaust each concurrency budget and verify the retry path.

The comparison used a fresh Debian 13 VM with one vCPU and 1 GiB RAM, native
host readings, synthetic service/trust discovery, and a real loopback fixture
for HomeBox. Each revision rendered 20 dashboards, followed by five simulated
visible clients exercising the production HTTP handler for 60.6 seconds. The
baseline clients reloaded HTML at 0/30/60 seconds; enhanced clients read host at
0/30/60, web at 0/60, and audit at 0 seconds. Request counts and body bytes
exclude the enhanced clients' initial document and shared script download.

| Measurement | Baseline `b2d916cd` | Enhanced `e2c521e8` |
| --- | ---: | ---: |
| Cold dashboard render | 21.38 ms | 21.75 ms |
| Median warm dashboard render | 6.84 ms | 0.44 ms |
| Initial dashboard HTML | 43,224 bytes | 51,038 bytes |
| Discovery / probe / trust calls across 20 renders | 20 / 20 / 20 | 1 / 1 / 1 |
| Requests during five-client trial | 15 | 30 |
| Status codes | 15 × 200 | 29 × 200, 1 × 304 |
| Response body bytes during trial | 648,360 | 26,140 |
| Median / maximum request duration | 8.04 / 22.29 ms | 0.89 / 15.49 ms |
| Host / discovery / probe / trust calls during trial | 2 / 15 / 15 / 15 | 2 / 1 / 1 / 0 |
| Process CPU during trial | 0.053 s | 0.048 s |
| Peak observed process threads, including simulated clients | 8 | 9 |
| Maximum resident memory | 47,336 KiB | 45,876 KiB |

Warm renders took about 94% less time and trial response bodies about 96% fewer
bytes, with larger initial HTML for status metadata and controls. Both trials
used less than 0.1 CPU-second; the CPU difference is too small to interpret.
The longest native host collection was 14.20 ms and the loopback probe 0.76 ms.
This short fixture trial measures shared collection and transfer cost, not a
general capacity limit or production TLS-discovery performance. Measurements
precede the final source-age, scan-metadata, and disconnect-handling fixes.
They support retaining the source-driven cadences and per-tab jitter; there
was no measured need for cross-tab coordination or a persistent transport.

### Validation and deployment

- `make check-ci` passed 10,161 tests with 26 skipped and 82% branch coverage,
  including syntax, documentation, generated brand assets, package metadata,
  and wheel build/install checks. After the final disconnect-handling and UI
  copy changes, `make check-fast SUITE=web` passed 1,346 tests with one skipped;
  the generated panel specimen was refreshed and checked.
- The full 102-case Chromium/Firefox run passed 99 cases. One storage selector
  regression was fixed; desktop and pairing development-reload timing failures
  were rechecked with source files held steady. The final focused run passed
  all 24 cases: 18 new status cases plus the existing storage, desktop, and
  pairing scenarios in both engines. The
  [status browser scenarios](../../tests/browser/status.spec.cjs) cover multiple
  tabs, no JavaScript, narrow layouts, both themes, focus/forms/disclosures,
  review changes, source clocks and failures, authentication expiry, timeouts,
  retry hints, no overlap, visibility suspension, and unchanged revisions.
- A public `basaltw setup` followed by `basaltw patch` installed the panel on a
  fresh one-vCPU, 1-GiB Debian 13 guest. The systemd service and Nginx configuration
  were healthy. Verified HTTPS checks covered anonymous rejection, authenticated
  access, proxy identity replacement, feature gating, query rejection,
  `no-store`, and empty-body `304` responses. The existing gateway rate limit
  remains in effect; a burst returned `429`, and the browser retained its
  last-good reading and backed off.
- The deployed panel passed Chromium 156.0.8078.4 and Firefox 157.0 smoke tests
  with certificate verification and browser sandboxes enabled, zero CSP errors,
  manual refresh, pause across navigation, unsaved fields/focus, loaded services,
  and 390-pixel light/dark layouts. One explicitly requested quick storage scan
  completed through passive progress updates; opening its result preserved the
  selected view. Final log review identified expected client disconnects during
  canceled reads; these now close quietly and release the request budget.
- Raw measurements, credentials, screenshots, and deployment evidence remain
  outside Git. The owned test guest is retained stopped with autostart disabled.
  Native Debian Firefox ESR manual release qualification remains a separate
  check under the [browser-testing guide](../agents/browser-testing.md); it was
  not performed by this automated Chromium/Firefox qualification.

### Follow-up review

A second review on 2026-11-16 corrected these remaining status issues:

- Host and loaded-service API assembly could read values and then obtain a
  different cache generation's timestamp or failure state. Initial host, web,
  and service HTML could also disagree with its embedded snapshot after a
  concurrent publication or a cache expiry during rendering. Values and
  metadata now travel in one `Reading`, reused by both HTML and snapshot
  builders; deterministic regressions cover API and initial-page races.
- Pause/resume and visibility changes could replace a sign-in error with a
  paused or suspended message while automatic requests remained stopped. The
  authorization notice now persists until an explicit retry.
- Completed storage scans retained their start time, causing the browser to
  keep increasing elapsed time. Elapsed time is now shown only while running.
- A metric omitted from a newer successful snapshot could retain its previous
  healthy value beside the newer observation time. Existing rows now become
  explicitly unavailable, and loaded-service record/attention counts follow
  the latest snapshot.

Browser diagnostics now retain intentionally canceled passive status GETs as
separate evidence. Navigation, pause, and hidden-tab cancellation no longer
produce false failures in the older page scenarios; other request failures
remain errors.

Follow-up validation passed `make check-ci` with 10,165 tests, 26 skipped, and
82% branch coverage. The 102-case browser run passed 101 cases; its only failure
was the navigation diagnostic for intentionally canceled status reads. After
correcting that diagnostic and updating service counts, the final focused run
passed all 20 status/navigation cases in Chromium and Firefox. The 52 focused
status/dashboard/host tests also passed after the final HTML change, and
documentation and generated-brand checks passed. Follow-up fixes were qualified
locally; the stopped deployment fixture was not redeployed. The native Firefox
ESR qualification limit above remains unchanged.

### Final integration review

Merged the current `main` before the final backend and browser review. The
browser review found that Back/Forward cache restoration retained an old pause
preference in the script closure. Restored documents now reread the current
tab-session preference, update their controls, and suspend or resume accordingly.
The existing pause regression exercises restoration after both pause and resume
changes. The backend review found no remaining material issues.
The corrected browser behavior and remaining frontend review are clean; all
20 final status/navigation scenarios passed in Chromium and Firefox. The
restoration case dispatches a persisted page-show event deterministically;
native browser history-cache eligibility remains browser-dependent.

## Out of scope

- A wholesale SPA, JavaScript framework, web-server framework, or frontend
  dependency migration.
- WebSockets or SSE for ordinary status in the initial rollout, background
  scans triggered by page views, or automatic journal/filesystem collection.
  Reconsider a persistent transport only if measured polling cannot meet the
  required freshness and load budget.
- Changes to approval authority, action replay/retry behavior, panel access
  policy, or the independent agent/publishing schedulers.
