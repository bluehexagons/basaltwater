# CachyOS agentic desktop qualification record

Status: **current workstation's running stack tested and accepted by its owner**
(2026-09-27). Fresh-install, interruption/recovery, and unimplemented automation
cases below remain separate. Unit tests do not satisfy those live cases.
Copy the record for each disposable CachyOS installation; keep private evidence locally
and commit only reviewed, redacted results. Do not record credentials, personal
window content, or raw session environment. The owning
[delivery plan](CACHYOS_AGENTIC_DESKTOP.md) defines the release gates.

Read-only observations on 2026-09-27, through commit `555efff`, passed local
CachyOS preflight, Codex login status, Arch package-name validation, and package
cleanup preview. No setup, updates, or cleanup were applied. These observations
do not pass the live acceptance cases below.

## Accepted workstation evidence — 2026-09-27

The owner confirms that everything running on this workstation can be considered
tested. This supersedes the earlier prerequisite-only assessment for the active
stack. At source commit `5026fb12`, the audit observed CachyOS x86-64 bare metal,
kernel `7.2.8-1-cachyos`, Plasma/KWin `6.7.5`, Wayland, PipeWire `1.6.9`,
WirePlumber `0.5.17`, and the NVIDIA graphics stack. Daily operation covers the
desktop, audio, gaming, Sunshine, native browsers, and T3 desktop.

T3 `0.0.42-1` runs a real Codex provider thread and terminal. Git/Git LFS,
GitHub CLI authentication, Codex `0.157.1` authentication, Node `26.10.0`, npm,
pnpm, Python `3.14.7`, and uv work. The successful setup selection is private
and `refresh --dry-run` reproduces it. No failed system/user units or package
database errors were found; 136 focused tests passed at that commit.

The audit also found broad T3/Sunshine listeners and unrestricted saved UFW
allow rules. Their existence is recorded, not treated as a security acceptance
of arbitrary network exposure. The new opt-in firewall policy needs its own
apply/client-connectivity test. No fresh reinstall, forced interruption,
web/desktop switch, or automated portal/input qualification is implied by
acceptance of the currently running stack.

## Environment

Record source commit, date, operator, bare metal versus VM, CachyOS/Plasma/KWin
versions, CPU variant, GPU/driver/compositor combination, portal backend,
PipeWire, AT-SPI/Python GObject, and tested applications. Record results for
different GPU vendors separately. A VM may qualify session helpers, but cannot
run the current bare-metal setup profile or qualify real GPU behavior. Qualify
the latest fully updated rolling release, recording Shelly and Plasma Login
Manager versions alongside the KDE Wayland stack; older ISO defaults are not
a separate support target.

## P0 prerequisite gate

Run `basaltw local cachyos-doctor --json` from a terminal in the disposable
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
| Setup | Fresh/rerun/interrupted setup, existing workspace and credentials preserved, recovery after failure | Not run |
| Refresh | Successful selection saved privately; source upgraded before a fresh CLI replays the same flags; missing state and source failure stop before setup; failed/preview runs retain prior selection | Not run |
| Selection persistence | Private validated selection records, omitted options stop future management without deleting data | Not implemented |
| T3 desktop | Retain an installed t3code-bin, install when absent through default Shelly with review prompts, check optional paru/yay fallbacks only when Shelly is absent, preserve web service on cancellation/failure, discover Codex from KDE, complete a thread and terminal command | Not run |
| T3 mode switching | Web → desktop disables only the owned service; desktop → web activates isolated data; preserve both environments and desktop package | Not run |
| Codex lifecycle | Fresh standalone install, managed update and rollback, external-manager preservation, existing configuration and credentials retained | Not run |
| Setup cleanup | Three cached package versions, installed version and recent archives retained; active-package lock defers deletion; user data and active agent resources preserved | Not run |
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
