# Test coverage and next checks

Updated 2026-11-17 UTC with selected runtime secret binding qualification. Earlier
updates include selected Headscale/Unbound qualification,
Debian/CachyOS offline media and Intel VA-API qualification, and selected
storage triage/filesystem and shared build/CI
host qualification,
retained live evidence and the owner's stack setup rerun
report, native CachyOS T3 and SDL/OpenGL application checks, current Shares & Data
HTTP/worker, recovery and installed-browser qualification, package-changing Admin
qualification, Proxmox inventory checks, installed native Windows checks and
automated regressions.
The rerun indicates basic functionality only; not all selected features
were tested. Corrected T3 refresh relaunch still needs a live run.
This is a current testing map, not a run log or the
[release qualification gate](../release-qualification.md). Coverage below is
partial: selected live workflows passed, with automated or controlled probes
where stated. CI success does not establish live coverage; repeat affected
checks when behavior changes.

For the dated overview, concise feature table, tested workflow examples and
methodology, start at [Testing status](../testing/README.md). This page retains
the detailed supporting observations and qualification boundaries.
The [dated evidence summaries](../testing/evidence.md) distinguish the latest
owner-reported rerun, Proxmox, USB storage and Shares & Data results by revision
where known and by test level.

Selected bounded storage checks include SSD-backed guests and USB-backed
storage. They do not qualify drive reliability. Keep fault experiments
disposable; disk-wide scans, SMART self-tests and hardware stress/endurance
tests are excluded.

## Surface

The declared surface, regenerated 2026-11-01, has **15 setup profiles, 50 top-level
commands, 312 command paths and 547 distinct long-option spellings**, with aliases collapsed. These
counts describe breadth, not a percentage tested. Regenerate the inventory with:

```bash
.venv/bin/python scripts/inventory_test_surface.py
```

Selected live workflows cover `server_lite`, `server_web`, `server_proxmox` and
`agent_vm` on Debian QEMU, unprivileged LXC and a physical Proxmox node.
The dedicated `server_github_runner` profile also has selected native tokenless
checks and actual registration followed by shared-host conversion, real jobs and
registered reboot checks on Debian QEMU; see the
[dated scope](../testing/evidence.md#shared-debian-build-and-ci-host). The owner also
reports basic setup reruns of `server_lite`, `server_web`, `server_proxmox`,
`agent_code_vm` and `server_github_runner`, partial `agent_cachyos` refresh and
basic non-WSL Windows setup with Godot/T3 Code. The runner command selected
`--ci-register-with-gh`, but no separate registration-state or job result was
supplied. These reports do not extend earlier lab coverage to all selected
features; exact tested revisions were not supplied.
Other profiles, native platforms and machine types remain unqualified as complete
installations; optional combinations need their own checks.

## Next useful checks

For an immediately runnable live-test batch, follow
[Continue testing on Proxmox](../testing/continuing.md): the selected current
Shares & Data HTTP/worker, selected recovery and installed-browser checks are
recorded, as is a real leaf-package Admin upgrade and separately approved no-op.
Selected full-profile Shares & Data refresh and populated rerun also pass.
Remaining client, broader package, other profile and interruption cases keep their
stated boundaries. The priorities below also include implementation and work
requiring additional infrastructure;
they are not all ready to execute on one node.

1. **Shared-host breadth and fault boundaries:** extend the selected native pass
   to organization/multi-repository, further actor/author/event denials, other
   process-loss and cleanup-acknowledgement timings, and retained-store/host
   exhaustion. Selected
   SIGKILL, bounded scratch ENOSPC and separate fresh provider-bootstrap checks
   now pass, as do selected real push/PR actions with nested artifacts and
   zero-step unsupported-action rejection. Public ingress and publication need
   their own scope; see the [shared-host acceptance](cicd-manifest-reuse.md).
2. **Build recovery:** target remaining worker/child teardown, storage exhaustion
   and lost-acknowledgement boundaries as shared adapters land. Preserve guards
   and evidence; automatic retention and generic resume are separate policy.
3. **Longer agent runs:** exercise longer provider-backed goals and remaining
   worker crash/restart boundaries while preserving usage limits and recovery.
4. **Agent administration:** extend the selected leaf-package, conffile/restart
   and failed-hook/recovery cases to distribution service packages, kernel updates
   and broader hook failures on disposable targets; keep interruption separate.
5. **Storage failure handling:** check hard-mounted network storage, faulted and
   partial-device Btrfs diagnostics, native scrub scheduling and an XFS kernel
   with online metadata scrub support. Selected clean native observations and
   reviewed file disposition now pass; retain their narrower scope.
6. **Recovery and developer workflows:** restore backups onto another physical
   host; test real GitHub mirror permissions and additional deployment
   manifests. Account-bound tests need their own explicit scope.
7. **Panel recovery:** extend selected service/exporter and removal ownership
   checks to interrupted setup/removal and power loss.
   Extend host-local HTTP and owned exporter checks to the public setup/production
   teardown lifecycle, and qualify full orphaned-panel teardown. Controller
   transfer investigation is deferred, with captures retained for paired-endpoint
   and controlled connection-close checks; complete controller/browser acceptance
   remains open. Durable unit recovery and
   controlled start-response uncertainty do not complete those wider boundaries.

## Keeping this useful

Update the affected coverage row and priorities in place. Keep detailed commands,
revisions, results, cleanup and artifacts in private evidence records; do not
append batch histories, fix chronologies or personal infrastructure inventories
here. Use [local project memory](../agents/agent-memory.md) to locate private
records; keep account-specific note names and paths out of this shared map.

Use `python3 run_tests.py --list-suites` and the
[contributor checks](../agents/contributing/README.md#standard-checks) to select
automated validation. Browser qualification is separate. Test meaningful option
interactions and failure/rollback paths; accepted parsing or installed packages
alone do not establish working features.

## Choose a topic

<a id="current-coverage"></a>

- [Network, workstation, and agent test coverage](testing-coverage-workstations.md) Review selected networking, workstation, media, and agent results and gaps.

- [Host and storage test coverage](testing-coverage-hosts-and-storage.md) Review selected Proxmox, Linux storage, sharing, and service results and gaps.

- [Build, delivery, and panel test coverage](testing-coverage-delivery-and-panels.md) Review selected deployment, build and CI, panel, and client results and gaps.
