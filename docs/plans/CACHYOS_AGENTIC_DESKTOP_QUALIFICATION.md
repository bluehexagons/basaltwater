# CachyOS agentic desktop qualification record

Status: **current workstation's running stack tested and accepted by its owner**
(2026-09-27), with native desktop/application evidence added on 2026-10-04.
Fresh-install, interruption/recovery, and broader automation
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

### Native application/control evidence — 2026-10-04

The owner approved KDE portal consent for this task. Tests ran from source
after the Blender compression fix `8d68852`, on CachyOS x86-64 bare metal
with the existing NVIDIA stack: Plasma/KWin `6.7.5-1.1`, PipeWire `1:1.6.9-1`,
WirePlumber `0.5.18-1.1`, portal `1.22.1-2.1`, KDE portal `6.7.5-1.1`,
AT-SPI `2.60.7-1.1`, Python GObject `3.56.3-1` and GTK3 `1:3.24.52-1.1`.
The owner selected one 2560×1440 monitor plus keyboard/pointer access.

| Check | Observed result |
| --- | --- |
| Portal capture/input | Private selected-monitor PNG; pointer clicks and paced keyboard/text input passed. |
| Human handoff | Pause button blocked subsequent input; explicit resume worked; Stop button closed portal/helper while KDE stayed running. |
| Blender 5.2.2 LTS | CPU smoke passed after fixing the new default compression false failure. Native console script edited a task cube, saved/reopened the blend and verified the change; viewport capture inspected. |
| Blender interchange | Textured glTF, GLB and OBJ/MTL moved out of the source directory and imported into fresh scenes with UVs, materials, loaded texture pixels and expected shape. Draco-compressed GLB export/import also passed. |
| Inkscape 1.4.4 | ID-based translate, SVG geometry query/reopen and transparent 256×144 PNG passed. Native pointer/keyboard move/save and AT-SPI coordinate-field set-text/activate/save passed; fresh query confirmed x=40. |
| GIMP 3.2.6 | Private GIMP3 batch loaded the PNG and saved XCF plus PNG successfully. Native UI was not exercised. |
| Krita 6.0.4 | Isolated offscreen CLI export failed with X BadWindow; native-session CLI export timed out after 30 seconds. No KRA export readiness claim. |

The agent harness inherited `NO_AT_BRIDGE=1`. A task-only GTK accessibility
environment exposed Inkscape's controls; native launches now enable GTK/Qt
accessibility per process, without changing global desktop preferences.
GStreamer lacked `pngenc`; capture uses raw RGBA and bounded stdlib PNG encoding.
Blender's optional MeshOptimizer bridge was absent but ordinary and Draco
exports passed. Inkscape's startup ICC/accelerator warnings were nonfatal.

Private synthetic files, screenshots and reports were retained locally;
personal desktop captures are not committed. Only task test instances were
closed. No failed system/user units were observed. Existing broad saved UFW
allows and non-loopback T3 exposure were recorded without changing policy.
The installed source checkout contained managed `cachyos-t3/` marker/lock
state, making `upgrade` refuse a dirty worktree. The source ignore rule now
excludes that runtime directory; a local Git exclusion unblocks older installs
without deleting the state.
Audacity, Scribus, Ardour, LMMS, FreeCAD and KiCad were absent; their workflows
are documented and discovered when installed, without claiming live coverage.
Shotcut, Kdenlive, OBS and Remmina package presence was observed without a new
editing/recording/connection check. No GPU backend, audio playback, Qt semantic
workflow, logout/reboot/second-login or additional hardware qualification is
implied. Denial/cancellation, owner loss, lease expiry and stale-reference
handling have mocked contract tests; live coverage above remains distinct.

Record source commit, date, operator, bare metal versus VM, CachyOS/Plasma/KWin
versions, CPU variant, GPU/driver/compositor combination, portal backend,
PipeWire, AT-SPI/Python GObject, and tested applications. Record results for
different GPU vendors separately. A VM may qualify session helpers, but cannot
run the current bare-metal setup profile or qualify real GPU behavior. Qualify
the latest fully updated rolling release, recording Shelly and Plasma Login
Manager versions alongside the KDE Wayland stack; older ISO defaults are not
a separate support target.

### Fast21 feedback and grant contracts — 2026-10-05

The Fast21 audit reported a first portal attempt that stopped without useful
logs, followed by successful owner-approved control. The first failure's cause
was not recovered. It also found native key/button taps too short for the
game's press animation and VM/browser assumptions in the agent manifest.

The implementation now reports initialization separately from a pending Start
response, retains failure stage/detail in private status and logs, and accepts
bounded, interruptible key/button holds. The manifest describes native
authentication and prefers the active session's browser provider, distinguishing
legacy artifact paths from capability. Discovery still launches no applications.

Read-only checks found RemoteDesktop interface version 2 and ScreenCast version
5 on this workstation, with portal 1.22.1 and KDE portal 6.7.5. Opt-in persistent
grants, single-use token handling, owner revocation, pause across restoration,
and bounded session renewal have mocked contract tests. These checks do not
qualify prompt-free restoration, a new live held-button/game interaction,
logout/login behavior or grant revocation on this hardware. Record those
results separately after explicit initial KDE monitor/device approval.

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
| GTK/Qt AT-SPI | Controlled editor save, slow dialog, foreign-window/stale-reference rejection, secret-field redaction | Inkscape GTK edits passed; wider GTK/Qt and lifecycle cases pending |
| Portal capture | User-approved selected screen; private bounded artifact; denial and cancellation | Selected-monitor capture passed; denial/cancellation mocked |
| Portal input | Explicit device selection, revocation, pause, lease expiry, logout cleanup, no privileged fallback | Input, human pause/resume and explicit stop passed; expiry/owner loss mocked; logout pending |
| Clipboard | Separate opt-in and bounds; protected content excluded | Not implemented |
| Applications | Creative/admin/gaming package and GPU/audio readiness matrix | Blender/Inkscape and GIMP batch paths above passed; remaining matrix pending |
| Recovery | Safe reruns, exact managed cleanup, concurrent human application use | Not implemented |
| Collaboration | Schema-aware unavailable/deferred/pending UI, private artifact access, no additional control channel | Not implemented |

Release only the capability and hardware/session combinations whose acceptance
cases pass. Record unsupported applications and backend versions explicitly.
