# Basaltwater v2.0 release notes and qualification

Basaltwater replaces the repository and runtime namespace. The distribution is
`basaltwater`, the Python entry point is `basaltwater.py`, and the command is
`basaltw`. Internal callers, install/staging paths, state directories, services,
accounts, locks, skills, generated configuration, and deployment manifests use
the new namespace. The gateway command is `basaltwater-web`. New disk serials
and cache volume groups use Basaltwater names; existing storage retains its
[durable identities](plans/BASALTWATER_CONTRACTS.md#persistent-storage-identities).

Setup automatically performs the [one-time migration](BASALTWATER_MIGRATION.md)
on recent infra-tools target installations before continuing. Standalone and
controller migrations retain the explicit `basaltw migrate` command for the
remaining user data; default controller configuration reads migrate recent
default client files automatically, including saved hosts.
There are no persistent old-name aliases, environment fallbacks, or historical
release support after cutover. The existing `bluehexagons/infra_tools` GitHub
location remains authoritative until the owner renames the repository.

## Repository verification

- The default suite exercises the renamed callers, setup plans, service
  configuration, authentication, agent integrations and state handling.
- Migration fixtures cover read-only preview, private data preservation,
  disjoint-directory merging, collision/symlink rejection, skill replacement,
  service cutover, and recovery after a failed service start.
- Migration regression tests also cover active provisioning locks, duplicate
  legacy/canonical lock names, cross-filesystem lock retirement and recovery,
  and automatic recovery of the narrowly identified interrupted lock cutover.
- Installer tests cover new root/user installations, Debian/CachyOS selection,
  custom destinations, source activation failure and interruption recovery.
- The fresh wheel is built, installed and exercised outside the source tree.
  Packaging rejects retired entry modules and skill IDs.
- `make check` validates CLI docs, metadata, generated brand assets and tests.
  Visual identity and contrast checks remain described in [BRANDING.md](BRANDING.md).

## Before publishing v2.0.0

The maintainer owns these release checks. Mocked service operations and static
panel specimens do not certify live provisioning or migration.

- [ ] Qualify clean installation and migration from the recent pre-rename
  development baseline on disposable Debian controller/server/agent VMs and a
  CachyOS desktop. Record the exact source and OS versions.
- [ ] Compare private data checksums and modes, validate application access,
  service/timer health, agent configuration and completion after both system
  and user passes. Confirm that old units, launchers and data paths are gone.
- [ ] Interrupt a migration on disposable systems and exercise journal-based
  recovery. Verify no duplicate scheduled jobs and no concurrently active
  old/new lock namespaces. Do not perform this qualification on production.
- [ ] Qualify retained and new disk/LVM identities on disposable hosts: test
  mixed layouts, provider/guest reruns, reboot, missing disks, and interrupted setup.
- [ ] Requalify migration and setup on multiple nodes of a disposable Proxmox
  cluster, including separate `/run/lock` and `/var/lib` filesystems. Confirm
  that a rerun on the first node and subsequent nodes preserves lock exclusion.
- [ ] Qualify NAS and Samba + Gogs + Syncthing setup/reruns, boot ordering,
  missing mounts, Syncthing identity preservation and service-user access.
  Exercise sync/scrub failures and restoration using disposable data; a
  successful mirror is not evidence of application-consistent recovery.
- [ ] Confirm namespace ownership and naming clearance before public publication.
- [ ] Tag the qualified commit `v2.0.0`, publish artifacts and these notes, and
  verify installer/raw/archive/download URLs and `stable` channel selection.
- [ ] When the repository is renamed later, update its URLs together and verify
  every source-download and updater path; do not assume redirects suffice.

## Live agent VM audit — 2026-09-19

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
