# Basaltwater v2.0 release notes and qualification

The distribution is now `basaltwater` and the primary command is `basaltw`.
Bootstrap and package installation retain `infra-tools` through v2.x. Command
verbs, import modules, saved state, credentials, resource names and API routes
are unchanged. The web panel, documentation and bundled agent guidance use
the new identity. See [migration and rollback](BASALTWATER_MIGRATION.md) before
upgrading; differently named Python distributions require an explicit uninstall
of the old package before installation of the new one.

Repository implementation is complete. Publishing is a separate release step:
the existing `bluehexagons/infra_tools` GitHub repository remains the selected
source host, and no new domain is required. Keeping those URLs avoids depending
on unverified redirects. The optional future repository rename must verify raw
installer, archive, release and updater URLs individually before changing links.

## Evidence delivered

- Fresh wheel build/install and both launchers work outside the source tree.
- A real pre-rename development wheel upgrades and rolls back in an isolated
  virtual environment, with old/new ownership transferred in the correct order.
- Installer tests cover root/user paths, Debian/CachyOS selection, explicit
  custom paths, reruns, failures, recovery, and old/new environment precedence.
- New-name collisions preserve existing commands; older tagged source can
  select its underscore launcher without restoring that alias in v2.
- State and credential content/permissions survive inspection and tested source
  migrations. Existing locks, services, timers and ownership records retain one
  namespace. Current skill text updates through existing reconciliation.
- Web-panel tests, skill validation, theme contrast tests and VM-local browser
  checks cover the changed user-facing surfaces. See [identity guide](BRANDING.md)
  and [contract inventory](plans/BASALTWATER_CONTRACTS.md).

## Before publishing v2.0.0

The maintainer owns these release checks. Do not interpret mocked system calls
or static panel specimens as a live upgrade certification.

- [ ] Use disposable Debian root/controller and non-root/agent VMs, plus a
  CachyOS desktop, to install from the selected release commit. Record the
  exact OS and source versions. No disposable target was supplied and no container
  engine was available in the workspace, so live provisioning is unverified.
- [ ] On each supported upgrade baseline, back up private state outside the
  checkout, record checksums and modes without exposing contents, upgrade using
  the migration guide, rerun, and compare state. Verify both commands, completion,
  managed service health and agent guidance. For older `v0.2.0` source, use the
  current installer; its CLI lacks `channel` and `upgrade`.
- [ ] Exercise an interrupted installer and documented source-backup rollback
  in those disposable systems. Verify services and scheduled jobs still have
  exactly one owner and no duplicate instances. Never perform this on production.
- [ ] Verify old/new controller and target combinations used operationally.
  Retained rename contracts support coexistence; unrelated v2 changes may require
  clean workstation builds as described in [workstations](WORKSTATIONS.md).
- [ ] Confirm control of the PyPI namespace and finish relevant naming/trademark
  checks before public publication. Recorded HTTP lookups are observations,
  not reservations or clearance. No domain purchase is needed for this release.
- [ ] Tag the qualified commit `v2.0.0`, publish reviewed artifacts and these
  notes, and verify the existing installer/raw/archive/download links. Confirm
  `stable` resolves to the intended release after tagging.

The `infra-tools` alias cannot be removed before v3.0. Before retirement, audit
installed automation and skill content, publish removal instructions and verify
that no supported integration still needs it. Persistent identifiers and Python
imports have no automatic removal date.
