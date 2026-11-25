# Stable qualification matrix

[Documentation index](release-qualification.md)

### Stable qualification matrix

Complete all rows except those marked **Deferred** before publishing `v2.0.0`.
Record **Pass** only when the exact candidate commit completed the listed
operation and the result was checked. Use **Fail**, **Blocked**, or **Not run**
for other outcomes.
Results recorded for earlier tags still need qualification on the current
candidate.

| ID | Qualification case | Required evidence | Status |
| --- | --- | --- | --- |
| REL-01 | Source and wheel | Candidate CI passes; wheel installs outside the source tree; `basaltw --version` and packaged entry points match the candidate metadata. | **Pass** for `v2.0.0-rc.6` |
| REL-02 | Installer channels | Fresh exact-tag install and upgrade work; `stable` does not select an `-rc.` tag; `dev` still selects `main`. | **Pass** for `v2.0.0-rc.6` on Debian 13 VM |
| INS-01 | Clean Debian installations | Install controller, server, and agent profiles on disposable Debian hosts; cover the supported bare-metal/VM/LXC paths that each profile supports. Check commands, permissions, and selected services. | **Not run** in full. Fresh Debian 13 controller, `agent_vm`, and unprivileged `server_web` LXC installations passed on RC 6. Bare-metal installations require a disposable physical host. |
| INS-02 | Existing-install upgrades | Upgrade a supported Basaltwater installation and verify user data, setup state, credentials, and owned runtime paths. Exercise the documented intermediate-version migration for any remaining legacy installation; do not assume direct migration from historical `v0.2.0`. | **Pass** for `v2.0.0-rc.3` controller and Debian server upgrades, including pinned intermediate user and system cutovers. |
| INS-03 | Interruption and recovery | Interrupt migration and setup at documented safe test points. Confirm the operation marker blocks unsafe reruns, recovery guidance identifies retained state, and a successful retry preserves the prior working state. | **Not run** in full on RC 6. Hard setup interruption, refusal, recovery and retry passed on a Debian 13 `agent_vm`. Both pinned intermediate user and system cutovers recovered from SIGKILL and completed on a fresh apply during RC 3 qualification. |
| INS-04 | Services and private data | Compare pre/post checksums and modes for preserved private data; validate application access, managed service/timer health, agent configuration, and absence of retired namespace paths. | **Not run** in full. Private data, live services and timers, and retirement of the legacy runtime passed after Debian server migration and `v2.0.0-rc.3` setup. Broader agent configuration preservation remains open. |
| USR-01 | Managed target-user rename | On a disposable Debian/systemd target, rename a managed account through a separate SSH administrator. Verify UID/GID, home contents, SSH access, setup state/cache, managed units, service state, interruption handling, and resume. | **Pass** for `v2.0.0-rc.6` on Debian 13 LXC, including verified resume and reboot |
| DSK-01 | Shared XRDP desktop | On a disposable Debian workstation, cover fresh setup, human-first and concurrent starts, supported clients, keyboard/input/clipboard, resize and reconnect, frontend/network interruption, desktop crash/reboot, logout, interrupted setup, legacy-session blocking, and administrator rollback. Confirm the documented persistence and handoff behavior; see [XRDP guidance](workstations/xrdp.md). | **Not run** in full. RC 5 passed native FreeRDP, frontend restart, Xorg crash, and reboot. Other clients and legacy console migration remain open. |
| DSK-02 | Proxmox local browser kiosk | On a disposable Proxmox VE 9 console VM and target laptop, verify CLI/web boot, Firefox navigation, offline saved destinations and tab opening, local status/doctor/start/stop/restart, native/guest consoles, VT switching with a console focused, input/scaling, service stop/restart and child cleanup, crash recovery, package rerun, removal, and current lid/suspend behavior; see the [kiosk checklist](workstations/proxmox-kiosk.md#proxmox-and-hardware-qualification). | **Deferred** to a later release |
| PRV-01 | Privileged executor and Admin refresh lifecycle | On a disposable Debian/systemd VM, cover approval origin, TLS, review window and credential privacy; source activation, setup failures, partial activation, explicit recovery identity and evidence retention. Interrupt the worker/broker or lose a dispatch response; confirm uncertain work is never replayed. Exercise pipe-holding or TERM-ignoring descendants and replaced paths; cleanup must remain bounded. Preserve active builds, unpushed work, T3 and desktop state through maintenance/readiness checks. See [privilege approvals](agents/privilege-approvals.md) and the [broker reference](agents/privilege-broker-reference.md). | **Not run** in full on RC 5. Exact RC 5 passed interrupted source activation recovery, protected preview, and approved refresh with both busy and idle build capacity. Some executor recovery cases were last tested on RC 3. |
| T3-01 | T3 Code server and client | On a disposable Debian VM, qualify stable T3 0.1.12 with a compatible V2 client and supported provider versions. Confirm complete managed skills after restarting the agent session. Retain fresh-install, V1-to-V2 migration, 0.1.11-to-0.1.12 upgrade, pairing, provider/terminal, update/reboot, gateway preview/recording, and direct/Connect evidence. Cover the additional client and server workflows in the [stable feature cases](agents/t3-qualification.md#stable-feature-qualification). | **Not run** in full. RC 5 passed credential preflight, private HTTPS, Chromium pairing and link reuse refusal, managed service recovery after reboot, and database integrity. [0.1.12 standalone checks](agents/t3-releases/0.1.12.md#verification) passed separately. Provider/Connect and V1-to-V2 migration qualification remain open. |
| BRW-01 | Browser qualification | Pass the Guacamole integration boundary in Chromium and Firefox; on disposable Debian, complete the manual Firefox ESR checks and verify internal HTTPS enrollment on a managed deployment. See [browser testing](agents/browser-testing.md#guacamole-boundary) and its [native release procedure](agents/browser-testing.md#native-debian-firefox-esr-release-qualification). | **Pass** for RC 6 with 100 Chromium and Firefox Playwright cases, native ESR checks, and CA enrollment on the installed gateway |
| SEC-01 | Required audit coverage | On a required-audit Debian profile, verify `/usr/sbin/auditctl -s`, managed rules from `auditctl -l`, a fresh healthy panel snapshot, and security-monitor collection. Confirm missing tools, stopped auditd, or missing rules are surfaced as source failures. | **Pass** for `v2.0.0-rc.6` on a Debian 13 `server_web` VM |
| TLS-01 | Certificate trust reporting | Exercise a self-signed endpoint with its configured CA, a publicly trusted endpoint, missing CA material, and failed TLS verification. Confirm `basaltwater-web ca --json` and the panel never claim public trust without verification. | **Pass** for `v2.0.0-rc.6` on a Debian 13 `server_web` VM |
| SEC-02 | Security-log retention gaps | Move audit/journal cursors beyond retained source data and verify the missing interval is reported rather than silently treated as clean. If gap detection remains open, record the release risk decision, mitigation, and owner; do not imply that unobserved intervals were checked. | **Pass** for `v2.0.0-rc.6` for gaps before the oldest retained audit, Fail2ban and SSH entries on a Debian 13 `server_web` VM |
| STO-01 | Disk and LVM identity | Exercise mixed retained/new layouts, provider and guest reruns, reboot, missing disks, and interrupted setup. Confirm existing persistent identities are preserved and new identities use Basaltwater names. | RC 6 passed interrupted disk addition, explicit recovery, rerun and reboot with the original and added disk identities intact. The detached-disk and retained legacy serial cases passed on RC 5. |
| PVE-01 | Multi-node Proxmox | Install and rerun on multiple nodes of a disposable cluster, including separate `/run/lock` and `/var/lib` filesystems. Confirm lock exclusion and guest operations across nodes. This does not qualify standalone maintenance, storage-specific health, interrupted updates, guest evacuation, unavailable backup targets, or isolated restore; see [Proxmox operations](operations/proxmox.md). | **Deferred** to a later release |
| STO-02 | NAS, Samba, Gogs, and Syncthing | Qualify setup/reruns, boot ordering, missing mounts, identity preservation, and service-user access on disposable storage. | **Pass** for RC 6 on a Debian 13 VM. Fresh setup, rerun, reboot, failing data mount, service recovery, identity and directory access, and authenticated Samba read/write passed. |
| STO-03 | Storage failure and restoration | Cause sync and scrub failures with disposable data; verify the failure is reported and exercise the documented restore path. A successful mirror alone is not recovery evidence. | **Pass** for `v2.0.0-rc.6` on a Debian 13 VM. Missing backup storage blocked sync, and parity failure protected the mirror until verified restore. |
| APP-01 | HomeBox lifecycle | On a disposable amd64 Debian VM, test reboot and mounted-data recovery; certificate issuance/renewal and firewall reachability; browser login, items, attachments, and WebSockets; setup rerun, upgrade rollback, and full restore. | **Deferred** to a later release. RC 6 evidence covers mounted setup, reboot, mount-failure recovery, browser workflows and archive restore. Public HTTPS and upgrade rollback remain open. |
| CICD-01 | Signed webhook builds, delivery and rollback | Submit a signed webhook and verify the resulting artifact. Separately authorize delivery of that artifact to an explicitly enrolled target and check its health. Force an unhealthy activation and confirm the previous working release is restored and the failure is reported. Automatic webhook-to-target delivery is deferred. | **Pass** for `v2.0.0-rc.5` on a Debian 13 VM. Both HTTPS routes served the verified signed artifact. Nginx isolated the unhealthy replacement until rollback restored the working release. |
| REL-03 | Prepublication identifiers and assets | Confirm namespace/name clearance, the matching wheel and release notes, planned source/download URLs, and documented upgrade instructions before tagging. | **Not run** in full. The RC 6 wheel and source URLs were verified. Stable name clearance and publication sign-off remain open. |

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
