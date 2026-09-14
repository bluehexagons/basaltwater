# CachyOS agentic desktop qualification record

Status: **not run**. Debian unit tests do not satisfy this checklist. Copy the
record for each disposable CachyOS installation; keep private evidence locally
and commit only reviewed, redacted results. Do not record credentials, personal
window content, or raw session environment. The owning
[delivery plan](CACHYOS_AGENTIC_DESKTOP.md) defines the release gates.

## Environment

Record source commit, date, operator, bare metal versus VM, CachyOS/Plasma/KWin
versions, CPU variant, GPU/driver/compositor combination, portal backend,
PipeWire, AT-SPI/Python GObject, and tested applications. Record results for
different GPU vendors separately. A VM may qualify session helpers, but cannot
run the current bare-metal setup profile or qualify real GPU behavior.

## P0 prerequisite gate

Run `infra-tools local cachyos-doctor --json` from a terminal in the disposable
user's Plasma Wayland session. The JSON is a prerequisite inventory, not a
test result. Compare session health and listener/service state before and
after each run using the test machine's existing tools.

| Case | Required evidence | Result |
| --- | --- | --- |
| Initial login | Expected user, Wayland socket, native package versions, unit states | Not run |
| Read-only behavior | No new listener, capture, permission prompt, service activation, or desktop change | Not run |
| Missing optional package | Actionable deferred result; other observations remain usable | Not run |
| Portal/AT-SPI not running | Doctor does not start either service | Not run |
| Missing graphical session | Deferred session state; no fallback to another user's session | Not run |
| Remote/invalid bus environment | Only canonical local owned user bus is queried | Not run |
| Logout and second login | Fresh observations refer to the invoking session's prerequisites | Not run |
| Reboot | Desktop preserved; no new background agent or automatic control | Not run |
| Slow/unavailable diagnostic | Bounded timeout or output-limit result; no orphaned helper | Not run |
| Report privacy | No credentials, journal content, personal paths, or UI content in JSON | Not run |

Record failures and remediation with source commit and retest date. This table
qualifies the existing setup and diagnostic contract; it does not release a
machine-use capability. Keep the table unpassed until evidence exists.

## Later capability gates

These tests require the corresponding implementation. Do not simulate them by
marking a package query successful or by using unrestricted desktop tools.

| Layer | Acceptance evidence | Result |
| --- | --- | --- |
| Setup/state | Fresh/rerun/interrupted setup, private validated selection records, disabled management preserves data | Not implemented |
| T3 access | Local pairing, private-LAN pairing from another device, T3 Connect link/status/unlink, service restart and logout behavior | Not run |
| Browser | Pinned runtime pair, isolated local page interaction/capture, strict HTTPS, origin restrictions including redirects and subresources | Not implemented |
| Browser recovery | Interrupted update retains old pair; bounded private artifacts and task profile cleanup | Not implemented |
| GTK/Qt AT-SPI | Controlled editor save, slow dialog, foreign-window/stale-reference rejection, secret-field redaction | Not implemented |
| Portal capture | User-approved selected screen; private bounded artifact; denial and cancellation | Not implemented |
| Portal input | Explicit device selection, revocation, pause, lease expiry, logout cleanup, no privileged fallback | Not implemented |
| Clipboard | Separate opt-in and bounds; protected content excluded | Not implemented |
| Applications | Creative/admin/gaming package and GPU/audio readiness matrix | Not implemented |
| Recovery | Safe reruns, exact managed cleanup, concurrent human application use | Not implemented |
| Collaboration | Schema-aware unavailable/deferred/pending UI, private artifact access, no additional control channel | Not implemented |

Release only the capability and hardware/session combinations whose acceptance
cases pass. Record unsupported applications and backend versions explicitly.
