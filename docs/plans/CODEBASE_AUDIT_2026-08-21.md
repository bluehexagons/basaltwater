# Codebase Audit and Maintenance Review (2026-08-21)

Status: historical audit and reference. Resolved findings remain useful
evidence; active command/state residuals are owned by
[transactional execution](TRANSACTIONAL_EXECUTION.md), while deferred webhook
replay and updater-trust decisions remain in the
[roadmap](ROADMAP.md). Do not treat this audit as a separate work queue.

## Scope and verification

This pass covered setup orchestration, persistent state, SSH and host-key
handling, network transitions, Proxmox lifecycle commands, CI/CD webhooks and
deployment, agent updates, transaction/logging helpers, packaging metadata, CI,
and focused tests and documentation.

Verification included:

- `make check`: compilation, CLI documentation and package metadata checks, and
  the full unittest suite passed (2,402 tests, one skipped in the audit run).
- A setuptools wheel build from `pyproject.toml`, followed by extraction into a
  clean directory and an import smoke test.
- Line-level source inspection and repository-wide searches for command failure
  contracts, destructive operations, rollback markers, host-key options, and
  transaction-framework callers.
- Focused reproductions of command redaction with quoted and delimiter-bearing
  values, plus review of source/config staging and release activation failure
  paths.
- Final worktree and whitespace checks after documentation changes.

## Findings

### AUD-01: The declared wheel is not self-contained (resolved)

**Severity: High — release and installation maintenance**

`pyproject.toml` declares `py-modules = ["infra_tools", "remote_setup"]`, while
the entry points import `lib`, `common`, `plugins`, `web`, `security`, and other
source packages. The built wheel contained only the two top-level modules and
metadata. Importing `infra_tools` from the extracted wheel failed with
`ModuleNotFoundError: No module named 'lib'`.

The release workflow runs `make check` and publishes a GitHub release, but it
does not build, install, or smoke-test a wheel. The source-worktree installer
currently masks this packaging defect.

**Resolution (2026-08-21):** package discovery now includes every runtime
package and required desktop/web template, while retaining the two top-level
modules. A clean virtual-environment check inspects the wheel, imports its
runtime packages outside the source tree, and runs both installed launchers.
Tagged-release CI builds, verifies, and attaches that wheel before publication.

### AUD-02: CI/CD deployment targets are enrolled from unauthenticated scans

**Severity: High — host-key trust**

`web/build_server_steps.py` runs `ssh-keyscan` for every deployment target and
appends the result to the service account's `known_hosts` file. The command uses
`check=False`; any successful network response becomes trusted material before a
first production deployment. `docs/CICD.md` asks an operator to verify the
fingerprints afterward, but the trust decision has already been staged.

**Resolution (2026-08-24):** CI/CD setup no longer scans or appends deploy
target keys. Operators must run the explicit workspace enrollment command after
independent fingerprint verification; setup validates that each target has an
enrolled key before enabling deployment. Re-enrollment replaces the matching
host entry and remains an explicit operator action.

### AUD-03: Proxmox node SSH bypasses the workspace known-hosts policy (resolved)

**Severity: High — inconsistent SSH enforcement**

`lib/ssh_utils.py` routes shared SSH/SCP/rsync builders through the workspace
`known_hosts` file with `StrictHostKeyChecking=yes`. The separate
`lib/proxmox_guest.py::_ssh_opts()` helper sets strict checking but does not set
`UserKnownHostsFile`, so Proxmox node connections consult OpenSSH's normal host
files instead of the enrollment store described by the SSH documentation.

**Resolution (2026-08-21):** the Proxmox SSH option builder now uses the shared
workspace `known_hosts` path. Guest provisioning, node probes, and multiplexed
control-socket commands all consume that option list, and focused tests assert
the workspace path with and without a dedicated identity key.

### AUD-04: Target setup state is saved before setup steps execute (resolved)

**Severity: Medium-High — recovery and reconciliation**

`remote_setup.py` writes machine state and the recalled setup configuration
immediately before entering the step loop. If a later step fails, the target
retains the new remembered configuration without a durable in-progress marker or
success record. The controller's setup history does not make that target-local
state safe to replay after interruption.

**Resolution (2026-08-21):** real target setup now writes a versioned operation
marker before its step loop and records each current step. Machine state and
the recalled setup configuration are finalized only after all setup,
deployment, share, and storage work succeeds. Success removes the marker;
ordinary failure marks it `recovery_required`, hard interruption leaves the
last in-progress phase, and dry runs remain state-free. A matching setup rerun
now resumes a `recovery_required` operation under the same ID and reapplies the
idempotent plan, retaining the failed step and error type as recovery context.
An `in_progress`, corrupt, or different-identity marker still blocks automatic
recovery for operator inspection.

### AUD-05: Required setup mutations still have weak failure contracts (partially resolved)

**Severity: Medium-High — partial apply**

Several required paths use best-effort subprocess calls without checking the
result at the owning step. Examples include package/service setup in
`web/web_steps.py`, firewall initialization in `security/security_steps.py`, and
CI/CD prerequisite installation in `web/cicd_steps.py`. This is distinct from
intentional probes and cleanup calls, which should remain explicit
`check=False` operations.

**Resolution progress (2026-08-24):** the named high-impact setup paths now
fail the operation when required work does not complete. Nginx package,
enable/start, ownership, symlink, reload, and configuration-validation failures
propagate after restoring prior configuration where applicable. CI/CD dependency
installation is verified, and its service-user ownership/permission setup no
longer reports success after failure. Deployment, storage, app-server, and
build-server setup callers now use the strict argv-native command contract as
well. Required UFW policy and allow-rule mutations are strict; failed
activation stops non-container setup while the documented container-capability
exception remains best-effort. Focused tests cover these contracts.

**Remaining follow-up:** finish the caller inventory from
[Transactional execution](TRANSACTIONAL_EXECUTION.md), classify each command as
required, optional, probe, or cleanup, and add step-level tests for remaining
required callers outside these setup paths.

### AUD-06: Corrupt state is hidden by permissive readers

**Severity: Medium — operability and drift**

`lib/release_management.py`, `lib/cache.py`, and `lib/machine_state.py` convert
JSON decode/I/O/type failures into `{}`, omission, or automatic detection. The
atomic writer prevents partial replacement, but a deliberately or accidentally
corrupted complete file can still make the system behave as though saved state
never existed.

**Follow-up:** distinguish missing state from invalid state, add schema versions
where compatibility matters, name the affected file in the error, and provide a
documented backup/quarantine/remediation path.

### AUD-07: Signed webhook deliveries can be replayed

**Severity: Medium — deployment reliability and integrity**

The receiver verifies the HMAC and commit fields, but never reads or records
`X-GitHub-Delivery`. Every valid repeat of a delivery creates a new nonce-bearing
job file, so the executor can rebuild and redeploy the same commit more than
once. Consuming each queue file after one attempt does not provide delivery
idempotency.

**Follow-up:** persist bounded delivery IDs (with repository/commit context and
expiry), reject or coalesce repeats, and add a replay regression test. Keep the
existing one-attempt queue consumption behavior as a separate malformed-job
safety measure. **Disposition:** defer until CI/CD deployment work resumes;
this is not part of the current reliability slice.

### AUD-08: Destructive snapshot deletion lacks the CLI confirmation contract

**Severity: Medium — operator safety**

`infra-tools proxmox delsnapshot` validates the snapshot name and executes the
remote deletion, but exposes only `--dry-run`; unlike guest destruction and
orphan-volume deletion it has no `--yes` switch or interactive confirmation.

**Resolution (2026-09-13):** the Proxmox `delsnapshot` CLI and interactive
shell now require an exact interactive `yes` unless `--yes`/`-y` is supplied;
dry runs remain non-mutating. Focused tests cover refusal, approval, dry run,
and the shell confirmation path.

### AUD-09: Agent update trust is recorded, not verified

The Codex agent updater downloads and executes the current script at
`https://chatgpt.com/codex/install.sh`, records its SHA-256, and validates size
and post-update behavior. The digest is an audit record, not an authenticity
check; no pinned digest, signature, or release-channel policy is enforced.

**Follow-up:** define the supported update trust model—vendor signature, pinned
digest manifest, or an explicitly documented rolling-channel exception—and make
the policy visible in the update result and maintenance documentation.

### AUD-10: Command redaction leaks quoted secret suffixes (resolved)

**Severity: High — diagnostic secret exposure**

`lib/remote_utils.py::_redact_command()` redacts only up to whitespace,
semicolons, and pipe characters. It does not parse shell quoting. For example,
`--password 'secret phrase'` is rendered as `--password <redacted> phrase'`,
and a quoted value containing `;` leaves the suffix visible. This affects both
the normal command log and `CommandExecutionError` diagnostics. It is relevant
to generated passwords because the supported password alphabet includes shell
metacharacters.

**Resolution (2026-08-21):** diagnostic redaction now consumes complete
shell-style values without evaluating them. It handles single and double
quotes, escaped whitespace, concatenated word segments, shell delimiters inside
quotes, and unterminated quoted input. Regression tests cover both command and
stderr diagnostics. The argv-native command API now further reduces string
reconstruction for required setup paths, while the demonstrated suffix
disclosure remains closed.

### AUD-11: The shared command runner has no timeout contract (resolved)

**Severity: Medium — hung setup/deployment operations**

`lib/remote_utils.run()` passes no `timeout` to `subprocess.run()`. It is used
by hundreds of setup, deployment, package, service, and network callers, so a
stalled package manager, service action, child process, or shell command can
hold the operation indefinitely. A few specialized paths add their own shell
timeouts, but the shared helper does not expose or enforce a caller-visible
timeout policy.

**Resolution (2026-08-21):** `run()` now applies a one-hour default bound and
accepts an explicit positive timeout or `None` for a deliberately unbounded
operation. Timeouts raise `CommandTimeoutError` regardless of `check`, because
there is no completed result for best-effort callers to inspect. Shell commands
start in a separate session; timeout cleanup sends TERM and then KILL to the
whole process group. Tests cover the default, invalid values, secret-safe
diagnostics, best-effort propagation, and process-group cleanup. Commands known
to require more than an hour must opt into a larger bound at the call site.

### AUD-12: Active agent config staging follows source symlinks (resolved)

**Severity: Medium-High — local file disclosure boundary**

`lib/setup_common.py::_copy_existing_path()` follows a symlink for a file via
`shutil.copy2()` and preserves symlinks while copying directories. The helper
is used for selected active agent configuration such as Codex, Claude, OpenCode,
and GitHub CLI config. The credential-specific path rejects symlink sources,
but the general config path does not, so a user-selected config copy can stage
content outside the expected home/config tree into the remote payload.

**Resolution (2026-08-21):** agent config staging now fails closed when the
selected source file or directory is a symlink, and checks every directory
entry before copying without following links. Non-regular top-level sources are
also rejected. Temporary-directory tests cover symlinked config files,
symlinked config roots, and directory symlinks nested below a real config root.
Credential-specific regular-file validation remains in place.

### AUD-13: Manifest activation proceeds after service-stop failure (resolved)

**Severity: Medium-High — release/process split-brain**

`lib/deployment.py::deploy_manifest()` stops app units with `check=False` and
does not inspect the result before renaming the active release directory. If a
unit cannot be stopped, the old process can remain alive while the service
definition and active tree are changed. The later rollback path also treats
stop/restart failures as best effort, which can leave the operator with a
partially restored process set even when the release tree is restored.

**Resolution (2026-08-21):** manifest deployment now stops existing app units
before the release rename and verifies each unit became inactive. A failed stop
aborts activation; units already stopped earlier in that phase are restarted
and verified against the unchanged release. Post-activation rollback continues
restoring the release tree even when a unit stop fails, verifies every restored
unit restart, and reports incomplete service recovery instead of claiming a
clean rollback. Fault-injection tests cover stop failure before activation,
rollback stop failure, and inactive/active verification failures.

## Positive controls verified

- Shared SSH builders already require strict host-key checking; the remaining
  issue is policy bypass/inconsistent enrollment, not a blanket `accept-new`
  default.
- The live network handoff persists a transition identity and rollback snapshot,
  verifies SSH at the requested address before commit, and aborts on failed
  verification. Its cleanup commands are deliberately best-effort and should
  not be generalized to required setup mutations.
- Proxmox guest destruction and orphan-volume cleanup require explicit
  confirmation, and the network transition has focused identity/rollback tests.
- The callback-based transaction framework was retired after this audit.
  Sync/scrub operations now use explicit fail-fast control flow, while the
  operation logger remains diagnostic evidence. The main setup/deployment paths
  use a small versioned state primitive rather than another callback
  abstraction. Manifest deployment now records its build/activation phases and
  retains incomplete rollback state; target setup records failed steps and
  supports guarded, same-identity recovery reruns under AUD-04.

## Recommended order at the time of this audit

Current ownership and sequencing live in the
[roadmap](ROADMAP.md) and [transactional-execution plan](TRANSACTIONAL_EXECUTION.md).

1. Continue required-command caller classification beyond the completed nginx,
   firewall, and CI/CD prerequisite paths.
2. Add lightweight inspection and rerun guidance for the landed operation
   markers.
3. Make corrupt-state failures actionable without silently resetting state.
4. ~~Add the local package-install smoke test and improve status/`--json` output.~~
   The wheel smoke test now runs as part of local `make check`, and Proxmox
   read-only status/list/health/top/audit commands share the versioned JSON
   envelope used by the provider-neutral VM commands.
5. ~~Complete explicit SSH enrollment for CI/CD targets when that deployment
   path is next maintained.~~ CI/CD target enrollment is now explicit and
   setup fails closed when a target key is missing.
6. ~~Standardize destructive CLI confirmation for snapshot deletion.~~ The
   CLI and interactive shell now share the explicit confirmation boundary;
   re-audit new destructive commands when that surface changes.

Webhook replay protection and a stronger updater trust policy remain valid
follow-ups, but are deferred until their respective workflows justify the
additional state or operator policy.
