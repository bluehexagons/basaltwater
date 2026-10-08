# Basaltwater v2.0 preview and release qualification

Status (2026-10-08): the source is moving to a release-candidate preview
series. `pyproject.toml` declares `2.0.0rc1`; the matching GitHub tag
`v2.0.0-rc.1` has not been created or published. The latest final release
remains `v0.2.0`. This document is the qualification record and release gate
for the eventual stable `v2.0.0` release.

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
[durable identities](plans/BASALTWATER_CONTRACTS.md#persistent-storage-identities).

The infra-tools cutover is complete for the managed fleet. Its migration engine
and automatic client/target migrations are retired. Any remaining old
installation or interrupted cutover requires the pinned
[intermediate version](BASALTWATER_MIGRATION.md) before current setup.
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
  Visual identity and contrast checks remain described in [BRANDING.md](BRANDING.md).

## Qualification protocol

Run live cases only on disposable systems and data. Mocked system calls,
fixtures, static panel specimens, and the historical agent VM audit below do
not qualify a release candidate. Test the exact candidate commit and retain a
short, reviewed result for every required case. Never commit credentials,
private hostnames, personal data, or raw logs that contain them.

### Preview publication gate

- [ ] `pyproject.toml` declares the matching PEP 440 version, such as
  `2.0.0rc1` for `v2.0.0-rc.1`.
- [ ] Release CI passes on Python 3.10 and 3.14, builds the wheel, and
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
| SEC-01 | Required audit coverage | On a required-audit Debian profile, verify `/usr/sbin/auditctl -s`, managed rules from `auditctl -l`, a fresh healthy panel snapshot, and security-monitor collection. Confirm missing tools, stopped auditd, or missing rules are surfaced as source failures. Issue #102's originally affected-host check remains separately tracked. | Not run for `v2.0.0-rc.1` |
| TLS-01 | Certificate trust reporting | Exercise a self-signed endpoint with its configured CA, a publicly trusted endpoint, missing CA material, and failed TLS verification. Confirm `basaltwater-web ca --json` and the panel never claim public trust without verification. Issue #103's originally affected-host check remains separately tracked. | Not run for `v2.0.0-rc.1` |
| SEC-02 | Security-log retention gaps | Move audit/journal cursors beyond retained source data and verify the missing interval is reported rather than silently treated as clean. If gap detection remains open, record the release risk decision, mitigation, and owner; do not imply that unobserved intervals were checked. | Not run for `v2.0.0-rc.1` |
| STO-01 | Disk and LVM identity | Exercise mixed retained/new layouts, provider and guest reruns, reboot, missing disks, and interrupted setup. Confirm existing persistent identities are preserved and new identities use Basaltwater names. | Not run for `v2.0.0-rc.1` |
| PVE-01 | Multi-node Proxmox | Install and rerun on multiple nodes of a disposable cluster, including separate `/run/lock` and `/var/lib` filesystems. Confirm lock exclusion and guest operations across nodes. | Not run for `v2.0.0-rc.1` |
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

## Live agent VM audit — 2026-09-19

The following records describe the cutover-era checkouts and remaining work at
that time. The fleet has since completed migration; the code and retry behavior
described below are retained only in the pinned intermediate version.

After the operator reran setup with commit `03298b0`, a read-only audit of the
Debian agent VM (kernel `6.12.107+deb13-cloud-amd64`) confirmed:

- The user migration journal is complete. The old runtime and default user
  config/share/state directories are absent.
- All 14 registered Git checkouts resolve at their current paths, including
  the relocated managed worktrees.
- Codex 0.155.1 subscription credentials are current. T3 0.0.42 service,
  pairing, native runtime, Git identity and GitHub integration checks pass.
- Maintenance timers report successful runs; no system units are failed.
  Managed unit/launcher scans found no old runtime paths or broken unit links.
- The managed browser smoke test passes. Go, Node and Godot toolchain checks
  pass, including Godot export templates.

The host reports retained swap usage above 25%, with about 2.6 GiB available
RAM and no active swapping in the short sample. A failed user
`xfce4-notifyd.service` reports no display; graphical desktop operation was not
tested or changed. Active T3 sessions were not restarted. Service checks do
not establish successful provider prompts or client-side previews.

This is post-setup evidence for one agent VM, not full release qualification:
pre-migration private-data checksums, live interruption/recovery, fresh installs,
and the other platform combinations above remain outstanding. No package
publication, repository rename or domain purchase was performed.

### Follow-up after setup from `9b4230d`

The installed source metadata identifies a clean snapshot of `9b4230d`.
All-capability doctor checks pass for the host, managed browser smoke test,
development toolchains, installed agent clients and T3. The user migration
preview has no remaining actions; its completed journal and both independent
repository trust entries are retained. Managed system services are healthy,
the web-panel backend returns `ok`, and loopback HTTPS routes respond with
success or the expected authentication challenge. These route probes do not
certify client-side TLS trust or provider prompts.

The audit found matching old and canonical journald drop-ins. The follow-up
code includes journald and zram-generator settings in initial migration and
archives matching legacy duplicates on setup retries after a completed
migration. Different values stop setup before runtime replacement. Tests also
verify that unfinished unit-replacement markers block migration and that
hidden recovery backups remain unchanged. `make check` passes with 4,095 tests
run and two skipped, including the new migration and retry regressions.

The VM still needs a setup rerun with this follow-up code to archive its old
journald file. Its idle old unit-operation lock is inert, and the previously
reported XFCE notifier failure still reports no display. This audit did not
restart the desktop or T3. Root-only configuration validation was unavailable
through this session's sudo access. The broader release matrix remains open.
