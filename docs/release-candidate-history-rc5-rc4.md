# Preview qualification for RC 5 and RC 4

[Preview qualification index](release-candidate-history.md)

## `v2.0.0-rc.5`

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

## `v2.0.0-rc.4`

RC 4 blocks Debian refresh previews and dispatch while a source activation
needs recovery. It also checks T3 device-pairing credentials before target
setup can change packages or services. The Debian release jobs fell back to a
stale version constant because the test containers did not get the release
tag. The workflow stopped before prerelease publication.
[Release workflow](https://github.com/bluehexagons/basaltwater/actions/runs/44425135437)
