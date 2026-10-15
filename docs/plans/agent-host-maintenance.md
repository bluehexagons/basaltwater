# Agent host maintenance

Status: host updates, user-cache cleanup, and support diagnostics are available.
Tool-version policy, work-aware restart coordination, broader fleet health, and
maintenance-service sandboxing remain unscheduled. Current operator behavior
is documented in the [maintenance guide](../operations/maintenance.md),
[maintenance agents](../operations/maintenance-agents.md), and
[project tooling](../agents/project-tooling.md).

## Current contract

- Routine host maintenance does not silently update user-installed agent CLIs.
  `basaltw agent update` provides explicit per-user updates with version/help
  checks, a bounded result record, and rollback when a local executable fails
  verification.
- `basaltw agent doctor` reports installed tool presence, path, version, and
  credential-file presence. It does not establish provider sign-in, pinned
  freshness, repository health, or full credential/configuration safety.
- User-cache cleanup runs under the configured account, uses each tool's
  supported cleanup path, preserves agent state and repositories, and provides
  dry-run output.
- System jobs retain their own maintenance lock, host capability checks, and
  configured restart policy. Container profiles may skip host-only work.

## Remaining boundaries

| Area | Current gap |
| --- | --- |
| Tool lifecycle | Saved channels, exact-version policy, publisher verification, staged package-level rollback, and T3 update ownership are not unified across providers. A fetched-binary hash is evidence of bytes, not an independent trust anchor. |
| Restart coordination | Login-session checks do not reliably identify every agent, build, `tmux`, or child process. No shared drain contract protects arbitrary in-progress work at a forced reboot deadline. |
| Read-only health | Provider sign-in, config parsing/modes, repository dirtiness, timer history, cache age, and tool freshness are not a single fleet result. |
| Retention | Repository cache limits, credential-age inventory, and operator-configurable cache limits are not defined. Secret values must remain outside diagnostics. |
| Service hardening | Maintenance units use shared service infrastructure; narrower per-job systemd sandboxes need a compatibility and recovery contract. |

## Qualification boundary

Mocked lifecycle coverage does not qualify Debian service activation,
interrupted updates, restart policy, or recovery. Complete the applicable
disposable-host cases in the [release checklist](../release-qualification.md)
before expanding the support claim. Do not infer loaded service code from an
installed source revision or setup receipt alone.
