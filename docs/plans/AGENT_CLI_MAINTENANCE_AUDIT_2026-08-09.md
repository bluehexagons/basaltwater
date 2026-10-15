# CLI-Only Agent Host and Maintenance Audit (2026-08-09)

Status: active follow-up plan. Small correctness and documentation fixes from
this audit landed with the document. The first explicit tool-update slice has
also landed; version policy, workload-aware restarts, and fleet observability
still require larger design work. The narrow `agent_vm` and
`agent_workstation` profile shorthands have also landed.

This document retains audit-time observations and former command names. Use
the current [maintenance contract](../operations/MAINTENANCE.md) and `basaltw` references
for operations. The 2026-10-12 Antistatic slice delivers ballooning-aware memory
interpretation, readable OOM counters without cgroup paths, bounded cache/cleanup and
maintenance-review captures, and identity-checked private disposal through the
existing owners. Fixture coverage does not qualify live setup or service
recovery; the [disposable matrix](PRIVILEGE_EXECUTION_QUALIFICATION.md) remains
pending, and historical failed requests are not replayed.

The optional installation-context manifest now compares invoked and managed
source with activation/snapshot receipts, retaining missing revisions as unknown.
CachyOS adds successful setup source and managed-web service identity without
inferring loaded code or replacing developer launchers. Use the
[source-context contract](../agents/PROJECT_TOOLING.md#installed-cli-and-source-context)
for custom managed roots and comparison limits. Local fixtures/workstation checks
do not qualify Debian service revision or interrupted activation/recovery.

## Scope and recommended baseline

This review traced the terminal-only agent path from argument normalization and
plugin step selection through tool installation, config/credential staging,
repository caching, agent diagnostics, security hardening, and recurring
systemd jobs.

The recommended CLI-only profile is now:

```bash
infra-tools setup agent_vm 10.0.0.10 agent \
  --agent-config active --git-access read \
  --repo https://github.com/user/project.git
```

`agent_vm` supplies the normal firewall and CLI profile plus GitHub CLI and
Codex. `server_lite` omits the firewall and generic CLI bundle, so it should be
selected only when that lighter security/packaging boundary is intentional.

An explicit `--agent-tool` list replaces the profile's GitHub CLI and Codex
defaults, allowing any supported provider combination. No language runtime is
implicit; add packages and runtimes with their individual flags.

## Maintenance observed at audit time

On a Debian VM or bare-metal `agent_vm` target, the terminal-agent setup
inherits these host jobs:

| Unit | Behavior | Agent-specific effect |
| --- | --- | --- |
| `security-monitor.timer` | Reads fail2ban, auditd, and SSH events every 15 minutes | Events are logged locally even without `--notify`; configured targets also receive noteworthy events and collection failures |
| `auto-update-apt.timer` | Runs a daily non-removing distribution upgrade | Updates GitHub CLI and the Debian coding-tool baseline through APT |
| `cleanup-maintenance.timer` | Cleans bounded journals, APT caches, and strictly named stale temp artifacts weekly | Root-only system cleanup no longer invokes user package-manager commands against the wrong home |
| `user-cache-maintenance.timer` | Inventories and prunes bounded developer-tool caches weekly as the configured non-root user | Covers npm, pip, uv, Go, OpenCode cache data, Codex cache data, and stale Codex temporary entries while preserving persistent agent state |
| `auto-restart-if-needed.timer` | Checks daily and after boot for `/var/run/reboot-required` | Defers for active login sessions until the force deadline, then may restart despite active work |

Selected terminal agents do **not** install an automatic updater for Codex CLI,
Claude Code, or OpenCode. Their setup functions skip installation whenever the
command is already found. `infra-tools agent update` now provides an explicit
per-user update with pre/post smoke checks, an atomic non-secret audit record,
and automatic executable rollback when verification fails. `infra-tools agent
doctor` reports presence, path, version, and credential-file presence, but it
does not yet determine freshness or timer health.

Container capability checks may skip firewall, fail2ban, kernel, auditd,
AppArmor, security-monitor, and restart behavior. See the machine-type matrix
for the exact target-specific boundary.

## Findings addressed in this audit

### AGT-01: The documented bootstrap used the lightweight server profile

The install and command-reference bootstrap examples used `server_lite`, even
though the agent documentation identified `server_dev` as the terminal-only
development profile. That path omitted the standard firewall and generic CLI
steps. The examples first moved to `server_dev`; they now use `agent_vm`, which
keeps that server boundary while adding the narrow GitHub CLI and Codex
defaults. The profile difference remains explicit in the agent documentation.

### AGT-02: Security monitoring was a no-op without notification targets

`security_monitor.main()` returned before querying fail2ban, auditd, or the SSH
journal when no `--notify` target existed. A default agent VM therefore ran a
timer every 15 minutes without monitoring anything. The monitor now always
collects and records results locally; notification delivery remains optional.

### AGT-03: Missing default maintenance timers did not fail setup

The shared timer installer verifies daemon reload, enablement, and active state,
but the security monitor, cleanup, and restart callers ignored a failed result.
The APT caller preserved distro timers but also allowed setup to report success.
These four default-host callers now raise after failed verification. APT still
retains the distro update timers before the failure propagates.

## Larger follow-up work

### P1: Verified, versioned agent-tool lifecycle

**Risk:** Codex CLI, Claude Code, and OpenCode are installed by network-fetched
shell pipelines, their installed versions are not recorded as desired state,
and rerunning setup skips an existing executable. Only T3 Code currently uses a
release-provided SHA-256 digest; GitHub CLI follows the managed APT repository.
This produces unbounded version drift and makes a rebuild dependent on whatever
the upstream installer serves at that moment.

Build one lifecycle contract for every agent tool:

1. define supported channels and optional exact-version pins in saved config;
2. fetch installers or release artifacts before execution and verify a pinned
   digest or publisher signature where the upstream supports it;
3. record installed source, version, digest, and install time without secrets;
4. stage an upgrade, run `--version` and a minimal non-authenticated smoke test,
   then atomically activate it with the previous binary retained for rollback;
5. add an explicit `infra-tools agent update` plan/apply flow rather than
   silently changing tools during unrelated host maintenance; and
6. define notification and rollback behavior for failed upgrades.

Tool-specific implementation must follow each vendor's supported distribution
and update contract; do not assume one install method or version syntax works
across all four agents.

Progress delivered in the first lifecycle slice:

- `infra-tools agent update` and `--dry-run` select Codex CLI, Claude Code, and
  OpenCode independently and use their documented vendor update mechanisms;
- only executables resolved under the current user's home are eligible, keeping
  APT and other system package installations outside this workflow;
- the previous executable is retained, `--version` and `--help` run before and
  after the update, and a changed/broken result triggers automatic rollback;
- a mode-`0600`, atomically written record preserves the method, observed
  versions, time, result, exit code, rollback outcome, and the downloaded Codex
  installer's SHA-256 without storing updater output; and
- an `in_progress` state is persisted before invoking the vendor updater so an
  interrupted operation is visible instead of looking successful.

Remaining lifecycle scope is still P1: saved channels and exact-version pins,
publisher-signature or pinned-digest verification where vendors expose it,
artifact-level staging rather than executable-only rollback, T3 Code updates,
notifications, and integration of desired-versus-observed versions into the
read-only audit surface. The Codex standalone installer currently documents a
fixed HTTPS update endpoint but no pinned publisher digest; its recorded hash
is therefore evidence of fetched bytes, not an independent trust anchor.

### P1: Agent-aware restart and maintenance windows

**Risk:** the default host restart policy checks login sessions, not active
agent processes, repository mutations, builds, `tmux` workloads, or an explicit
drain marker. After seven days of deferral, the force policy can schedule a
restart despite active sessions. Conversely, disabling forced restarts can
leave security-required reboots pending indefinitely.

Add a saved agent-host maintenance policy with:

- configurable maintenance windows and a host-level drain/hold command;
- detection of active agent processes, multiplexed sessions, and declared
  long-running jobs without inspecting prompts or repository contents;
- pre-restart warnings with a durable deadline and operator-visible reason;
- an optional quiesce hook that agents can use to checkpoint work; and
- post-boot verification of tool paths, repositories, timers, network access,
  and the previous maintenance result.

This should extend the current restart policy rather than create a parallel
reboot service.

### P1: Agent and maintenance audit surface

**Risk:** `agent doctor` can confirm that commands exist, but a green result
does not prove versions are supported, credentials have safe permissions,
copied config is parseable, repositories are healthy, or recurring jobs have
run successfully. Operators must manually correlate tool output with systemd
state and journals.

Extend the roadmap's read-only audit layer with stable text and JSON output for:

- expected versus observed tool versions and installation channels;
- executable ownership, writable parent directories, config/credential modes,
  and authentication status without secret contents;
- repository path ownership, dirty/unpushed state summaries, disk usage, and
  root-cache age;
- timer enabled/active state, last and next run, last service result, and
  pending reboot age; and
- firewall, SSH, disk, memory, time-sync, and notification delivery health.

The audit must distinguish `warning` (for example, sign-in still required) from
`error` (missing required tool, unsafe permissions, or failed required timer).

### P2: Repository, credential, and user-cache lifecycle

**Risk:** uploaded repositories are intentionally retained in a root-only cache
without retention or size policy. Copied credentials persist until a user or
vendor tool rotates/removes them.

Add explicit inventory and cleanup commands with dry-run output. Cache policy
should support maximum age/size and preserve the only available repository copy.
Credential work should report age and permissions, never secret contents, and
defer revocation/rotation to an explicit operator action.

The first user-cache lifecycle slice is complete. A weekly timer now runs as
the configured non-root account, inventories paths reported by npm, pip, uv,
and Go, and uses supported per-tool cleanup commands with conservative size
limits. OpenCode and Codex handling is limited to allowlisted rebuildable cache
and temporary paths, defers while the matching tool is active, rejects links
and paths outside the user's home, and never targets repository, credential,
session, memory, package, plugin, or OpenCode application-state directories.
The script also exposes direct `--dry-run` output. Repository retention,
credential inventory, operator-configurable limits, and a unified CLI audit
surface remain follow-up work.

### P2: Maintenance unit privilege separation

**Risk:** the shared service template has a broad root execution environment.
APT upgrade and system cleanup need substantial privileges, but security-event
collection and user-tool tasks do not need the same writable filesystem and
kernel surface.

Split hardening profiles by job capability. Apply systemd sandboxing such as
read-only paths, private temporary directories, restricted address families,
and bounded writable paths only after tests prove the required APT, journal,
audit, notification, and state operations still work. Keep the broad APT job
separate from user-scoped agent-tool maintenance.

User-tool cache work is now split into its own configured-account service; the
root cleanup service no longer runs npm, pip, or uv commands. Capability-based
systemd sandboxing for the maintenance units remains outstanding and still
needs the real-VM validation described above.

## Existing roadmap dependencies

- Cleanup-first removal of managed units and partial-apply recovery remain
  tracked in [Transactional execution and reconciliation](TRANSACTIONAL_EXECUTION.md).
- Fleet-wide desired-versus-observed reporting should reuse the roadmap's
  planned audit layer.
- Corrupt saved-state handling and durable operation markers should be solved
  once in the transactional work, not independently in agent commands.

## Validation requirements

- Unit tests cover no-notification local monitoring and every required timer
  verification failure.
- A Debian VM smoke test verifies the canonical `agent_vm` setup,
  firewall access, all five host timers, and `agent doctor` as the setup user.
- Upgrade tests cover interrupted download, bad digest/signature, failed smoke
  test, and rollback for each supported agent tool.
- Restart tests include active SSH, `tmux`, and agent processes plus force-deadline
  and explicit hold behavior.
- Audit output tests prove that no credential contents, tokens, prompts, or
  repository file contents are emitted.

## Primary implementation evidence

- `common/agent_steps.py`
- `lib/agent_cli.py`
- `lib/config.py`
- `plugins/common.py`, `plugins/server.py`, and `plugins/security.py`
- `lib/maintenance_systemd.py`
- `security/security_steps.py`
- `security/service_tools/security_monitor.py`
- `common/service_tools/auto_update_apt.py`
- `common/service_tools/auto_restart_if_needed.py`
- `common/service_tools/cleanup_maintenance.py`
