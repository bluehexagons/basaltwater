# Basaltwater v2.0 release notes and qualification

Basaltwater is a complete repository and runtime rename. The distribution is
`basaltwater`, the Python entry point is `basaltwater.py`, and the command is
`basaltw`. Internal callers, install/staging paths, state directories, services,
accounts, locks, skills, generated configuration, and deployment manifests use
the new namespace. The gateway command is `basaltwater-web`.

Setup automatically performs the [one-time migration](BASALTWATER_MIGRATION.md)
on recent infra-tools target installations before continuing. Standalone and
controller migrations retain the explicit `basaltw migrate` command.
There are no persistent old-name aliases, environment fallbacks, or historical
release support after cutover. The existing `bluehexagons/infra_tools` GitHub
location remains authoritative until the owner renames the repository.

## Repository verification

- The default suite exercises the renamed callers, setup plans, service
  configuration, authentication, agent integrations and state handling.
- Migration fixtures cover read-only preview, private data preservation,
  disjoint-directory merging, collision/symlink rejection, skill replacement,
  service cutover, and recovery after a failed service start.
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
