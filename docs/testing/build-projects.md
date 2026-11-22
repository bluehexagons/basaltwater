# Real project build qualification

**2026-11-17 UTC; source `b5ec2462`, Debian 13 x86-64 on separate disposable
builder and web-target guests.** This pass used a pinned public React/Vite
repository with a static deployment component, a separate CI workflow, a Node
major pin and a direct dependency on a published tag archive. Application source
and its manifest were unchanged.

## Compatibility findings

Selected-builder planning previously rejected a manifest whenever it declared
`ci`, even alongside supported deployment components. Delivery now accepts that
combination and selects only component commands and exports. Explicit workflow
requests select the CI commands separately. Automated tests verify both command
validation paths and continued refusal of CI-only deployment and publishing.

Source preparation also rejected the project's literal `allow-remote=root`
npm policy. Snapshot creation and extraction now accept the documented literal
`all`, `none` and `root` values for that option. Round-trip and forged-snapshot
tests cover accepted values, malformed values, duplicates, interpolation and
credential-bearing combinations. Other npm configuration restrictions remain
in the [source contract](../agents/contributing/build-source.md#tracked-project-npm-policy).

## Observed results

| Check | Result |
| --- | --- |
| Public builder setup | `server_lite --build-server --build-project` installed and enrolled the builder through normal setup. Installed snapshot-policy and planner hashes matched the candidate |
| Dry runs | Builder and selected-target dry runs passed without installing source or enrollment on their respective fresh guests. Target planning left no retained controller attempt |
| Selected deployment | Normal `server_web --build-on` setup pinned the application source, generated target enrollment, completed the managed SSH reserve/build/stage/activate flow and acknowledged guarded final profile success |
| Component build | The supervised worker used Node 26.13.1 and the manifest's component commands. Its verified artifact contained the declared static output |
| Explicit CI workflow | A separate request used the same pinned source and ran install/build/test stages. Lint, formatting, type checking, production builds and all 134 tests in eight files passed. Its verified artifact had no exported application files |
| Capacity inspection | Root-local inspection reported both jobs succeeded, with no queued, running or uncertain jobs. Before any submissions, inspection returned an unavailable response |
| HTTPS publication | All four exported files, including the index, matched the authenticated artifact's sizes and SHA-256 hashes over controller-origin HTTPS. Certificate and hostname verification stayed enabled; HTML, JavaScript, CSS and SVG content types were checked |
| Target isolation | The completed target had no Go, Node, npm or C compiler. Saved setup retained artifact mode without a builder selection or builder enrollment |
| Idle reboot | Both guests acquired new boot IDs. Original deployment/CI receipts and the target's completed admission remained queryable through the same pinned transport identities. HTTPS bytes, certificate, index inode and permissions survived |
| Retained refresh | With the builder shut down, public target `refresh` dispatched an independent job that completed successfully and reconciled the retained artifact. Verified HTTPS bytes, content types, certificate and target tool isolation remained correct |

The full hosted checks for this candidate passed: 10,329 tests with 22 skips,
82% aggregate branch-aware coverage, static/documentation/artifact checks,
Python 3.15 packaging and the separate 79-test Node 26 compatibility selection.
The affected local fast suite passed 1,540 tests with three skips.

Both disposable guests were stopped with autostart disabled and their evidence
retained. Existing guest configurations, states and running process identities
were preserved; no active host task remained.

## Boundaries

This qualifies the selected static-site and explicit CI paths for this candidate.
It adds no service runtime-secret-binding controller qualification; that remains
separate from the [native binding and managed SSH checks](runtime-bindings.md).
Public DNS, ACME/Tunnel ingress, browser rendering, external clients, interrupted
work, concurrent administrator changes and power loss were outside this pass.
The local HTTPS certificate was obtained over the already pinned administrative
SSH connection, without enrolling it into a client-wide trust store.

Raw logs, credentials, application source identities, transport keys, guest
inventories and operation handles remain in private evidence outside Git.
