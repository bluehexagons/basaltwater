# Basaltwater v2.0 preview and release qualification

The current preview candidate is `v2.0.0-rc.6` (Python package `2.0.0rc6`).
The latest final release remains `v0.2.0`. This document is the qualification
record and release gate for the eventual stable `v2.0.0` release.

The `v2.0.0-rc.1` tag remains available for audit. Its Python 3.11 and 3.15
checks failed before release publication. The next candidate corrected those
test failures and restored package version reporting in Git-based installs.

The [coverage map](plans/testing-coverage.md) summarizes selected workflow
checks. Those results do not pass the exact-candidate matrix below. The
[roadmap](plans/roadmap.md) owns implementation priorities, including the
unfinished combined build/CI host; this document is not a progress log.

## Version and publication model

| Stage | Git tag | Python package version | Installer behavior |
| --- | --- | --- | --- |
| Development | `main` | Current preview version | Selected by `dev` |
| Preview candidate | `v2.0.0-rc.N` | `2.0.0rcN` | Opt in by selecting the exact tag; GitHub release is marked prerelease |
| Stable release | `v2.0.0` | `2.0.0` | Selected by `stable` after final qualification |

Increment `N` for each new candidate. Do not move or reuse a published tag.
The release workflow marks `-rc.` tags as prereleases, and the installer’s
`stable` channel matches only unqualified `vMAJOR.MINOR.PATCH` tags. Preview
users select an exact candidate, for example `basaltw channel
v2.0.0-rc.6`. A release candidate is for qualification and feedback; it does
not claim that the stable release matrix has passed.

Publishing a preview requires the tag’s Python package version to match the
candidate number and the release CI checks to pass. The package metadata check
compares the version against the exact tag on release builds, including the
final stable tag. After a failed candidate, fix the issue, increment the
candidate number, and record which previously passed cases need to be repeated.

Basaltwater replaces the repository and runtime namespace. The distribution is
`basaltwater`, the Python entry point is `basaltwater.py`, and the command is
`basaltw`. Internal callers, install/staging paths, state directories, services,
accounts, locks, skills, generated configuration, and deployment manifests use
the new namespace. The gateway command is `basaltwater-web`. New disk serials
and cache volume groups use Basaltwater names; existing storage retains its
durable identities as described in the current
[Proxmox storage guide](operations/proxmox.md).

The infra-tools cutover is complete for the managed fleet. Its migration engine
and automatic client/target migrations are retired. Any remaining old
installation or interrupted cutover requires the pinned
[intermediate version](migration-guide.md) before current setup.
There are no persistent old-name aliases, environment fallbacks, or historical
release support after cutover. The repository is hosted at
`bluehexagons/basaltwater`.

## Repository verification

- The default suite exercises the renamed callers, setup plans, service
  configuration, authentication, agent integrations and state handling.
- Retirement fixtures verify that old installations and incomplete journals
  stop setup without mutations, while current runtime activation preserves
  Syncthing traversal and respects unfinished systemd recovery.
- Installer tests cover new root/user installations, Debian/CachyOS selection,
  custom destinations, source activation failure and interruption recovery.
- The fresh wheel is built, installed and exercised outside the source tree.
  Packaging rejects retired entry modules and skill IDs.
- `make check` validates CLI docs, metadata, generated brand assets and tests.
  Visual identity and contrast checks remain described in [BRANDING.md](brand/README.md).

## Qualification protocol

Run live cases only on disposable systems and data. Mocked system calls,
fixtures, static panel specimens, and development-host observations do not
qualify a release candidate. Test the exact candidate commit and retain a
short, reviewed result for every required case. Never commit credentials,
private hostnames, personal data, or raw logs that contain them.

### Preview publication gate

- [x] `pyproject.toml` declares `2.0.0rc6` for `v2.0.0-rc.6`.
- [ ] Release CI passes on Python 3.11 and 3.15, builds the wheel, and
  installs and smoke-tests the packaged launchers on Python 3.13.
- [ ] The GitHub release is marked **pre-release** and contains the wheel built
  from the tagged commit.
- [ ] An isolated disposable controller installs the exact candidate tag and
  reports its candidate version through `basaltw --version` and
  `basaltw channel`.
- [ ] `stable` still selects the latest unqualified stable tag; preview tags
  remain opt-in.

Passing this gate permits a preview for feedback. It does not pass the live
qualification matrix or authorize the final stable release.

### Candidate results

#### `v2.0.0-rc.6`

RC 6 defers the cloud-init snippet upload until Proxmox capacity checks
and VMID allocation have succeeded. Exercise both a refused allocation and
a successful fresh VM boot on the exact candidate. Verify that neither
leaves a snippet behind. RC 5 results below retain their original scope.

#### `v2.0.0-rc.5`

Qualification on 2026-11-19 UTC used commit
`c705dcd7c7362041f92033c20e86e9ac716059d9` (package `2.0.0rc5`). Release CI
passed on Python 3.11 and 3.15 in Debian Trixie. Python 3.13 built and
smoke-tested the wheel before GitHub published the prerelease. Debian release
jobs now receive the GitHub tag and check the package version against it.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44425585733)

- The published wheel matched the tested CI artifact byte for byte and passed
  a second isolated installation and launcher smoke test. Its SHA-256 is
  `642798fc94717f108493a74208c31cf187dee4715d310d5386426632cf0bdc4d`.
  The public source archive matched the tagged installer, package metadata,
  target setup and refresh modules.
  [GitHub prerelease](https://github.com/bluehexagons/basaltwater/releases/tag/v2.0.0-rc.5)
- A Debian 13 VM installed the exact tag in a fresh controller account and
  upgraded a second account from RC 3. The upgrade preserved private file
  bytes and mode. `stable` resolved to historical `v0.2.0` and refused to
  activate it. `dev` selected `main`, and switching back selected RC 5 again.
  A pinned system installer also preserved the workstation's saved setup and
  account state during the upgrade.
- On a Debian 13 VM, a SIGKILL immediately after source activation left a
  private recovery marker. Both exact RC 5 refresh preview and dispatch
  refused with its operation ID and launched no refresh unit. Explicit
  rollback restored the prior snapshot and retained the staged checkout,
  saved setup, unpushed work and storage data. Preview worked again after
  recovery.
- On the Debian 13 workstation, a target run with the pairing credential
  hidden in a private mount namespace failed before changing the package
  database, writing a setup marker or replacing the saved configuration.
  A dry run accepted the existing credential. The workstation also completed
  a full exact RC 5 setup using that credential.
- Chromium trusted the workstation's enrolled CA and paired with the managed
  T3 0.1.10 server. The session persisted through reload. A second browser
  profile could not reuse the same link. After reboot, all nine required T3
  doctor checks, gateway and pairing HTTPS, and SQLite integrity passed.
- Native FreeRDP exercised human-first startup and concurrent agent starts,
  keyboard input, clipboard in both directions, dynamic resize, reconnect
  after an XRDP frontend restart, and logout. Killing the disposable Xorg
  process retired its generation. A new session started and logged out, and
  the XRDP frontend and sesman came back after reboot. A live PAM/logind X11
  session blocked the managed desktop setup guard until that session logged out.
- The standalone HTTPS approval panel ran a refresh preview with a live
  desktop and T3 service. An approved refresh with a held build lease failed,
  retained the drain, and kept the same T3 process, desktop generation,
  source, and unpushed file. After the lease ended, a separate approval
  completed refresh on the pinned candidate.
- A clean unprivileged Debian 13 `server_web` LXC passed exact source, saved
  setup, managed account, and package checks. Nginx served HTTP and SSH access
  worked again after the container restarted. A managed-account rename ran
  through a separate SSH administrator. After a SIGKILL immediately after
  identity cutover, explicit resume preserved the UID, GID, private file and
  timer definition. SSH and the timer passed after another container reboot.
- The Debian 13 storage VM reran provider and guest setup with its existing
  origin/cache pair, backup disk and additional disk. The LVM UUID, mount
  UUIDs, disk serials, application accounts, and Syncthing device ID stayed
  stable across the reboot and RC 5 reconciliation, even as Linux changed
  device names. The mirror and scrub tests passed again. An unavailable backup
  mount blocked sync, and damaged source bytes left the healthy mirror intact
  until restore recovered the file contents and metadata.
- A failing systemd data mount prevented Samba, Gogs, Syncthing and HomeBox
  from starting against the underlying filesystem. All four recovered after
  restoring the mount and restarting their units. A fresh authenticated Samba
  round trip, the Gogs login endpoint, and Syncthing's device identity passed.
- The single-node provider identified a detached managed disk from live
  Proxmox configuration. The Debian guest could not finish boot while the
  disk was absent. Reattaching its original retained volume brought back the
  mount, service identities and mirrored data without replacing the disk.
  The guest refused a deliberately changed serial. With a preexisting
  identity recorded consistently in the provider and guest, a later rerun
  preserved the retained legacy serial, original volume and filesystem UUID.
- The provider refused a new headless VM whose memory floor would exceed the
  host target. It had already uploaded a cloud-init snippet, which the test
  operator removed after verifying that no guest referenced it.
- HomeBox login, the inventory item and its attachment, and the WebSocket
  passed in Chromium over a verified SSH tunnel. The candidate HomeBox CLI
  archived the service, restored a deliberately damaged attachment with its
  original bytes and ownership, and verified health after the restore.
- On a Debian 13 web VM, the 31 managed audit rules and the native collection
  and status panel verified a real file event. Missing audit tooling, stopped
  auditd and a missing managed rule surfaced failures and preserved cursors.
  Audit, Fail2ban and SSH retention-gap evidence survived a subsequent healthy
  collection. The certificate CLI and panel agreed for private and public
  trust, missing trust material, and verification failures.
- The Guacamole suite passed all 100 Playwright cases across Chromium and
  Firefox on the exact tag. Native Firefox ESR 153.7.0 passed keyboard, zoom,
  security-panel, pairing and approval checks. Firefox's native Certificate
  Manager imported the fixture CA with website trust and continued to reject
  the wrong hostname. A new Firefox profile rejected the installed workstation
  gateway before importing its CA and accepted it after profile-only enrollment.
- An invalid webhook signature was refused and duplicate signed deliveries
  produced one build. The operator independently verified the bundle and then
  authorized it for the enrolled target. A recovery marker from an earlier
  test kept the first admission uncertain. After verifying the earlier
  rollback, a distinct authorization delivered the artifact to both HTTPS
  routes. The original admission retained its uncertain result. An unhealthy
  replacement ran its loopback health probes, stayed hidden from HTTPS traffic,
  and rolled back to the verified working binary. The neighboring static
  application remained available throughout 439 paired HTTPS samples.
  The reused builder reached its configured reservation limit and retained a
  second signed input as `retry_pending`. Increasing the lab quota allowed the
  input to queue. The worker refused the job after detecting the changed
  configuration, and a fresh signed build passed after restarting the worker.

#### `v2.0.0-rc.4`

RC 4 blocks Debian refresh previews and dispatch while a source activation
needs recovery. It also checks T3 device-pairing credentials before target
setup can change packages or services. The Debian release jobs fell back to a
stale version constant because the test containers did not get the release
tag. The workflow stopped before prerelease publication.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44425135437)

#### `v2.0.0-rc.3`

On 2026-11-18 UTC, release qualification used `v2.0.0-rc.3` at
`e8b9288461c4c65f8a832b5bac1e680893413744` (package `2.0.0rc3`). Release CI
passed on Python 3.11 and 3.15 in Debian Trixie. Python 3.13 built and
smoke-tested the wheel before GitHub published the prerelease.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44396675945)

- The published wheel matched the CI artifact byte for byte and passed an
  isolated installation and launcher smoke test. Its SHA-256 is
  `75ac0e6836bc21f808ee6d5083a0b5b1643b4b580e397150c4e2c89ec2f9fba2`.
  The public source archive matched the tagged installer, package metadata,
  and both changed runtime modules.
  [GitHub prerelease](https://github.com/bluehexagons/basaltwater/releases/tag/v2.0.0-rc.3)
- A disposable Debian 13 VM installed the exact tag in an isolated controller
  account and upgraded a second controller account from `v2.0.0-rc.2`. The
  upgrade preserved the private test file and its mode. `dev` resolved to `main`.
  `stable` resolved to historical `v0.2.0` and refused to activate it. The same
  VM upgraded its system installation with the pinned installer, preserving the
  saved setup and deployed service binary. The exact installer versions and
  both deployed applications were verified again after reboot.
- A new Debian 13 `agent_vm` was provisioned on single-node Proxmox using the
  exact candidate. The installed source, completed setup, SSH host identity,
  managed account, package state, and working Codex and GitHub CLI launchers
  were checked. Provider authorization was disabled for this fixture. The
  account, tools, and SSH access passed again after reboot.
- A Debian 13 `server_lite` VM installed Samba, Gogs, Syncthing and HomeBox on
  a Proxmox-backed mount with a writethrough LVM cache and a separate mirror
  disk. An authenticated Samba round trip, initial parity, missing backup mount,
  integrity failure, mirror protection, restore, disk addition and reboot
  passed. HomeBox 0.27.1 browser login, inventory, attachment and WebSocket
  checks passed over the SSH tunnel. A full archive restored an intentionally
  damaged attachment and its file metadata, and health passed after restore.
- On the Debian 13 `agent_vm`, a SIGKILL during APT index refresh left the
  original setup marker and blocked an ordinary retry. The operator checked
  the saved setup, package state, private file, and SSH host identity before
  resolving the original operation. Setup then succeeded. A separate handled
  APT failure resumed under the same operation ID and preserved the verified
  setup, package, and private file state.
- In a separate account on the VM, RC 3 refused a recent infra-tools workspace
  and directed the operator to the migration guide. The pinned intermediate
  version retained its journal when the user migration was killed immediately
  after the workspace rename. A repeat apply stopped at the journal. Explicit
  recovery restored the original workspace, and a fresh apply preserved the
  private file's bytes, ownership, and mode. RC 3 read the migrated workspace.
- A separate Debian 13 `server_lite` VM exercised the pinned intermediate
  system migration. RC 3 refused the old runtime. A SIGKILL after the new
  source was renamed into place left private state and both source copies
  accounted for in the journal. The intermediate version refused a repeat
  apply, and RC 3 refused setup while the journal was incomplete. Explicit
  recovery restored the old runtime, saved setup, private file, HTTP service,
  and timer. A fresh migration restored the HTTP response under the renamed
  service and linked the runtime to durable state. The operator moved the HTTP
  fixture's document root from deployment staging to `/srv` before repeating
  the full RC 3 setup. The new source, private file bytes and permissions,
  saved profile, HTTP response, timer, SSH identity, and package state passed
  verification after reboot.
- The HTTPS client rejected the standalone approval endpoint until it trusted
  the VM's CA. The approval service rejected the panel credential, a forged
  origin, replayed forms, and an expired review. An approved Admin package
  update failed at an injected APT error. A fresh approval ran a distinct
  successful job and preserved the private file. No plaintext passwords
  appeared in the checked HTTP responses, daemon arguments, approval auth
  file, broker database, or unit logs.
- When the broker was killed after an approved command wrote its test file,
  restart marked the request uncertain and expired a pending request. Stale
  approvals were rejected and the approved command's effect occurred once.
  A second broker restart restored HTTPS approval without operator action,
  and a fresh request from the managed `basaltw` launcher completed.
  A separate timeout test terminated a TERM-ignoring descendant and collected
  its transient systemd unit in 66.5 seconds. The broker rejected an executor
  path replaced with a symlink before it ran the approved test command.
- On the Debian 13 `server_lite` VM, the source refresh activation fetched
  the exact tag and was killed just after the final source rename. The private
  marker retained the operation ID and source paths. Admin's availability
  check blocked refresh and preview. The direct `basaltw refresh --dry-run`
  command still printed a plan and returned success. With the CLI fix from
  `main`, preview and dispatch both refused with the retained operation ID.
  Explicit rollback kept the staged source, private data, and unpushed changes.
  Qualify the fix on the next candidate.
- The installed candidate ran both builder and target roles on the VM, with
  separate authorization for the target. Over verified HTTPS, the builder
  rejected an invalid signature and deduplicated the signed push. A new build
  produced the verified artifact and descriptor, and authenticated SSH delivery
  served the artifact on the target.
- During typed Go service replacement, 17 of 55 paired HTTPS checks observed
  HTTP 503 before healthy activation. In the rejected rollout, 397 of 439 paired
  checks observed the pause. None returned a response from the unhealthy
  release, which answered its direct loopback health probes. The neighboring
  application stayed available throughout. Rollback restored the original
  binary and HTTPS response. A separate SIGKILL during the pause left the route
  on HTTP 503 and blocked another reconciliation until snapshot recovery.
- Retained bundles from prior runs consumed the lab target's staging allowance.
  After verifying that the refused stage left no artifact intent or payload,
  the operator increased the disposable target's configured quota from 1 GiB
  to 2 GiB and retried the same reservation. The retained evidence stayed intact.
- A disposable Debian 13 `server_web` VM upgraded to the exact tag and preserved
  its saved setup. Kernel auditing loaded all 31 managed rules. The panel exporter
  captured a real protected-file event, and a healthy monitoring run advanced the
  cursor. Missing audit tools, stopped auditd, and a missing managed rule group
  each produced a coverage warning and preserved the monitor's cursor. The audit
  service, rules, and panel exporter were verified after restoration.
- A test cursor set before the oldest retained security logs reported gaps for
  auditd, the SSH journal, and Fail2ban. The audit and journal gap boundaries
  matched the retained data. The monitor returned a failure result and advanced
  one 15-minute window. A subsequent healthy scan preserved the recorded gaps.
- The installed `basaltwater-web ca --json` and panel trust renderer agreed on
  five live TLS cases. The VM-local CA matched its published fingerprint, and a
  publicly trusted endpoint passed system trust verification. A missing CA,
  hostname mismatch, and VM-local certificate presented as public each produced
  an unknown-trust diagnostic. Policy variations ran in a private mount
  namespace and left the installed gateway policy and CA intact.
- A disposable Debian 13 LXC deployed the exact candidate through a saved setup
  with zero deployment specs. An independent SSH administrator completed a
  managed account rename and resumed a second rename after a worker SIGKILL at
  the login and home cutover. Numeric IDs, private file bytes and metadata, SSH
  login, saved setup, controller cache, and the storage timer were verified.
  The recovered account and timer persisted through an LXC restart.
- The exact tag passed the Guacamole protocol integration in Playwright
  Chromium 156 and Firefox 157. The full suite passed 97 of 100 checks. Two
  failures came from the Shares & Data fixture and its outdated recovery
  expectations. A third came from Firefox cancelling overlapping navigation
  in the development desktop. The fixture now follows the installed broker's
  approval policy, and the development reload and test navigation were corrected
  on `main`. All 50 Firefox checks and the six affected Chromium cases passed
  after the corrections.
- Native Firefox ESR 153.7.0 on Debian 13.7 amd64 passed panel keyboard,
  disclosure, meter, theme, narrow-layout and 100%/200% zoom checks. The CA
  download matched its verified SHA-256. Current-browser pairing, one-time
  links, approval, forged decisions, expiry and replay checks passed against
  disposable fixtures. A fresh trusted profile received login challenges on
  each surface. The Firefox Settings UI imported the fixture CA with website
  trust in an isolated graphical session. The fixture panel loaded, and the
  wrong-hostname certificate was still rejected. A separate test of the
  installed `server_web` gateway succeeded after the CA was transferred over
  verified SSH and enrolled in a disposable Firefox profile.

#### `v2.0.0-rc.2`

On 2026-11-18 UTC, release qualification used `v2.0.0-rc.2` at
`4e24c2928fd0294b696fed8778ad9e52d4f5cb35` (package `2.0.0rc2`). Release CI
passed the full Debian suite on Python 3.11 and 3.15. Local `make check-ci`
completed with 10,441 tests, 25 skips and 82% branch-aware coverage.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44383019598)

- The published wheel matched the Python 3.13 CI artifact byte for byte and
  passed another isolated installation and launcher smoke test. Its SHA-256 is
  `0f2c12765eb61583b800328c91977791680ea52e35bb4aa343bfb124b16b45e1`.
  The public source archive matched the tagged installer, package metadata,
  and changed runtime modules.
  [GitHub prerelease](https://github.com/bluehexagons/basaltwater/releases/tag/v2.0.0-rc.2)
- A disposable Debian 13 controller VM installed the exact tag from the
  pinned installer, upgraded from a compatible pre-preview commit, preserved
  private state and mode, and reported the tag, commit and version. The installed
  launcher reported the same version after reboot. `dev` followed `main`.
  `stable` resolved to historical `v0.2.0`
  and refused to activate it. An attempted switch preserved the working preview.
- A disposable Debian 13 LXC ran `basaltw deploy` with a saved setup containing
  zero deployment specs. An independent SSH administrator renamed the managed
  user and resumed a subsequent rename after a worker SIGKILL immediately
  after login and home cutover. Numeric IDs, private file bytes and metadata,
  login, setup state, controller cache, and the storage timer were verified.
  The reconciled state and SSH access survived a container restart.
- Two disposable Debian 13 VMs ran the exact candidate as builder and target.
  The builder received a locally sent GitHub-format push over verified private
  HTTPS, rejected an invalid signature, and deduplicated the accepted push.
  After the pinned Node version was installed during builder maintenance, the
  signed revision built successfully. The controller verified the bundle and
  descriptor digests, then used separate target enrollment and explicit SSH
  activation to serve the artifact at both the root and a subpath. The target
  also rejected preflight for an unenrolled application.
- A replacement Go release on the enrolled target returned HTTP 503 to its
  declared health probe. Activation reported failure and retained its recovery
  guard. The prior release returned to service with the original binary digest
  and HTTPS response, and the neighboring static deployment still served the
  verified artifact. During verification, the existing Nginx route also served
  the unhealthy release. `v2.0.0-rc.2` has this behavior.
  See [deployment safety](services/deployment-safety.md#automatic-safeguards).
- A disposable Debian 13 `server_web` VM reported active kernel auditing and
  all 31 managed rules. The panel's systemd exporter produced a fresh snapshot
  containing a real protected-file event. The authenticated panel rendered the
  event over HTTPS with its configured CA. The installed security monitor
  advanced its cursor with healthy sources. Hiding either audit tool, stopping
  auditd, and removing one managed rule group each produced a coverage warning
  in the audit snapshot and a monitor failure that preserved the cursor.
  Auditd, its rules, and the panel snapshot were verified after restoration.
- The installed monitor started from an isolated test cursor before the real
  retained audit, SSH journal, and Fail2ban logs. It reported uncovered
  intervals, returned a nonzero result, and advanced only one 15-minute scan
  window. The audit and journal gap boundaries matched the retained data. A
  subsequent healthy scan preserved the recorded gaps. This qualifies detection
  of missing history before the oldest retained entry for those sources.

Private transcripts and state comparisons are retained by the qualification
operator. The remaining stable work includes supported profile and recovery
matrices, shared desktop and T3 checks, and storage and application
recovery. Multi-node Proxmox and kiosk qualification are deferred to a later
release.

### Stable qualification matrix

Complete all rows except those marked **Deferred** before publishing `v2.0.0`.
Record **Pass** only when the exact candidate commit completed the listed
operation and the result was checked. Use **Fail**, **Blocked**, or **Not run**
for other outcomes.
Results recorded for earlier tags still need qualification on the current
candidate.

| ID | Qualification case | Required evidence | Status |
| --- | --- | --- | --- |
| REL-01 | Source and wheel | Candidate CI passes; wheel installs outside the source tree; `basaltw --version` and packaged entry points match the candidate metadata. | **Pass** for `v2.0.0-rc.5` |
| REL-02 | Installer channels | Fresh exact-tag install and upgrade work; `stable` does not select an `-rc.` tag; `dev` still selects `main`. | **Pass** for `v2.0.0-rc.5` on Debian 13 VM |
| INS-01 | Clean Debian installations | Install controller, server, and agent profiles on disposable Debian hosts; cover the supported bare-metal/VM/LXC paths that each profile supports. Check commands, permissions, and selected services. | **Not run** in full. A fresh controller and Debian 13 `server_web` LXC passed on `v2.0.0-rc.5`. An earlier `agent_vm` fresh install passed on `v2.0.0-rc.3`. Bare-metal installations require a disposable physical host. |
| INS-02 | Existing-install upgrades | Upgrade a supported Basaltwater installation and verify user data, setup state, credentials, and owned runtime paths. Exercise the documented intermediate-version migration for any remaining legacy installation; do not assume direct migration from historical `v0.2.0`. | **Pass** for `v2.0.0-rc.3` controller and Debian server upgrades, including pinned intermediate user and system cutovers. |
| INS-03 | Interruption and recovery | Interrupt migration and setup at documented safe test points. Confirm the operation marker blocks unsafe reruns, recovery guidance identifies retained state, and a successful retry preserves the prior working state. | **Pass** for `v2.0.0-rc.3` setup and upgrade guards on Debian 13. The pinned intermediate user and system cutovers both recovered from SIGKILL and completed on a fresh apply. |
| INS-04 | Services and private data | Compare pre/post checksums and modes for preserved private data; validate application access, managed service/timer health, agent configuration, and absence of retired namespace paths. | **Not run** in full. Private data, live services and timers, and retirement of the legacy runtime passed after Debian server migration and `v2.0.0-rc.3` setup. Broader agent configuration preservation remains open. |
| USR-01 | Managed target-user rename | On a disposable Debian/systemd target, rename a managed account through a separate SSH administrator. Verify UID/GID, home contents, SSH access, setup state/cache, managed units, service state, interruption handling, and resume. | **Pass** for `v2.0.0-rc.5` on Debian 13 LXC, including verified resume and reboot |
| DSK-01 | Shared XRDP desktop | On a disposable Debian workstation, cover fresh setup, human-first and concurrent starts, supported clients, keyboard/input/clipboard, resize and reconnect, frontend/network interruption, desktop crash/reboot, logout, interrupted setup, legacy-session blocking, and administrator rollback. Confirm the documented persistence and handoff behavior; see [XRDP guidance](workstations/xrdp.md). | **Not run** in full. RC 5 passed native FreeRDP, frontend restart, Xorg crash, and reboot. Other clients and legacy console migration remain open. |
| DSK-02 | Proxmox local browser kiosk | On a disposable Proxmox VE 9 console VM and target laptop, verify CLI/web boot, Firefox navigation, offline saved destinations and tab opening, local status/doctor/start/stop/restart, native/guest consoles, VT switching with a console focused, input/scaling, service stop/restart and child cleanup, crash recovery, package rerun, removal, and current lid/suspend behavior; see the [kiosk checklist](workstations/proxmox-kiosk.md#proxmox-and-hardware-qualification). | **Deferred** to a later release |
| PRV-01 | Privileged executor and Admin refresh lifecycle | On a disposable Debian/systemd VM, cover approval origin, TLS, review window and credential privacy; source activation, setup failures, partial activation, explicit recovery identity and evidence retention. Interrupt the worker/broker or lose a dispatch response; confirm uncertain work is never replayed. Exercise pipe-holding or TERM-ignoring descendants and replaced paths; cleanup must remain bounded. Preserve active builds, unpushed work, T3 and desktop state through maintenance/readiness checks. See [privilege approvals](agents/privilege-approvals.md) and the [broker reference](agents/privilege-broker-reference.md). | **Not run** in full on RC 5. Exact RC 5 passed interrupted source activation recovery, protected preview, and approved refresh with both busy and idle build capacity. Some executor recovery cases were last tested on RC 3. |
| T3-01 | T3 Code server and client | On a disposable Debian VM, qualify stable T3 0.1.10 with a compatible V2 client and supported provider versions. Confirm complete managed skills after restarting the agent session. Retain fresh-install, V1-to-V2 migration, 0.1.9-to-0.1.10 upgrade, pairing, provider/terminal, update/reboot, gateway preview/recording, and direct/Connect evidence. Cover the additional client and server workflows in the [stable feature cases](agents/t3-code.md#stable-feature-qualification). | **Not run** in full. RC 5 passed credential preflight, private HTTPS, Chromium pairing and link reuse refusal, managed service recovery after reboot, and database integrity. Provider/Connect and migration qualification remain open. |
| BRW-01 | Browser qualification | Pass the Guacamole integration boundary in Chromium and Firefox; on disposable Debian, complete the manual Firefox ESR checks and verify internal HTTPS enrollment on a managed deployment. See [browser testing](agents/browser-testing.md#guacamole-boundary) and its [native release procedure](agents/browser-testing.md#native-debian-firefox-esr-release-qualification). | **Pass** for `v2.0.0-rc.5` with 100 Chromium and Firefox Playwright cases, native Debian Firefox ESR, and managed HTTPS enrollment |
| SEC-01 | Required audit coverage | On a required-audit Debian profile, verify `/usr/sbin/auditctl -s`, managed rules from `auditctl -l`, a fresh healthy panel snapshot, and security-monitor collection. Confirm missing tools, stopped auditd, or missing rules are surfaced as source failures. | **Pass** for `v2.0.0-rc.5` on a Debian 13 `server_web` VM |
| TLS-01 | Certificate trust reporting | Exercise a self-signed endpoint with its configured CA, a publicly trusted endpoint, missing CA material, and failed TLS verification. Confirm `basaltwater-web ca --json` and the panel never claim public trust without verification. | **Pass** for `v2.0.0-rc.5` on a Debian 13 `server_web` VM |
| SEC-02 | Security-log retention gaps | Move audit/journal cursors beyond retained source data and verify the missing interval is reported rather than silently treated as clean. If gap detection remains open, record the release risk decision, mitigation, and owner; do not imply that unobserved intervals were checked. | **Pass** for `v2.0.0-rc.5` for gaps before the oldest retained audit, Fail2ban and SSH entries on a Debian 13 `server_web` VM |
| STO-01 | Disk and LVM identity | Exercise mixed retained/new layouts, provider and guest reruns, reboot, missing disks, and interrupted setup. Confirm existing persistent identities are preserved and new identities use Basaltwater names. | **Not run** in full. RC 5 preserved the LVM cache and persistent disk identities across reruns, reboot, and a detached disk and reattachment. A known legacy serial was retained and a changed identity was refused. Interrupted disk setup remains open. |
| PVE-01 | Multi-node Proxmox | Install and rerun on multiple nodes of a disposable cluster, including separate `/run/lock` and `/var/lib` filesystems. Confirm lock exclusion and guest operations across nodes. This does not qualify standalone maintenance, storage-specific health, interrupted updates, guest evacuation, unavailable backup targets, or isolated restore; see [Proxmox operations](operations/proxmox.md). | **Deferred** to a later release |
| STO-02 | NAS, Samba, Gogs, and Syncthing | Qualify setup/reruns, boot ordering, missing mounts, identity preservation, and service-user access on disposable storage. | **Not run** in full on RC 5. Mounted service reruns, boot ordering, failing data mount, identity and directory access, and authenticated Samba read/write passed. Fresh setup passed on RC 3. |
| STO-03 | Storage failure and restoration | Cause sync and scrub failures with disposable data; verify the failure is reported and exercise the documented restore path. A successful mirror alone is not recovery evidence. | **Pass** for `v2.0.0-rc.5` on a Debian 13 VM. Missing backup storage blocked sync, and parity failure protected the mirror until verified restore. |
| APP-01 | HomeBox lifecycle | On a disposable amd64 Debian VM, test reboot and mounted-data recovery; certificate issuance/renewal and firewall reachability; browser login, items, attachments, and WebSockets; setup rerun, upgrade rollback, and full restore. | **Not run** in full. RC 5 passed mounted setup reconciliation, reboot, browser login and access, WebSocket, and verified archive restore. Public certificate issuance, renewal, and firewall reachability require a disposable public HTTPS endpoint. |
| CICD-01 | Signed webhook builds, delivery and rollback | Submit a signed webhook and verify the resulting artifact. Separately authorize delivery of that artifact to an explicitly enrolled target and check its health. Force an unhealthy activation and confirm the previous working release is restored and the failure is reported. Automatic webhook-to-target delivery is deferred. | **Pass** for `v2.0.0-rc.5` on a Debian 13 VM. Both HTTPS routes served the verified signed artifact. Nginx isolated the unhealthy replacement until rollback restored the working release. |
| REL-03 | Prepublication identifiers and assets | Confirm namespace/name clearance, the matching wheel and release notes, planned source/download URLs, and documented upgrade instructions before tagging. | **Not run** in full. RC 5's published wheel, source URLs, and exact-tag upgrade were verified. Stable name clearance and publication sign-off remain open. |

The experimental CachyOS desktop profile is not expanded in this release pass
and is not part of the Debian stable-support gate. No new CachyOS capabilities
or compatibility claims are in scope.

### Result record and prepublication sign-off

For each row, retain a redacted record with:

- case ID, result, operator, and UTC date;
- exact Git tag and commit, package version, host role, OS release, and machine
  type (bare metal, VM, or LXC);
- commands or workflow exercised, expected result, observed result, and any
  recovery performed; and
- a private evidence location and linked issue for failures, if applicable.

Keep raw evidence private when it contains host details or user data. Before
tagging final `v2.0.0`, all required rows must pass, failures must be resolved
and retested, known security-monitor gaps must have an explicit release
decision, and the exact qualified commit must match the tag. Record the final
sign-off here before publication.

### Post-publication verification

After publishing the stable release, record these follow-up checks separately;
they cannot pass before the tag exists:

- [ ] `v2.0.0` points to the exact qualified commit and the GitHub release is
  final, not marked prerelease.
- [ ] The wheel and release notes are attached to the expected release, and
  raw/archive/download URLs resolve to the tagged source.
- [ ] A fresh controller using `stable` selects `v2.0.0` and completes the
  documented install or upgrade path.
