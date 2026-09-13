# Residual Codebase Audit and Remediation Plan (2026-09-13)

Status: active second-pass audit. This plan records concerns that remain after
the [2026-08-21 codebase audit](CODEBASE_AUDIT_2026-08-21.md), plus issues
confirmed by a broader review of the current tree. It is an implementation
plan, not a claim that the findings have already been fixed. Existing domain
plans remain the owners for transactional execution, test coverage, Proxmox
maintenance, and deployment secrets.

## Scope and verification

The review covered installer activation and interruption paths, setup
orchestration, durable state and caches, systemd and Nginx generation, CI/CD
webhooks and deployment, credential staging, third-party installers, command
timeouts, and destructive Proxmox commands. It used complete-file inspection
of the affected modules, repository-wide searches for direct process/file
operations and permissive error handling, focused test/source cross-checks,
and a direct reproduction of the atomic writer's default file mode.

The repository was clean at the start of the review. The current baseline
passed `make check`, including Python compilation, documentation and packaging
checks, wheel smoke testing, and 3,689 unittest cases with one intentional
skip. No live target, Proxmox host, system service, or external deployment was
mutated. The optional `coverage` package was not installed in this environment,
so this plan does not use a new coverage percentage as evidence.

The second pass also reconfirmed the following controls and does not reopen
them as findings: shared SSH builders require strict workspace host-key
enrollment, job files reject unsafe file types and sizes, deployment paths are
bounded below their configured base, manifest activation has health-gated
rollback, and the shared JSON writer prevents partial replacement. The gaps
below are mostly at the boundaries around those controls.

## Findings

Severity describes impact if the condition occurs; priority describes the
recommended delivery order.

### P0 — fix before relying on CI/CD or unattended reinstall

#### RCF-01: CI/CD configuration permissions break the service-user workflow

**Severity: High — current functional failure. Carry-forward validation.**

The initial webhook configuration is written with mode `0644` by
[`create_default_webhook_config`](../web/cicd_steps.py:141), but the privileged
`webhook-manager add` and `remove` commands call [`save_config`](../web/service_tools/webhook_manager.py:47),
which uses [`write_json_atomic`](../lib/atomic_io.py:48) with its default mode
`0600`. The receiver and executor run as `webhook` ([`cicd_steps.py`](../web/cicd_steps.py:193)),
so the manager's normal update path changes a readable configuration into a
root-only file. The receiver catches the permission error and returns an empty
configuration ([`webhook_receiver.py`](../web/service_tools/webhook_receiver.py:84));
pushes are then acknowledged as “repository not configured”
([`webhook_receiver.py`](../web/service_tools/webhook_receiver.py:249)).

The audit reproduced the writer's default mode as `0600`. Existing tests round
trip the file under one account and do not verify the service account's access.

**Plan:** define one owner/mode contract, preferably `root:webhook` with
`0640`, repair existing files during setup, fail health checks when the service
cannot read configuration, and add a test that writes as the manager identity
then reads as the service identity.

#### RCF-02: Interrupted installer activation can strand the previous install

**Severity: High — availability and recovery. New in this pass.**

After the current installation is moved to a timestamped backup, the installer
activates the staged source and bootstraps it
([`install.sh`](../install.sh:618)). The rollback function is only called by
selected ordinary error branches. The signal handler is instead
`trap 'exit 1' HUP INT TERM` ([`install.sh`](../install.sh:580)), and the exit
cleanup only removes the download temporary directory. An interrupt between
the backup move and a successful activation therefore leaves no live
`INSTALL_DIR`; the old source remains under `*.backup.*` and the staged source
may remain under `*.new.*`. A hard kill has the same outcome without even
running cleanup.

**Plan:** make activation a single recoverable state machine, install a trap
that restores the backup when activation is incomplete, retain failed/staged
directories with an actionable message, and test interrupts at each boundary
where the active path is absent.

#### RCF-03: `--install-dir` accepts broad existing paths and replaces them

**Severity: High — operator safety and potential data loss. Carry-forward
validation.**

The installer only requires an absolute path and rejects `/`
([`install.sh`](../install.sh:307)). It accepts broad directories such as
`/opt`, `/usr/local`, or a home directory, then moves the entire directory to a
backup and installs the staged repository at that exact path
([`install.sh`](../install.sh:618)). The existing Git-dirty check protects only
directories that already look like Git worktrees. A typo can therefore move
unrelated content out of service and, for a root install, recursively change
ownership of the replacement tree ([`install.sh`](../install.sh:643)).

**Plan:** restrict paths to a dedicated infra-tools parent or require a
verified managed-install marker before replacement. Refuse symlinks, mount
points, and non-leaf broad directories; require an explicit confirmation for
any exceptional migration.

### P1 — reliability, state integrity, and privileged setup

#### RCF-04: Service replacement is cleanup-first and non-atomic

**Severity: Medium-High — service outage after interruption or write failure.
New in this pass.**

[`cleanup_service`](../lib/systemd_service.py:50) stops, disables, and removes
the current unit before a replacement is written. CI/CD then writes service
files directly with `open(..., 'w')` ([`cicd_steps.py`](../web/cicd_steps.py:241),
[`cicd_steps.py`](../web/cicd_steps.py:312)); the Gogs service follows the same
pattern ([`gogs_steps.py`](../web/gogs_steps.py:1236)). A disk-full error,
permission failure, or interruption after cleanup leaves the service absent or
the file truncated, with no restoration of the previously working unit. This
is outside the already improved manifest activation path.

**Plan:** render and validate a replacement in a same-directory temporary
file, atomically install it, preserve the old unit until the new file passes
validation, and record/restore the prior enabled and active state on failure.
Add fault-injection tests for write, daemon-reload, enable, start, and health
failures.

#### RCF-05: `run_remote_setup` bypasses the shared subprocess timeout

**Severity: Medium-High — unattended setup can hang indefinitely. Carry-forward
validation.**

The local and SSH execution branches use direct `subprocess.Popen` and
unbounded `process.wait()` calls ([`setup_common.py`](../lib/setup_common.py:1382),
[`setup_common.py`](../lib/setup_common.py:1445)). The shared runner has a
bounded default and descendant cleanup ([`remote_utils.py`](../lib/remote_utils.py:256)),
but these paths do not use it. A stuck remote command, shell descendant, or
pipe holder can keep the controller waiting forever; SSH connection timeouts
do not bound remote command completion.

**Plan:** add a setup-specific bounded process wrapper with process-group
termination, preserve streamed output, and expose the timeout in diagnostics.
Test local hangs, remote hangs, and descendants that retain output pipes.

#### RCF-06: Operation markers lack an interprocess lock

**Severity: Medium — concurrent setup/deploy state corruption. New in this
pass.**

[`OperationStateStore.begin`](../lib/operation_state.py:122) loads the marker,
checks for absence, and writes a new UUID. [`transition`](../lib/operation_state.py:155)
and [`complete`](../lib/operation_state.py:181) use the same unlocked
load-then-write/remove pattern. Atomic replacement prevents partial JSON but
does not prevent two processes from both observing an empty marker or from
overwriting each other's phase. The global target marker is used by
`remote_setup.py`, so simultaneous invocations on one target can perform
overlapping mutations and make the recorded identity misleading.

**Plan:** acquire a non-blocking operation lock or lease before `begin`, hold
it through completion/recovery, and define stale-lock inspection and release.
Use a two-process test to prove that only one operation can own a marker.

#### RCF-07: Corrupt state is still treated as missing or default state

**Severity: Medium — drift and wrong-target decisions. Carry-forward
validation.**

Malformed cache files are skipped or returned as `None`
([`cache.py`](../lib/cache.py:222)), malformed machine state becomes a default
state ([`machine_state.py`](../lib/machine_state.py:185)), and invalid deploy
target JSON becomes an empty mapping ([`remote_deploy.py`](../lib/remote_deploy.py:48)).
This hides corruption behind a successful-looking “no saved setup,” “default
machine,” or “unknown target” result. In mutation-sensitive callers that can
create new configuration, skip intended maintenance, or select an incorrect
workflow.

**Plan:** distinguish missing, invalid, and unsupported-schema state; include
the exact file and repair action in the error; quarantine invalid files before
recovery; and refuse mutation when required state is invalid. Add tests for
each reader and for the operator recovery path.

#### RCF-08: Valid GitHub webhook deliveries remain replayable

**Severity: Medium — repeated builds/deployments. Carry-forward validation.**

The receiver verifies the HMAC and validates the push payload, but it does not
persist or compare `X-GitHub-Delivery` before creating a new nonce-bearing job
([`webhook_receiver.py`](../web/service_tools/webhook_receiver.py:205)). Nginx
forwards the delivery ID ([`cicd_steps.py`](../web/cicd_steps.py:381)), but the
application discards it. Replaying a captured valid request therefore rebuilds
and can redeploy the same commit.

**Plan:** persist a bounded delivery-ID ledger with repository/commit context
and expiry, coalesce duplicates, and keep queue-file consumption as a separate
malformed-job safety measure. Add a replay regression test.

#### RCF-09: Existing webhook secrets are not re-hardened or reconciled

**Severity: Medium — local disclosure and forged webhook risk. Carry-forward
validation.**

[`generate_webhook_secret`](../web/cicd_steps.py:101) trusts an existing secret
file and returns early when an environment file already exists. It does not
verify ownership, mode, non-empty content, or agreement between the canonical
secret and the environment file. The mode/ownership repair exists only when
the file is newly created ([`cicd_steps.py`](../web/cicd_steps.py:120),
[`cicd_steps.py`](../web/cicd_steps.py:131)). A weakened secret file can be
read by local users and used to forge accepted webhook requests; a stale
environment file can make all legitimate requests fail while setup reports
success.

**Plan:** validate existing files, repair or fail closed on unsafe ownership or
permissions, require a non-empty single-line secret, and compare the canonical
and environment values before enabling services.

#### RCF-10: CI/CD service timeout is shorter than a valid pipeline

**Severity: Medium — partial pipeline/deployment. Carry-forward validation.**

The executor service has `TimeoutStartSec=2h`
([`cicd_steps.py`](../web/cicd_steps.py:271)), while each configured install,
build, test, and deploy operation can run for up to one hour
([`cicd_executor.py`](../web/service_tools/cicd_executor.py:269)). The job loop
can run all of those stages sequentially ([`cicd_executor.py`](../web/service_tools/cicd_executor.py:351)),
so systemd can kill a valid job after two hours, including during remote
deployment.

**Plan:** choose one total-job deadline, pass remaining time to each stage,
align `TimeoutStartSec` with that policy, and report whether termination
occurred before or during deployment. Add a service-unit contract test.

#### RCF-11: CI/CD repository configuration has no shared schema validator

**Severity: Medium — malformed configuration can consume jobs or fail requests.
New in this pass.**

The job payload has a validator, but repository configuration is loaded as
arbitrary JSON by both receiver and executor. Later code assumes that the root
is a dictionary, every repository is a dictionary, branches are iterable, and
scripts are path strings ([`webhook_receiver.py`](../web/service_tools/webhook_receiver.py:249),
[`cicd_executor.py`](../web/service_tools/cicd_executor.py:305)). A malformed
shape can raise during request handling or be caught by the executor after the
job has already been consumed. The privileged manager also accepts URLs,
branches, and script paths without the shared validators.

**Plan:** define a versioned configuration schema and validator shared by
manager, receiver, executor, and setup; restrict repository URL schemes and
script path forms; validate before writing; and expose invalid configuration
in health/status output.

#### RCF-12: Secret payload cleanup is not crash-resistant

**Severity: Medium — credential residue after hard interruption. New in this
pass.**

Remote setup removes uploaded agent, pairing, and web-panel payload directories
only from the normal `finally` path ([`remote_setup.py`](../remote_setup.py:160),
[`remote_setup.py`](../remote_setup.py:746)). A hard kill, power loss, or
abrupt process termination can leave credentials under `/opt/infra_tools` until
another setup happens to clean them. There is no startup scrub of stale
payloads.

**Plan:** use a target-side temporary location with restrictive ownership,
record an expiry/owner for each payload, scrub stale payloads at startup, and
test interruption and cleanup failure without logging secret contents.

#### RCF-13: Destructive Proxmox snapshot deletion lacks confirmation

**Severity: Medium — irreversible operator error. Carry-forward validation.**

`delsnapshot` directly calls the deletion helper
([`proxmox_shell.py`](../lib/proxmox_shell.py:692)), while the management function
offers only `dry_run` ([`proxmox_manage.py`](../lib/proxmox_manage.py:926)). It
does not share the explicit confirmation contract used by guest destruction
and orphan-volume deletion.

**Plan:** require interactive confirmation or `--yes` for non-interactive use,
retain `--dry-run`, and test refusal, approval, and machine-readable behavior.

### P2 — policy and lower-probability operational concerns

#### RCF-14: Third-party installers use rolling network shell trust

**Severity: Medium — supply-chain policy gap. Carry-forward validation.**

Codex, Claude, and OpenCode installation paths pipe vendor URLs to shells
([`agent_steps.py`](../common/agent_steps.py:544)). The CachyOS path downloads
and executes the same class of installer ([`cachyos_steps.py`](../common/cachyos_steps.py:171));
the Codex updater records a digest but does not compare it with a pinned
expected value ([`agent_cli.py`](../lib/agent_cli.py:657)). The uv and nvm paths
also execute downloaded installer content ([`common_steps.py`](../common/common_steps.py:1140),
[`common_steps.py`](../common/common_steps.py:1230)). These are user-scoped in
the reviewed paths, but a compromised upstream, transport proxy, or unexpected
vendor change still becomes code execution on the target account.

**Plan:** select and document a trust model per tool: signed releases, a
maintained digest manifest, or an explicitly accepted rolling channel. Make
the selected mode visible in setup/update output and retain provenance for
each installed artifact.

#### RCF-15: Local package/service/user probes bypass the timeout contract

**Severity: Low-Medium — setup can stall in unusual host environments. New in
this pass.**

`is_package_installed`, `is_service_active`, and `user_exists` call
`subprocess.run` without a timeout ([`remote_utils.py`](../lib/remote_utils.py:417)).
They are used widely before setup mutations. A blocked package database,
systemd call, or NSS-backed `id` lookup can delay setup independently of the
one-hour timeout used by the shared command runner.

**Plan:** route these probes through an explicit short probe contract with
bounded timeout and a clear “unknown” result, then classify each caller as
required, optional, probe, or cleanup under the existing transactional plan.

#### RCF-16: CI/CD log collisions and unbounded queue growth

**Severity: Low-Medium — audit evidence and disk availability. New in this
pass.**

Build logs are named only by commit SHA and opened with truncation
([`cicd_executor.py`](../web/service_tools/cicd_executor.py:323)). The same
commit can occur in multiple repositories, so one job can overwrite another's
log. The executor processes every pending JSON file in sequence
([`cicd_executor.py`](../web/service_tools/cicd_executor.py:709)) without a
queue-size, age, or disk-budget limit. Slow builds and replayed deliveries can
therefore exhaust the CI/CD state filesystem.

**Plan:** name logs with a repository digest plus job ID, retain an explicit
job-to-log mapping, add queue backpressure and stale-job policy, and alert
before the state filesystem reaches a configured threshold.

#### RCF-17: Loopback health checks may honor proxy environment variables

**Severity: Low — environment-dependent readiness. New in this pass.**

The deployment and internal-web readiness checks use the default
`urllib.request.urlopen` opener ([`deployment.py`](../lib/deployment.py:1138),
[`infra_web.py`](../common/service_tools/infra_web.py:908)). Unlike the newer
CachyOS check, they do not explicitly disable proxies. On a host with
`HTTP_PROXY`/`HTTPS_PROXY` and no matching `NO_PROXY`, a proxy can answer or
redirect a loopback request, so readiness may validate the proxy rather than
the local service.

**Plan:** use a proxy-disabled opener for loopback checks, assert the response
host remains local, and add tests with proxy variables set.

#### RCF-18: CI/CD repository-script execution remains a deliberate trust boundary

**Severity: High under a repository-compromise threat model — accepted design
risk, not an unauthenticated webhook bypass. Carry-forward architecture
finding.**

The executor intentionally runs repository-authored scripts and can stream a
deploy script to a configured app server. The service has useful systemd
hardening and runs as `webhook`, but the configured scripts still determine
what that account can do and what remote deployment can change
([`cicd_executor.py`](../web/service_tools/cicd_executor.py:233),
[`cicd_executor.py`](../web/service_tools/cicd_executor.py:533)). HMAC protects
the webhook ingress; it does not protect against a compromised repository,
over-permissive repository configuration, or an over-privileged deploy key.

**Plan:** keep protected branches and repository review as prerequisites,
restrict scripts to the checked-out repository where feasible, separate build
and deploy credentials, minimize remote sudo authority, and document the
accepted trust assumptions before expanding CI/CD manifest reuse.

## Delivery sequence

The implementation should proceed in slices that leave each boundary more
recoverable than before:

| Phase | Scope | Exit criteria |
| --- | --- | --- |
| 1. CI/CD correctness gate | RCF-01, RCF-09, RCF-11, RCF-16, plus the service-file part of RCF-04 | Manager updates remain readable by `webhook`; existing secret/config files are validated; malformed configuration is rejected before enablement; duplicate/large queue behavior is defined; fault-injection tests cover service-file replacement. |
| 2. Installer safety | RCF-02 and RCF-03 | A signal or failed activation leaves either the new managed install or the previous one at the active path; broad/unmanaged targets are refused; shell tests cover backup, rollback, interruption, and state preservation. |
| 3. Setup execution and state | RCF-05, RCF-06, RCF-07, RCF-12, RCF-15 | Local and SSH setup waits are bounded; only one operation owns a target marker; invalid state blocks mutation with remediation; stale secret payloads are scrubbed; probe callers have explicit timeout/failure classes. |
| 4. Operator safety | RCF-10 and RCF-13 | Total CI/CD duration is coherent with systemd; destructive snapshot deletion requires explicit approval; failure and partial-deployment messages identify the recovery action. |
| 5. Trust policy | RCF-08, RCF-14, and RCF-18 | Webhook delivery replay is coalesced; installer provenance policy is selected and visible; protected-branch, credential, and remote-sudo assumptions are documented and tested at their boundaries. |
| 6. Readiness and evidence | RCF-17 and the remaining RCF-16 work | Loopback checks bypass proxies; logs are collision-resistant; queue and disk limits are observable; deployment evidence identifies one immutable job. |

## Acceptance criteria

- The default CI/CD setup, manager update, receiver, and executor are tested
  under their actual distinct identities and file permissions.
- Installer interruption and ordinary failure both preserve a usable active
  installation or leave an unambiguous recovery directory and message.
- Every direct setup process has a bounded wait or an explicit documented
  reason to remain unbounded; process-group cleanup is tested where needed.
- An operation marker has exclusive ownership, and corrupted or stale state
  cannot silently turn into a fresh mutation.
- A repeated valid GitHub delivery creates at most one pending job within the
  configured retention window.
- Destructive CLI commands use one documented confirmation contract.
- Tests remain local, use temporary directories, mock system calls, and do not
  open real deployment or Proxmox connections.
- `make check`, `git diff --check`, and the relevant domain suites pass for
  every implementation slice.

## Ownership and relationship to existing plans

- RCF-05, RCF-06, RCF-07, and RCF-15 extend the active
  [Transactional execution and reconciliation](TRANSACTIONAL_EXECUTION.md)
  project; they should not create a second execution framework.
- RCF-08 and RCF-18 extend the CI/CD boundary covered by
  [CI/CD manifest reuse](CICD_MANIFEST_REUSE.md).
- RCF-09 and RCF-12 complement [Deploy secrets](DEPLOY_SECRETS.md).
- RCF-13 remains an input to the [Proxmox maintenance audit](PROXMOX_MAINTENANCE_AUDIT_2026-08-09.md).
- The [test suite audit](TEST_SUITE_AUDIT_2026-09-02.md) owns the coverage
  mechanics; this plan adds only the security/reliability cases needed to
  validate these findings.

Until the corresponding implementation and acceptance criteria land, the
findings should remain visible in release and deployment readiness reviews.
