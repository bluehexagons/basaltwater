# Native Windows test sessions

Use `--test-session` with `desktop develop exec`, `node` or Godot `run` for a
disposable application profile, bounded project-defined readiness and a retained
evidence directory. Sessions use the existing Scheduled Task guardian and Job
Object; `status`, `logs`, `wait` and `cancel` keep their normal lifecycle meanings.
Projects retain their native launch commands, assertions and capture recipes.
The runnable [Node adapter example](node-test-session-adapter.md) demonstrates
explicit child profiles, atomic readiness, file-URL preloads and retained results.

```powershell
basaltw desktop develop exec --project 'F:\Repos\sample-game' --test-session --ready-timeout 60 --timeout 600 --dry-run -- python.exe qa_session.py
basaltw desktop develop exec --project 'F:\Repos\sample-game' --test-session --ready-timeout 60 --timeout 600 -- python.exe qa_session.py
basaltw desktop develop ready TASK_ID --timeout 90
basaltw desktop develop status TASK_ID
basaltw desktop develop logs TASK_ID
basaltw desktop develop cancel TASK_ID
basaltw desktop develop wait TASK_ID --timeout 60
```

Record the returned ID before waiting. Dry run validates the plan without
creating directories, reserving a project or launching code. It does not
evaluate whether the project actually honors isolation or readiness.

## Project contract

The child receives four environment variables:

| Variable | Meaning |
| --- | --- |
| `BASALTWATER_TEST_PROFILE` | Newly created disposable profile directory |
| `BASALTWATER_TEST_EVIDENCE` | Retained `artifacts` directory for project captures/results |
| `BASALTWATER_TEST_READY_FILE` | New readiness JSON path inside the profile |
| `BASALTWATER_TEST_READY_TOKEN` | Unique token identifying this invocation |

The project must explicitly use the supplied profile for saves, settings and
other test data. This is a directory contract, not a Windows account or OS
sandbox. Basaltwater does not redirect APPDATA/LOCALAPPDATA, rewrite settings,
read project manifests or infer isolation from successful launch. Do not use
the feature with an application that ignores the profile path.

After its own readiness assertions pass, the project atomically publishes a
JSON object containing `Token` equal to `BASALTWATER_TEST_READY_TOKEN` at
`BASALTWATER_TEST_READY_FILE`. The file must be at most 1024 bytes. For example,
a Python adapter can publish after checking that its owned application has
loaded the intended scene and profile:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

# Run project-owned readiness assertions before this publication.
ready = Path(os.environ["BASALTWATER_TEST_READY_FILE"])
temporary = ready.with_suffix(".tmp")
temporary.write_text(
    json.dumps({"Token": os.environ["BASALTWATER_TEST_READY_TOKEN"]}),
    encoding="utf-8",
)
temporary.replace(ready)
```

Keep the application/adapter running while the test session is in use. The
guardian must observe readiness while its contained launcher is alive; an
early exit fails even with exit code zero. This acknowledgement attributes the
signal to the private invocation; it is not an independent health assertion or
a security boundary against other processes belonging to the same account.
Malformed or mismatched markers fail the session. Readiness defaults to 30
seconds; `--ready-timeout` accepts 1–300 and starts at containment-gate release,
including worker startup. Overall `--timeout` remains separate and can end the
task earlier. Deadline expiry terminates owned descendants and retains evidence.

`develop ready` waits up to 60 seconds by default (1–86400 with `--timeout`). It
returns the same wait envelope as `develop wait`, with `WaitState: ready` only
for a live running session whose marker was accepted. Exit 0 means ready;
exit 1 means the session ended or needs attention, even if it previously became
ready. Exit 124 is a caller wait timeout and does not cancel the session. Use
`develop wait` to confirm terminal cleanup after cancellation or application exit.

## Coordination, evidence and cleanup

Session admission and ordinary task submission share the setup lock. A session
rejects active tasks for the same project; subsequent supervised tasks for that
project reject the session's reservation. Queued and cleanup-uncertain receipts
retain reservations until terminal cleanup. An active legacy receipt lacking
project identity blocks new sessions conservatively. Project paths reject
reparse aliases. Coordination covers the exact project directory supplied to
Basaltwater, not unrelated directories, repository aliases, unsupervised builds
or manually launched clients. Build first, then start a session; cancel and
confirm cleanup before rebuilding through the supervisor.

The task directory is the retained evidence folder. It contains
`session-evidence.json`, bounded `stdout.log`/`stderr.log`, project `artifacts`
and the authoritative `receipt.json`. The receipt records readiness UTC and
monotonic elapsed time, final execution/stream accounting and profile cleanup.
The evidence index records project and worker-source revision/dirty state,
selected executable/PowerShell/Node paths, available file versions and Windows
version. Missing checkout/version information stays unknown. A bounded
[host environment report](windows-environment.md) now records GPU/driver and
display geometry, mode refresh, scaling and read-only power-plan/AC/battery
evidence where available. Failed sections remain unknown without blocking
dispatch; per-window DPI, HDR and effective power-mode overlays are uncollected.
Captured metadata describes submission time, not a pinned source snapshot.

New runs retain `target.json` for the actual native executable identity/observed
exit code. Use [`develop diagnose`](windows-diagnostics.md) for bounded native
root resource snapshots; its `diagnostics` folder also survives profile cleanup.

The evidence index omits executable arguments and environment snapshots, which
can contain credentials. The private launch request is removed on settled
cleanup. Project logs/artifacts can still contain sensitive information; review
them before sharing. Projects own artifact size budgets and retention; only
stdout/stderr have automatic stream caps. No screenshots, crash dumps, ETW or
audio recording are started automatically.

After verified process-tree/output cleanup, private-request removal and
Scheduler retirement, Basaltwater removes only the generated profile and
publishes terminal completion. It validates at most 10,000 profile entries and
refuses reparse points without following them. Cleanup failure retains remaining
data and blocks maintenance and conflicting work. The guardian reports
`cleanup-uncertain`; unstarted-task cancellation raises the cleanup error and
leaves the receipt unfinished until guarded retirement succeeds.
Guardian loss also retains the profile; existing boot-recovery rules apply.
Queued cancellation and abandoned-task retirement after reboot perform the same
guarded profile cleanup. Retained logs, artifacts, provenance and receipts survive
profile removal. There is no automatic rerun after an uncertain outcome.

## Initial qualification

On 2026-11-02 the native Windows suite and session contracts passed, including
PowerShell 5.1/7 contracts and owned-process readiness, early exit, deadline,
cancellation and uncertain Scheduler cleanup cases. A separate disposable
fixture also passed through the actual limited interactive Scheduled Task path:
live readiness, terminal completion, profile/request removal and retained result
evidence were observed. These fixtures do not qualify a game's save isolation,
rendering, input or project-specific assertions. No installation refresh or
reboot was performed; raw receipts and logs remain private.

The subsequent installed-launcher review confirmed literal argv, caller wait
timeout without cancellation, project conflict/refusal and reservation release,
native exit seven propagation, and retained live/settled diagnostics. See the
[native readiness and acceptance record](windows-testing.md) for the tested
scope and the separate navigation-input correction required after `b16d1ca0`.
