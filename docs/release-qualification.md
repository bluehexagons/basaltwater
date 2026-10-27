# Basaltwater v2.0 preview and release qualification

Status: `pyproject.toml` declares `2.0.0rc1`; the matching GitHub tag
`v2.0.0-rc.1` has not been created or published. The latest final release
remains `v0.2.0`. This document is the qualification record and release gate
for the eventual stable `v2.0.0` release.

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
v2.0.0-rc.1`. A release candidate is for qualification and feedback; it does
not claim that the stable release matrix has passed.

Publishing a preview requires the tag’s Python package version to match the
candidate number and the release CI checks to pass. The package metadata check
compares the version against the exact tag on release builds, including the
final stable tag. This documentation and metadata change does not create a tag
or publish a release. After a failed candidate, fix the issue, increment the
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

- [ ] `pyproject.toml` declares the matching PEP 440 version, such as
  `2.0.0rc1` for `v2.0.0-rc.1`.
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

### Stable qualification matrix

All rows below are required before publishing `v2.0.0`. Record **Pass** only
when the exact candidate commit completed the listed operation and the result
was checked; otherwise use **Fail**, **Blocked**, or **Not run**.

| ID | Qualification case | Required evidence | Status |
| --- | --- | --- | --- |
| REL-01 | Source and wheel | Candidate CI passes; wheel installs outside the source tree; `basaltw --version` and packaged entry points match the candidate metadata. | Not run for `v2.0.0-rc.1` |
| REL-02 | Installer channels | Fresh exact-tag install and upgrade work; `stable` does not select an `-rc.` tag; `dev` still selects `main`. | Not run for `v2.0.0-rc.1` |
| INS-01 | Clean Debian installations | Install controller, server, and agent profiles on disposable Debian hosts; cover the supported bare-metal/VM/LXC paths that each profile supports. Check commands, permissions, and selected services. | Not run for `v2.0.0-rc.1` |
| INS-02 | Existing-install upgrades | Upgrade a supported Basaltwater installation and verify user data, setup state, credentials, and owned runtime paths. Exercise the documented intermediate-version migration for any remaining legacy installation; do not assume direct migration from historical `v0.2.0`. | Not run for `v2.0.0-rc.1` |
| INS-03 | Interruption and recovery | Interrupt migration and setup at documented safe test points. Confirm the operation marker blocks unsafe reruns, recovery guidance identifies retained state, and a successful retry preserves the prior working state. | Not run for `v2.0.0-rc.1` |
| INS-04 | Services and private data | Compare pre/post checksums and modes for preserved private data; validate application access, managed service/timer health, agent configuration, and absence of retired namespace paths. | Not run for `v2.0.0-rc.1` |
| USR-01 | Managed target-user rename | On a disposable Debian/systemd target, rename a managed account through a separate SSH administrator. Verify UID/GID, home contents, SSH access, setup state/cache, managed units, service state, interruption handling, and resume. | Not run for `v2.0.0-rc.1` |
| DSK-01 | Shared XRDP desktop | On a disposable Debian workstation, cover fresh setup, human-first and concurrent starts, supported clients, keyboard/input/clipboard, resize and reconnect, frontend/network interruption, desktop crash/reboot, logout, interrupted setup, legacy-session blocking, and administrator rollback. Confirm the documented persistence and handoff behavior; see [XRDP guidance](workstations/xrdp.md). | Not run for `v2.0.0-rc.1` |
| DSK-02 | Proxmox local browser kiosk | On a disposable Proxmox VE 9 console VM and target laptop, verify CLI/web boot, Firefox navigation, offline saved destinations and tab opening, local status/doctor/start/stop/restart, native/guest consoles, VT switching with a console focused, input/scaling, service stop/restart and child cleanup, crash recovery, package rerun, removal, and current lid/suspend behavior; see the [kiosk checklist](workstations/proxmox-kiosk.md#proxmox-and-hardware-qualification). | Not run; mocked tests do not qualify a physical seat |
| PRV-01 | Privileged executor and Admin refresh lifecycle | On a disposable Debian/systemd VM, cover approval origin, TLS, review window and credential privacy; source activation, setup failures, partial activation, explicit recovery identity and evidence retention. Interrupt the worker/broker or lose a dispatch response; confirm uncertain work is never replayed. Exercise pipe-holding or TERM-ignoring descendants and replaced paths; cleanup must remain bounded. Preserve active builds, unpushed work, T3 and desktop state through maintenance/readiness checks. See [privilege approvals](agents/privilege-approvals.md) and the [broker reference](agents/privilege-broker-reference.md). | Not run for `v2.0.0-rc.1` |
| T3-01 | T3 Code server and client | On a disposable Debian VM, qualify stable T3 0.1.3 with a compatible V2 client and supported provider versions. Confirm complete managed skills after restarting the agent session. Retain fresh-install/V1 migration, pairing, provider/terminal, update/reboot, gateway preview/recording, and direct/Connect evidence. Exercise delegation, workspace binding, queues/recovery, schedules/webhooks, and PR watches using the [stable feature cases](agents/t3-code.md#stable-feature-qualification). | Not run for `v2.0.0-rc.1` |
| BRW-01 | Browser qualification | Pass the Guacamole integration boundary in Chromium and Firefox; on disposable Debian, complete the manual Firefox ESR checks and verify internal HTTPS enrollment on a managed deployment. See [browser testing](agents/browser-testing.md#guacamole-boundary) and its [native release procedure](agents/browser-testing.md#native-debian-firefox-esr-release-qualification). | Not run for `v2.0.0-rc.1` |
| SEC-01 | Required audit coverage | On a required-audit Debian profile, verify `/usr/sbin/auditctl -s`, managed rules from `auditctl -l`, a fresh healthy panel snapshot, and security-monitor collection. Confirm missing tools, stopped auditd, or missing rules are surfaced as source failures.  | Not run for `v2.0.0-rc.1` |
| TLS-01 | Certificate trust reporting | Exercise a self-signed endpoint with its configured CA, a publicly trusted endpoint, missing CA material, and failed TLS verification. Confirm `basaltwater-web ca --json` and the panel never claim public trust without verification.  | Not run for `v2.0.0-rc.1` |
| SEC-02 | Security-log retention gaps | Move audit/journal cursors beyond retained source data and verify the missing interval is reported rather than silently treated as clean. If gap detection remains open, record the release risk decision, mitigation, and owner; do not imply that unobserved intervals were checked. | Not run for `v2.0.0-rc.1` |
| STO-01 | Disk and LVM identity | Exercise mixed retained/new layouts, provider and guest reruns, reboot, missing disks, and interrupted setup. Confirm existing persistent identities are preserved and new identities use Basaltwater names. | Not run for `v2.0.0-rc.1` |
| PVE-01 | Multi-node Proxmox | Install and rerun on multiple nodes of a disposable cluster, including separate `/run/lock` and `/var/lib` filesystems. Confirm lock exclusion and guest operations across nodes. This does not qualify standalone maintenance, storage-specific health, interrupted updates, guest evacuation, unavailable backup targets, or isolated restore; see [Proxmox operations](operations/proxmox.md). | Not run for `v2.0.0-rc.1` |
| STO-02 | NAS, Samba, Gogs, and Syncthing | Qualify setup/reruns, boot ordering, missing mounts, identity preservation, and service-user access on disposable storage. | Not run for `v2.0.0-rc.1` |
| STO-03 | Storage failure and restoration | Cause sync and scrub failures with disposable data; verify the failure is reported and exercise the documented restore path. A successful mirror alone is not recovery evidence. | Not run for `v2.0.0-rc.1` |
| APP-01 | HomeBox lifecycle | On a disposable amd64 Debian VM, test reboot and mounted-data recovery; certificate issuance/renewal and firewall reachability; browser login, items, attachments, and WebSockets; setup rerun, upgrade rollback, and full restore. | Not run for `v2.0.0-rc.1` |
| CICD-01 | CI/CD delivery and rollback | Deliver a signed webhook deployment to an explicitly enrolled target; verify successful health checks, then force an unhealthy activation and confirm the previous working release is restored and the failure is reported. | Not run for `v2.0.0-rc.1` |
| REL-03 | Prepublication identifiers and assets | Confirm namespace/name clearance, the matching wheel and release notes, planned source/download URLs, and documented upgrade instructions before tagging. | Not run for `v2.0.0-rc.1` |

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
