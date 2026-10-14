# Scheduling recipes

Substitute the real project directory and authorized scope. Inspect capabilities
and existing tasks before activating work. These examples show the interface;
they do not authorize running these jobs for an unrelated request.

## A deferred check

This schedules one inspection in 20 minutes, with one attempt and a 10-minute
runtime. Generate an absolute first-run timestamp once and retain the request
settings if the creation response is uncertain.

```bash
python3 - <<'PY' | basaltw agent tasks create - --schedule --request-key project-check-1 --json
import json
import time
print(json.dumps({
    "title": "Check the local build result",
    "prompt": "Inspect the saved local build output. Report its status and unresolved errors. Do not change files.",
    "directory": "~/repos/project",
    "mode": "inspect",
    "interval": "once",
    "start_at": time.time() + 20 * 60,
    "max_runs": 1,
    "timeout_minutes": 10,
}))
PY
```

A recurring check uses `hourly`, `daily`, `weekly`, or `custom` with
`repeat_minutes`. These are elapsed intervals, not calendar cron rules. For
an external signal, use `interval: "event"`, activate the task, and deliver
`notify` with a stable delivery key. Notifications do not schedule a timer.

## A bounded goal handed off from T3

Save or send these settings through stdin or the create MCP tool. The prompt
must match the user's authorization. Activate with `--run-now` when continuation
should start immediately, or `--schedule` for the first continuation interval.

```json
{
  "title": "Finish the project repair",
  "prompt": "Repair the failing project checks within the agreed scope. Preserve unrelated work. Leave changes for review and record verification results.",
  "initial_checkpoint": "The failure is reproducible with the project's documented focused checks. The suspected cause is in the parser. No changes have been made. Next: inspect the failing input and add a focused regression check.",
  "directory": "~/repos/project",
  "mode": "workspace",
  "interval": "once",
  "persistence": "checkpoint",
  "workspace": "worktree",
  "continue_until_done": true,
  "completion_criteria": "The documented focused checks pass with the regression covered; record exact commands and outcomes.",
  "continuation_minutes": 5,
  "max_runs": 6,
  "timeout_minutes": 30,
  "failure_limit": 2,
  "temporary_files": true,
  "network": false
}
```

Each successful unfinished run replaces the checkpoint with its own findings.
The goal stops on evidenced completion or its limits. Blocked reports count
toward failure pausing. Review evidence before raising a limit or resuming
paused work; changing limits preserves attempts and token consumption.
