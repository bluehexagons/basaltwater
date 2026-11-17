# Command batches

Use a batch around a script to get one final report of command outcomes, setup
targets, elapsed times, dry runs, and notes. Ordinary Basaltwater commands join
the active batch automatically and keep their normal live output and exit codes.

```bash
basaltw batch start "Configure application systems"
basaltw setup server_web web.example.com admin
basaltw setup server_lite worker.example.com admin
basaltw batch run --label "Check application DNS" -- getent hosts web.example.com
basaltw batch note "Review application DNS before enabling public traffic"
basaltw batch end
```

`batch end` prints the report and exits 1 if any recorded command failed or is
unfinished; otherwise it exits 0. `batch status` prints the latest report with
the same status convention without closing it. Dry runs are labeled; a pass
means that invocation returned zero, including commands that only queue work.
Inspect their native job results separately for completion.

Commands outside Basaltwater must use `batch run -- COMMAND [ARG ...]` to be
recorded. This runs argv directly with inherited input/output and no time limit;
it returns the command's exit code (128 plus signal number for signal exits).
It does not interpret shell operators. Use an explicit `sh -c '...'` if needed.
Nested Basaltwater commands inside a wrapper can create additional records.
Shell builtins, unrelated processes, parser errors, help/version requests,
the retired migration command, and individual REPL commands are not tracked.

## Scripts that stop on failure

A normal shell script continues after a nonzero command unless configured
otherwise. With Bash `set -e`, arrange for the summary to run on exit:

```bash
#!/usr/bin/env bash
set -e
export BASALTWATER_WORKSPACE="$HOME/.config/basaltwater"
export BASALTWATER_BATCH="application-setup-$$"
basaltw batch start "Application setup"
finish_batch() {
    script_status=$?
    trap - EXIT
    batch_status=0
    basaltw batch end || batch_status=$?
    if [ "$script_status" -ne 0 ]; then
        exit "$script_status"
    fi
    exit "$batch_status"
}
trap finish_batch EXIT
basaltw setup server_web web.example.com admin
basaltw setup server_lite worker.example.com admin
```

To attempt every command with `set -e`, handle each failure explicitly, for
example `basaltw setup server_web web.example.com admin || :`. The final batch
result still reports the failure. Shell failures outside tracked commands are
preserved by the exit trap but cannot be inferred by the batch report.

## Scope, storage, and recovery

There is one active batch per workspace and batch slot. The default slot is
`default`, shared by every invocation using that workspace, including other
terminals. Set and export `BASALTWATER_BATCH` to a distinct non-secret name to
isolate scripts. Keep that value and workspace selection consistent for start,
commands, and end. A subprocess cannot export variables into its parent shell;
`batch start` therefore never tries to set shell state. Sudo and remote commands
use their own account/environment/workspace unless you explicitly configure
them. Wrapping sudo or SSH with `batch run` records the outer command locally.

An active batch cannot be replaced by another start. Parallel commands may join
one batch; wait for them before ending it. State updates use a short advisory
lock and atomic writes. A command killed before it can record its result remains
unfinished. End refuses unfinished commands; inspect the actual outcomes and
use `batch end --force` to close with a nonzero report. Force does not cancel
commands. A late completion after forced closure cannot change the closed
report. No command is retried or rolled back by batching.

The latest report remains available with `batch status` after end. Starting the
next batch archives the previous closed report under
`<workspace>/batches/history/`; slot files live in `<workspace>/batches/`.
Reports use schema version 1 and mode 0600. Corrupt or unsupported state is
preserved and reported as an error before command execution. Each batch is
limited to 1000 commands and notes. Titles, labels, and notes accept up to 1000
characters and reject control characters.

Records contain command names, setup/patch/share targets, timings, exit codes,
and explicit notes. Arguments and raw stdout/stderr are not captured; existing
per-command output remains the source for service URLs and diagnostics. Use
`batch note` to retain selected useful details, keeping secrets out of notes,
titles, and labels. These private reports are local operational state and
should stay outside Git. To remove retained history, delete only reviewed,
closed reports when no commands are using the batch slot.
