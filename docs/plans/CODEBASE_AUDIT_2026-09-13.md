# Residual Codebase Audit and Remediation Plan (2026-09-13)

Status: active handoff plan. This is a concise continuation of the
[2026-08-21 audit](CODEBASE_AUDIT_2026-08-21.md), not a record of completed
fixes. Existing domain plans remain owners where noted below.

## Remediation progress

- RCF-02–03 implemented: one exit rollback guard covers activation failures
  and HUP/INT/TERM at rename boundaries; managed directory checks and explicit
  legacy migration prevent replacement of broad/unmanaged paths. Power-loss
  recovery and bootstrap side-effect limits are documented in Installation.
- RCF-21–24 implemented: initial setup honors mount/SMB failures; diagnostics
  preserve existing files; scrub inventories reject symlinks and abort on scan
  errors before parity work. Hostile concurrent directory replacement remains
  outside the path-based PAR2 contract (see Storage operations).
- Additional scrub fixes: restrict cleanup to exact parity names, verify
  volume-only parity, reject databases containing the source, and propagate
  unsuccessful results through the CLI exit status.
- Findings below retain the original evidence and suggested solutions; entries
  not listed here remain open.

## Review record

- Reviewed installer activation, setup execution, state/cache readers,
  systemd/Nginx generation, CI/CD, credentials, network installers, storage
  and scrub paths, sysadmin/release commands, process timeouts, and destructive
  Proxmox paths. The remaining-pass review also covered plugin discovery and
  composition, desktop/session runtime, T3 pairing/admin endpoints, web-panel
  diagnostics/jobs/audit export, notifications, recall/reconstruction,
  generic service managers, recurring maintenance, publishers, and packaging.
- Baseline was clean and `make check` passed: compilation, docs/package/wheel
  checks, and 3,689 unittest cases (one intentional skip). No live target,
  service, Proxmox host, or deployment was mutated. The optional `coverage`
  package was unavailable.
- Confirmed controls not reopened: strict workspace host-key enrollment,
  bounded job/deploy paths, job-file safety checks, manifest health-gated
  rollback, and atomic JSON replacement. Plugin validation and desktop/session
  lifecycle controls were rechecked; the remaining eager plugin-import concern
  is already owned by the [architectural review](ARCHITECTURAL_RISK_REVIEW_2026-08-07.md).
- Current document revalidation checked every local citation and finding ID;
  RCF-04 now distinguishes deletion-based replacement from in-place overwrite.

## Findings

Priority is delivery order; severity is impact if the condition occurs.
“Carry-forward” means the finding was already known and revalidated.
Finding IDs preserve audit history; priority headings and the delivery order
below are authoritative.

### P0 — address before depending on CI/CD or unattended reinstall

- **RCF-01 — High, carry-forward: CI/CD config permissions can break the
  service.** Initial config creation uses `0644`, while privileged manager
  updates use atomic-writer default `0600`; the `webhook` receiver then cannot
  read its config and reports repositories as unconfigured. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py:141),
  [`webhook_manager.py`](../../web/service_tools/webhook_manager.py:47),
  [`atomic_io.py`](../../lib/atomic_io.py:48),
  [`webhook_receiver.py`](../../web/service_tools/webhook_receiver.py:84).
  **Suggested solution:** define one `root:webhook`/`0640` contract, repair
  existing files, health-check service-user readability, and test distinct
  manager/receiver identities.

- **RCF-02 — High, new: interrupted installer activation can strand the old
  install.** After moving the active tree to backup, `install.sh` activates
  staged content; `HUP/INT/TERM` only exit, while rollback runs only on
  selected ordinary failures. An interruption can leave no active install,
  with recovery directories under `*.backup.*`/`*.new.*`.
  Evidence: [`install.sh`](../../install.sh:580),
  [`install.sh`](../../install.sh:618). **Suggested solution:** model
  activation as a recoverable state machine, restore backup on interrupted
  activation, retain actionable recovery state, and test every boundary where
  the active path is absent.

- **RCF-03 — High, carry-forward: `--install-dir` can replace broad existing
  paths.** Validation requires an absolute path and rejects `/`, but accepts
  directories such as `/opt` or a home directory; the entire path is moved
  aside and a replacement installed. Git-dirty protection only covers existing
  Git worktrees. Evidence: [`install.sh`](../../install.sh:307),
  [`install.sh`](../../install.sh:618). **Suggested solution:** require a
  dedicated parent or managed-install marker; refuse symlinks, mount points,
  and broad unmanaged directories; require explicit confirmation for
  migrations.

### P1 — reliability, state integrity, privileged setup, and operator safety

- **RCF-04 — Medium-High, new: service replacement is destructive or
  non-atomic.** Several writers delete the existing unit before recreating it;
  others overwrite the live unit with `open(..., 'w')`. A write, reload, or
  start failure can therefore leave the service absent or truncated, without
  restoring prior active/enabled state. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py:175),
  [`cicd_steps.py`](../../web/cicd_steps.py:242),
  [`cicd_steps.py`](../../web/cicd_steps.py:264),
  [`cicd_steps.py`](../../web/cicd_steps.py:313),
  [`gogs_steps.py`](../../web/gogs_steps.py:1236),
  [`systemd_service.py`](../../lib/systemd_service.py:252),
  [`service_manager.py`](../../lib/service_manager.py:71),
  [`storage_ops_steps.py`](../../sync/storage_ops_steps.py:70). **Suggested
  solution:** render/validate a same-directory temporary file, atomically
  replace it, preserve the prior unit until activation succeeds, and
  fault-test each failure boundary.

- **RCF-05 — Medium-High, carry-forward: remote setup bypasses bounded
  subprocess execution.** Local and SSH branches use `Popen` followed by
  unbounded `wait()`, unlike the shared runner with descendant cleanup.
  Evidence: [`setup_common.py`](../../lib/setup_common.py:1382),
  [`setup_common.py`](../../lib/setup_common.py:1445),
  [`remote_utils.py`](../../lib/remote_utils.py:256). **Suggested solution:**
  add a setup-specific deadline, process-group termination, streamed output,
  and tests for hangs and descendants holding pipes.

- **RCF-06 — Medium, new: operation markers have no interprocess lock.**
  `begin`, `transition`, and `complete` all use unlocked load-then-write/remove
  sequences. Atomic replacement prevents partial JSON but not two concurrent
  owners or phase overwrites. Evidence:
  [`operation_state.py`](../../lib/operation_state.py:122). **Suggested
  solution:** acquire a non-blocking lock/lease through completion or
  recovery, define stale-lock handling, and add a two-process ownership test.

- **RCF-07 — Medium, carry-forward: corrupt state is treated as missing,
  default state, or an inconsistent exception.** Cache, machine-state, and
  deploy-target readers respectively skip/return `None`, synthesize defaults,
  or return `{}` for some malformed data; valid JSON with the wrong root type
  can instead raise during field access. Evidence: [`cache.py`](../../lib/cache.py:222),
  [`machine_state.py`](../../lib/machine_state.py:185),
  [`remote_deploy.py`](../../lib/remote_deploy.py:48). **Suggested solution:**
  distinguish missing/invalid/unsupported state, quarantine invalid files,
  provide repair guidance, and refuse mutation when required state is invalid.

- **RCF-08 — Medium, carry-forward: valid GitHub deliveries are replayable.**
  HMAC validation does not persist `X-GitHub-Delivery`; replaying a valid
  request creates another job for the same commit. Evidence:
  [`webhook_receiver.py`](../../web/service_tools/webhook_receiver.py:205),
  [`cicd_steps.py`](../../web/cicd_steps.py:381). **Suggested solution:** keep a
  bounded delivery-ID ledger with repository/commit context and expiry, and
  coalesce duplicates.

- **RCF-09 — Medium, carry-forward: existing webhook secrets are not
  reconciled.** Existing secret and environment files are trusted without
  rechecking ownership, mode, non-empty content, or value agreement. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py:101),
  [`cicd_steps.py`](../../web/cicd_steps.py:120). **Suggested solution:** repair
  or fail closed on unsafe files, require a valid single-line secret, compare
  canonical and environment values, and verify before enabling services.

- **RCF-10 — Medium, carry-forward: CI/CD service timeout is shorter than a
  valid pipeline.** `TimeoutStartSec=2h`, but up to four sequential stages can
  each run for one hour. Evidence:
  [`cicd_steps.py`](../../web/cicd_steps.py:271),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:269),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:351).
  **Suggested solution:** choose one total-job deadline, pass remaining time to
  stages, align systemd, and report whether termination occurred before or
  during deployment.

- **RCF-11 — Medium, new: CI/CD repository config has no shared schema
  validator.** Receiver/executor assume arbitrary JSON has the expected root,
  repository, branch, URL, and script shapes; manager accepts these values
  without the shared validators. Malformed config can fail after a job is
  consumed. Evidence: [`webhook_receiver.py`](../../web/service_tools/webhook_receiver.py:249),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:305).
  **Suggested solution:** define a versioned schema shared by manager,
  receiver, executor, and setup; validate before writing/enabling and restrict
  URL/script forms.

- **RCF-12 — Medium, new: secret payload cleanup is not crash-resistant.**
  Remote agent/pairing/web-panel payloads are removed only by normal `finally`
  paths. Hard kill or power loss can leave credentials under
  `/opt/infra_tools`. Evidence: [`remote_setup.py`](../../remote_setup.py:160),
  [`remote_setup.py`](../../remote_setup.py:746). **Suggested solution:** use a
  restrictive temporary location, record expiry/owner, scrub stale payloads
  at startup, and test interruption without exposing secret contents.

- **RCF-13 — Medium, carry-forward: snapshot deletion lacks confirmation.**
  Shell and CLI `delsnapshot` handlers call the deletion helper directly; the
  helper has only `dry_run`, unlike other destructive commands. Evidence:
  [`proxmox_cli.py`](../../lib/proxmox_cli.py:1412),
  [`proxmox_shell.py`](../../lib/proxmox_shell.py:692),
  [`proxmox_manage.py`](../../lib/proxmox_manage.py:926). **Suggested
  solution:** require confirmation or `--yes` for non-interactive use, retain
  dry-run, and test refusal and approval.

- **RCF-19 — Medium-High, new: orphan-volume cleanup is not fail-closed.**
  `_active_vmids` ignores a failed `qm list` or `pct list`, so valid disks can
  be classified as orphaned and deleted with `--delete`; failed `pvesm list`
  calls are also skipped. Evidence:
  [`proxmox_storage.py`](../../lib/proxmox_storage.py:43),
  [`proxmox_storage.py`](../../lib/proxmox_storage.py:102). **Suggested
  solution:** abort on incomplete inventory, show inventory errors in the
  result, require a fresh complete scan before deletion, and return failure on
  any partial cleanup.

- **RCF-20 — Medium-High, new: rolling Proxmox updates lack bounded SSH
  execution and resumable checkpoints.** `_ssh_result` calls
  `subprocess.run` without a timeout; updates then proceed node by node, so a
  later failure leaves earlier nodes changed and later nodes skipped. Evidence:
  [`cluster_update.py`](../../lib/cluster_update.py:34),
  [`cluster_update.py`](../../lib/cluster_update.py:226). **Suggested solution:**
  bound each SSH operation, persist per-target phase/result, make resume and
  stop-after-failure explicit, and apply the existing Proxmox maintenance plan's
  HA/Ceph/evacuation policy before mutation.

- **RCF-21 — High, new: initial sync/scrub setup ignores mount failures.**
  `validate_mount_for_sync` returns `False` for an unmounted `/mnt` path, but
  both setup builders ignore that result and continue through directory creation
  and the initial rsync/parity operation. A missing mount can redirect work to
  the underlying filesystem; rsync's delete mode can then remove the wrong
  files. Evidence: [`sync_steps.py`](../../sync/sync_steps.py:62),
  [`scrub_steps.py`](../../sync/scrub_steps.py:83),
  [`mount_utils.py`](../../lib/mount_utils.py:47). **Suggested solution:**
  fail before any mkdir/write when a required mount check is false, surface the
  mount error in the operation result, and test that initial work is not called.

- **RCF-22 — Medium-High, new: mount diagnostics mutate fixed filenames.**
  SMB and accessibility checks open `.smb_connectivity_test` or
  `.accessibility_test` with `w`, then unlink the path. An existing user file
  can be overwritten and deleted, while interruption can leave a misleading
  artifact. The public connectivity/status checks therefore are not
  guaranteed read-only. Evidence: [`mount_utils.py`](../../lib/mount_utils.py:116),
  [`mount_utils.py`](../../lib/mount_utils.py:202). **Suggested solution:**
  use a unique `O_CREAT|O_EXCL|O_NOFOLLOW` probe, never remove a pre-existing
  name, and make cleanup safe on every exit path.

- **RCF-23 — Medium-High, new: scrub input scope is not symlink-confined.**
  The scrub walk includes file symlinks and passes their paths to PAR2; path
  validation does not reject them, while only the database subtree is excluded
  by resolved path. A link can make parity generation or repair read outside
  the declared directory. Evidence: [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:494),
  [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:507),
  [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:105). **Suggested
  solution:** reject symlink files/directories and enforce real-path
  containment for every source and database operation.

- **RCF-24 — Medium, new: scrub orphan cleanup can act on an incomplete scan.**
  Both directory walks omit an `onerror` handler. If an unreadable source
  subtree is skipped, `existing_files` is incomplete and the later orphan pass
  can delete parity for files that still exist. Evidence:
  [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:250),
  [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:494),
  [`scrub_par2.py`](../../sync/service_tools/scrub_par2.py:574). **Suggested
  solution:** collect scan errors, skip orphan deletion, and fail or report the
  run until a complete inventory succeeds.

- **RCF-25 — Medium, new: internal-web route and preview mutations lack a
  shared transaction lock.** Forward and live-preview commands load state,
  reconcile Nginx/UFW, and write state without interprocess serialization.
  Concurrent add/remove/start/stop commands can lose records, race port
  allocation, or leave generated configuration and state disagreeing. Evidence:
  [`infra_web.py`](../../common/service_tools/infra_web.py:640),
  [`infra_web.py`](../../common/service_tools/infra_web.py:1069),
  [`infra_web.py`](../../common/service_tools/infra_web.py:1643). **Suggested
  solution:** take one root-owned non-blocking lock around load, plan, apply,
  rollback, and state writes for all forward/preview mutations.

- **RCF-26 — Medium-High, new: generated CA bootstrap scripts bypass TLS
  verification.** Linux/macOS snippets use `curl`/`wget --insecure`, and the
  Windows snippet disables certificate validation before checking a digest
  rendered by the same untrusted page. A pre-enrollment MITM can replace both
  the downloaded CA and displayed digest. This conflicts with the independent
  transfer/checksum guidance in [`CLIENT_CA_TRUST.md`](../CLIENT_CA_TRUST.md:48)
  and [`INTERNAL_WEB.md`](../INTERNAL_WEB.md:248). **Suggested solution:**
  remove insecure bootstrap downloads; require a trusted transfer (such as SSH)
  and independently supplied fingerprint, or use TLS only after the client
  already trusts the issuer.

- **RCF-27 — Low-Medium, new: sysadmin convenience commands lack a completion
  timeout contract.** User-facing rsync, health, reachability, fan-out, service,
  mount, and upgrade wrappers call `subprocess.run` without a wall-clock limit.
  SSH connect/keepalive settings do not bound an established remote command or
  a stalled filesystem operation. Evidence: [`sysadmin_transfer.py`](../../lib/sysadmin_transfer.py:101),
  [`sysadmin_health.py`](../../lib/sysadmin_health.py:119),
  [`sysadmin_reachable.py`](../../lib/sysadmin_reachable.py:57),
  [`sysadmin_fan.py`](../../lib/sysadmin_fan.py:45). **Suggested solution:**
  route non-interactive helpers through the bounded process-group runner, report
  timeout as a distinct result, and retain unbounded behavior only for explicit
  interactive SSH/log-follow commands.

- **RCF-28 — Low-Medium, new: channel switching/upgrades run Git without a
  timeout.** `channel_manager` invokes fetch, checkout, and inspection commands
  directly, so a network stall or credential prompt can leave the upgrade CLI
  waiting indefinitely. Evidence: [`channel_manager.py`](../../lib/channel_manager.py:161),
  [`channel_manager.py`](../../lib/channel_manager.py:176). **Suggested solution:**
  use the shared bounded command runner or an explicit process-group timeout,
  then preserve and report the last known channel/worktree state on failure.

- **RCF-29 — Medium-High, new: recall can orphan a timed-out remote installer.**
  The recovery path starts an SSH/tar `Popen` into the live install directory;
  `communicate(timeout=60)` raises without killing or reaping that process.
  The child may remain active and leave a partially extracted remote install.
  Evidence: [`recall.py`](../../lib/recall.py:64),
  [`recall.py`](../../lib/recall.py:79),
  [`recall.py`](../../lib/recall.py:83). **Suggested solution:** start the
  installer in a process group, kill and reap it on timeout, stage the remote
  archive under a temporary/versioned path, and reconcile partial installs.

### P2 — policy and lower-probability operational concerns

- **RCF-14 — Medium, carry-forward: third-party installers use rolling
  network-shell trust.** Codex/Claude/OpenCode, CachyOS, uv, nvm, and the
  Codex updater execute downloaded content; the updater records a digest but
  does not compare it to a pinned expected value. Evidence:
  [`agent_steps.py`](../../common/agent_steps.py:544),
  [`cachyos_steps.py`](../../common/cachyos_steps.py:171),
  [`agent_cli.py`](../../lib/agent_cli.py:657),
  [`common_steps.py`](../../common/common_steps.py:1140). **Suggested solution:**
  select signed releases, a maintained digest manifest, or an explicitly
  accepted rolling channel per tool; expose and retain provenance.

- **RCF-15 — Low-Medium, new: package/service/user probes bypass timeouts.**
  `is_package_installed`, `is_service_active`, and `user_exists` invoke
  `subprocess.run` without a timeout and are widely used before mutations.
  Evidence: [`remote_utils.py`](../../lib/remote_utils.py:417),
  [`remote_utils.py`](../../lib/remote_utils.py:476),
  [`remote_utils.py`](../../lib/remote_utils.py:484). **Suggested solution:**
  use a short probe contract with an explicit “unknown” result and classify
  required, optional, probe, and cleanup callers.

- **RCF-16 — Low-Medium, new: CI/CD logs can collide and queues can grow
  without bound.** Logs are keyed only by commit SHA and truncated on open;
  an age-based cleanup exists, but pending jobs have no age, count, or disk
  budget. Evidence: [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:323),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:613),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:709).
  **Suggested solution:** key logs by repository plus job ID, retain a job-log
  mapping, add backpressure/stale-job policy, and alert on filesystem usage.

- **RCF-17 — Low, new: loopback readiness checks may honor proxies.** Default
  `urllib` openers are used for local health checks without disabling proxy
  environment variables. Evidence: [`deployment.py`](../../lib/deployment.py:1138),
  [`infra_web.py`](../../common/service_tools/infra_web.py:908). **Suggested
  solution:** use a proxy-disabled opener, assert a local response, and test
  with proxy variables set.

- **RCF-18 — High under repository compromise, carry-forward architecture
  risk: CI/CD scripts remain a trust boundary.** Repository-authored scripts
  execute as `webhook` and can stream deploy commands to an app server. HMAC
  protects ingress, not a compromised repository/configuration or an
  over-privileged deploy key. Evidence:
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:233),
  [`cicd_executor.py`](../../web/service_tools/cicd_executor.py:533).
  **Suggested solution:** protect branches, constrain scripts to the checkout,
  separate build/deploy credentials, minimize remote sudo, and document the
  accepted trust model.

- **RCF-30 — Low-Medium, new: T3 Connect expiry is request-triggered.** The
  watcher blocks on provider output, while the 15-minute TTL is checked only
  by `snapshot()`. An abandoned or silent provider can therefore run beyond
  its TTL until another request arrives. Evidence:
  [`device_pairing_service.py`](../../common/service_tools/device_pairing_service.py:31),
  [`device_pairing_service.py`](../../common/service_tools/device_pairing_service.py:331),
  [`device_pairing_service.py`](../../common/service_tools/device_pairing_service.py:420).
  **Suggested solution:** add an autonomous deadline watcher, kill and reap
  the process group on expiry, and test a silent child with no polling.

- **RCF-31 — Low-Medium, new: project publishers lack build command deadlines.**
  Static-site dependency installation/build and Godot export call
  `subprocess.run` without timeouts. The publication lock serializes writers
  but does not bound a hung package manager or exporter. Evidence:
  [`static_web_publish.py`](../../common/service_tools/static_web_publish.py:139),
  [`static_web_publish.py`](../../common/service_tools/static_web_publish.py:147),
  [`godot_web_publish.py`](../../common/service_tools/godot_web_publish.py:387).
  **Suggested solution:** use the shared bounded process-group runner with an
  explicit configurable build deadline, clean up on timeout, and add mocked
  timeout tests.

## Delivery order and ownership

1. **CI/CD gate:** RCF-01, 04, 08–11, and 16. Fix identity/config contracts,
   atomic service replacement, replay handling, queue limits, and tests.
2. **Installer and setup safety:** RCF-02, 03, 05–07, 12, 15, 21, 22, and
   29. Make activation, process waits, markers, state readers, credentials,
   mount gates, diagnostics, probes, and recovery bootstrap recoverable and
   bounded.
3. **Storage integrity:** RCF-23 and 24. Confine scrub scope and make
   inventory/orphan cleanup fail closed.
4. **Proxmox/operator safety:** RCF-13, 19, and 20. Confirm destructive
   actions, fail closed on incomplete inventory, and make rolling updates
   resumable.
5. **Trust/readiness policy:** RCF-14, 17, 18, and 26. Select supply-chain
   policy, isolate loopback checks from proxies, fix CA enrollment, and document
   CI/CD privileges.
6. **Command lifecycle:** RCF-25, 27, 28, 30, and 31. Serialize internal-web
   state mutations and bound remaining sysadmin, pairing, release, and
   publishing commands.

Ownership links:

- RCF-05–07 and 15 extend
  [Transactional execution](TRANSACTIONAL_EXECUTION.md); do not create a
  second execution framework.
- RCF-21–24 extend the storage-operation setup/runtime contract documented in
  [Storage operations](../STORAGE_OPERATIONS.md); keep mount and scrub safety
  checks shared by setup and recurring services.
- RCF-08, 11, 16, and 18 extend
  [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md); RCF-09 and 12 complement
  [Deploy secrets](DEPLOY_SECRETS.md).
- RCF-25 and 26 belong to the internal-web operator contract in
  [Internal web](../INTERNAL_WEB.md) and [client CA trust](../CLIENT_CA_TRUST.md).
- RCF-27 and 28 should reuse the shared SSH/process execution contract rather
  than adding per-command timeout behavior.
- RCF-29 should use that same process contract for recovery/bootstrap, with
  explicit remote staging and rollback semantics.
- RCF-30 and 31 belong to the bounded command lifecycle contract; publisher
  build deadlines should remain separate from publication activation.
- RCF-13, 19, and 20 feed the
  [Proxmox maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md), which
  owns HA/Ceph, evacuation, and live qualification details.
- The [test suite audit](TEST_SUITE_AUDIT_2026-09-02.md) owns coverage
  mechanics; this plan names only the regression cases required by findings.

## Acceptance criteria

- Distinct manager/service identities can read and update CI/CD configuration;
  malformed config and unsafe secrets fail before enablement.
- Interrupted install or setup leaves a usable active install/state or an
  explicit, recoverable failure; all direct mutation processes have a bounded
  wait or documented exception.
- Recall timeout cleanup cannot leave a live installer or partial active tree;
  abandoned T3 Connect and project-build processes are terminated and reaped.
- Concurrent operations cannot overwrite ownership; invalid state cannot
  silently become a fresh mutation; repeated deliveries coalesce.
- Required mounts gate both initial and recurring storage work; diagnostics are
  non-destructive; scrub scans are complete and confined before parity cleanup.
- Internal-web route/preview state changes serialize, and generated CA guidance
  never relies on an unauthenticated download plus same-channel checksum.
- Destructive Proxmox actions require one documented confirmation contract,
  complete inventory, and nonzero status on partial failure.
- Rolling updates expose per-target progress/resume state and policy; tests
  remain local with mocked system calls/temp directories; `make check` and
  `git diff --check` pass for each implementation slice.
