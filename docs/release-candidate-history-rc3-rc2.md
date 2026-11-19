# Earlier preview qualification: RC 3 and RC 2

[Documentation index](release-qualification.md) · [RC 5 and RC 4](release-candidate-history.md)

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
matrices, shared desktop and T3 checks, storage recovery, and signed delivery.
HomeBox, multi-node Proxmox, and kiosk qualification are deferred to a later
release.

Bare-metal installation needs a disposable physical Debian host.
Authenticated T3 provider, Connect and native-client cases need disposable
accounts and compatible clients. Stable publication also requires the
release owner's identifier and asset sign-off.
