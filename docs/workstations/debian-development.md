# Supervised development tasks on Debian desktops

Use plain `basaltw desktop develop` on the configured shared XRDP desktop.
`--native` selects the CachyOS KDE backend. Run as the configured desktop owner,
without sudo. `desktop_session_unavailable` reports the desktop state and a
`remediation_argv` command. For a stopped session, run `basaltw desktop start`.
If a running supervisor reports `desktop_capability_missing`, check the
`required_capability` field. Load the refreshed supervisor at a normal logout
checkpoint after saving work. Setup can refresh source while the existing
supervisor remains in use.

```bash
basaltw desktop develop doctor --project /absolute/project --json
basaltw desktop develop exec --project /absolute/project --dry-run -- /usr/bin/python3 script.py
basaltw desktop develop exec --project /absolute/project -- /usr/bin/python3 script.py
basaltw desktop develop list --project /absolute/project --json
basaltw desktop develop status TASK_ID --json
basaltw desktop develop logs TASK_ID --tail 30
basaltw desktop develop wait TASK_ID --timeout 120 --json
```

`doctor` inspects project declarations and prerequisites. It does not run the
application or establish rendering, sound or input readiness. `--dry-run`
validates arguments and the existing session environment without creating a
task or service. Both remain available under human override/disabled control.
Launch and stop require enabled control. No command changes that permission.

Godot `editor`, `run` and `import`, declared `node` scripts, and project `check`
recipes use the same bounded argument/metadata readers as the
[CachyOS development workflow](cachyos-development.md). Tools must already be
installed; task launch installs no engine, runtime or dependencies. The Debian
supervisor's PATH selects tools, rather than the invoking SSH shell's PATH.
Use an absolute `--engine` or explicit executable path for a particular build.
Project scripts and recipes still own application behavior and acceptance.

For an isolated manual run, use [a declared playtest profile](cachyos-development-session.md#launch-a-recorded-playtest-profile).
Start the recorder for the same project, then run `desktop develop playtest PROFILE --project PATH`
through the updated supervisor. The task is attached before launch.

## Test with a disposable browser

Use `develop browser` for a fresh native browser profile and a private download
directory. On Debian the running supervisor must advertise `disposable-browser`.
On CachyOS use `basaltw desktop --native develop browser` in the existing KDE
session and follow the usual portal rules for screen control.

```bash
basaltw agent manifest --application firefox-esr --json
basaltw desktop develop browser --browser firefox-esr --project /absolute/project \
  --url https://example.test/ --json
basaltw desktop develop status TASK_ID --json
basaltw desktop wait --pid LAUNCH_PID --generation GENERATION
```

`--browser firefox` tries Firefox first and accepts Firefox ESR when only the
ESR command is installed. Native Chromium, Chrome, and LibreWolf
also support disposable profiles. To add Chromium as a secondary browser on
Debian, use `sudo basaltw local browser chromium --no-default`. To inspect
the launch command, add `--dry-run` to `desktop develop browser`. The task reports its
`profile`, `downloads`, and initial `launch_pid` once the worker starts.

Use the window receipt and its current identity before capture or input. A
browser window that opens partly off-screen needs `desktop window move` or
`desktop window resize` before a window-scoped capture. A file picker can appear
in a separate window. Inspect `desktop windows` after opening it and confirm
the dialog belongs to the test before targeting it.
If AT-SPI provides no tree, capture the verified window and use the normal
observation-bound input workflow. Verify the downloaded file in the task's
`downloads` directory before ending the test.

```bash
basaltw desktop develop wait TASK_ID --timeout 30 --json
basaltw desktop develop cleanup-browser TASK_ID --json
```

Close the owned browser normally before waiting, or use the task-specific stop
command. Cleanup requires verified exit and removes the private profile.
Collect any required files before adding `--include-downloads` to remove the
download directory too. Task records and logs remain available.

## Environment and ownership

The actual shared-session supervisor supplies its local X11 display, validated
owned X authority file and owned local session-bus socket. The user manager's
bus is checked separately for systemd operations. No GUI environment is imported
into the global user manager. Remote/abstract session-bus addresses and unsafe
authority files are refused. Live use requires qualifying the actual VM's
session configuration, rather than guessing display or authority values.
Session-bus sockets may live in a root-owned sticky directory such as `/tmp`,
as `dbus-run-session` normally arranges. This route checks socket ownership and
the connected peer's UID with a bounded local connection; it does not relax
the user-manager bus's private-directory check.

Each task gets a random ID and a private directory beneath the account's
`~/.local/state/basaltwater/development/`. Its transient user-systemd service has
an exact task-specific description, invocation identity, `ExitType=cgroup` and
`KillMode=control-group`. XFCE does not own KDE's `graphical-session.target`, so
Debian tasks do not use that target as their lifetime boundary.

Queued workers recheck the originating desktop generation and control revision
before starting project code. A session/control change before admission refuses
the queued launch. Started jobs have independent user-manager ownership:
disconnecting the controller or replacing the desktop helper does not discard
their task records or request a restart. They are not automatically cancelled
when control changes. Normal logout, user-manager shutdown, an X server closing
or the application itself can end them. This is not a guarantee of surviving
logout or reconnect; verify current task/window state after each transition.

The worker retains `task.json`, a live bounded `output.log` (16 MiB), initial
launch PID, completion code, elapsed time and truncation/error status. It drains
output after the log fills. Status reports the specific service's current
cgroup/process count when available. A parent exiting successfully cannot
establish completion while task descendants remain active. Tasks that ask an
already running application to open a document do not own that application's
existing process; inspect actual window/process identities.

Task status/list/logs/wait work without the desktop helper.
Pass the launch result's `job.id` to `status`, `logs`, `wait` or `stop`.
For discovery or recovery, `develop list` returns the same handle in each
`tasks[].job.id`. Listing reads saved records and reports their job state as
`unknown`. Query `status` for the observed outcome.
`ok` on submission means user-systemd acknowledged the launch. The child may
already have finished when the submission returns.
`job.state` carries the observed outcome and `child_exit_code` carries the
recorded exit when known. Status can read the record of a failed task
successfully.
A missing or uninspectable service with no sufficient evidence
stays unavailable/unverified. Status does not automatically repair or restart
anything. Logs, argv and raw receipts may contain project information; retain
them privately and inspect before sharing.

`develop logs TASK_ID` reads a private log directly, including live recipe
output for `check` tasks. Use `--json` for a bounded excerpt and its `job_log`
artifact, or `--worker` for supervisor output. `output_truncated` describes the
excerpt. `log_truncated` reports whether the worker exceeded its retention cap
when a completion record is available for the worker log. A wait deadline while
the task is still running or stopping returns `wait_timeout`, the same job
handle, and exit 124. Successful completion exits 0. Failed or unverified
completion exits 1.

## Stop and reconcile

Prefer the owned application's normal exit. For a task needing cancellation:

```bash
basaltw desktop develop stop TASK_ID --dry-run --json
basaltw desktop develop stop TASK_ID --json
basaltw desktop develop status TASK_ID --json
```

Stop first verifies the recorded transient service description and pinned
invocation. It requests only that unit's stop; it never kills a desktop process
group or signals arbitrary recorded PIDs. An acknowledged asynchronous stop
can still report `stopping`. Inspect again until cleanup is verified; do not
claim success from queue acknowledgement. The service's ten-second stop limit
bounds escalation for its own cgroup. Identity changes refuse cleanup.

A supervisor reply with `state: launch-unverified` carries the task ID. Use
`develop status TASK_ID` to reconcile that task. A lost reply can leave a
started task without a returned ID. Before another launch, use
`develop list --project PATH` (optionally `--since`/`--until`) and
inspect candidate task status/logs. Do not automatically relaunch. Listing is
bounded to 500 scanned records and 50 returned matches; heed truncation.
Records and logs are retained for explicit review, not automatically pruned.

`desktop evidence start/finish` records supervised-launch task IDs and permits
finalization against durable task status. Verification treats an explicit
`not-launched` receipt as a refusal and flags `launch-unverified` as uncertain.
[Manual evidence](desktop-automation.md#map-and-record-debian-observations)
and task completion still do not establish that the application produced its
intended result. Independently verify saved/exported files and UI state.

Legacy `desktop exec` remains the lightweight session-local immediate-child
route. It has no durable log or task stop. Existing launch receipts do not
become supervised tasks after refreshing.

## Qualification after refresh

First use a disposable program/profile. Verify a small logged success and a
nonzero exit, live output, a bounded task stop with a descendant, and refusal
when a queued task's control/session changes. Recheck task inventory after safe
helper replacement and RDP reconnect while preserving unrelated applications.
Never end the shared desktop merely to test process cleanup. Record revision,
running-helper provenance and which transitions were actually exercised.
Mocked regression tests cover contracts and failure handling; these live
session/cgroup checks remain required on the refreshed VM.
