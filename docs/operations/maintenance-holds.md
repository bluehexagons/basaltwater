# Agent maintenance holds and configuration

[Documentation index](maintenance.md)

## Agent Maintenance Holds

Before starting agent work that must survive the normal automatic-restart
window, create a bounded hold as the account that owns the agent tools:

```bash
basaltw agent maintenance hold --hours 8
basaltw agent maintenance status
basaltw agent maintenance release
```

The hold is a private, atomic file below the account's home and expires without
manual cleanup. Durations are limited to 1–72 hours. Creating another hold
renews it; release is idempotent. The same operation can be requested from a
control system with `basaltw agent maintenance hold HOST USER --hours 8`.

When a restart is pending, the restart job checks live agent work owned by the
configured setup account. T3 Code's open orchestration database supplies run
and provider-turn status; queued work, requests waiting for human input, and
activity predating the current server process do not block a restart. This
applies to all providers managed through T3, including native subagents and
background commands that outlive their parent turn. A request waiting for human
input does not hide work in another provider turn or child. Passive monitors
and abandoned rolled-back work do not count as executing background tasks.
Standalone Codex turns are detected
from lifecycle events in rollouts held open by the provider. Executing Codex
`exec`/`review`, including scheduled tasks with global options before the
subcommand and no saved rollout, Claude `--print`, and OpenCode `run` commands
also count as work.
Standalone interactive Claude and OpenCode sessions need an explicit hold when
their work must cross the restart window.

Lingering login managers, background sessions, idle logins, idle provider
servers, open terminals, tmux/screen, build/Git processes, and processes merely
sitting in worktrees do not count as agent work. SSH/desktop logins alone do not
defer restarts on hosts configured with coding agents; hosts without coding
agents still protect non-idle human logins. Use a bounded hold for independent
work that needs protection, such as an unattended build.

The job records only workload categories. It reads T3 status/time metadata and
background-task kinds and persistence flags,
bounded provider command arguments and Codex rollout tails locally; it does not
log their contents or scan closed session history. An unreadable activity
source defers the restart rather than reporting the host idle. An invalid hold
also fails safe and is visible in `basaltw agent doctor --capability host`;
release and recreate it. The configured forced-restart deadline still overrides
known sessions, holds, and workloads after its maximum deferral period.

These changes take effect on managed systems after their setup is refreshed;
updating the orchestration checkout alone does not replace installed services.

## Related Configuration

- `--auto-restart` / `--no-auto-restart` controls normal automatic restarts.
- `--auto-restart-force-days N` sets the maximum deferral period; `0` disables
  forced restarts.
- `--auto-restart-grace N` sets the warning period before a restart.
- `basaltw agent maintenance hold|status|release` manages a temporary,
  per-user automatic-restart hold without changing the host's saved policy.
- Notification targets configured with `--notify` receive important maintenance
  failures and successes where the service supports notifications.
- Security monitoring always collects and logs locally. Without `--notify`, it
  does not send events off-host.

On XRDP hosts, the security monitor validates certificate/key syntax, match,
expiry, private-key permissions, and daemon readability. It notifies only when
health changes (including recovery or certificate fingerprint rotation), while
an unusable pair keeps the service result failed until repaired. The warning
window is 30 days. The monitor never logs or records private-key contents.
