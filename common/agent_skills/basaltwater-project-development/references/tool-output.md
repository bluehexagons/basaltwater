# Tool output and retained evidence

Use direct output for short commands. For verbose tests, builds or diagnostics,
keep full evidence in private files outside Git and bring only the status,
counts, actionable failure and artifact path into context. Do not suppress a
failure to obtain a smaller reply.

## Capture and inspect

With an existing Python 3.11+ interpreter, use the scripts shipped with this skill:

```text
python3 <skill-directory>/scripts/capture_output.py --timeout 300 -- COMMAND [ARGS...]
python3 <skill-directory>/scripts/read_output.py /private/run/summary.json
python3 <skill-directory>/scripts/read_output.py /private/run/summary.json --stream stderr --match FAILED --match Traceback
python3 <skill-directory>/scripts/read_output.py /private/run/summary.json --summary-only
```

On native Windows use the project's real Python executable in place of `python3`;
the Store alias is not proof of an interpreter. Neither script needs the Linux
controller or third-party packages. Native supervised-task `status/logs` remain
the route when Python is unavailable; this adds no launcher dependency.

Capture runs literal argv with closed stdin and keeps stdout/stderr in private
temporary files. It saves `summary.json` atomically before printing its compact
summary. `--output-directory NEW_PATH` selects a new directory under an existing
private parent; existing paths are refused. Retain its location before launching
work that may lose the terminal. Child status is preserved; timeout returns 124,
interruption 130. `error` and `cleanup_error` are evidence, not successful cleanup.
Windows cleanup targets the owned tree before its parent.

The reader never starts or resumes a command. It shows status/exit/duration and
byte counts. Success and unfinished captures default to summary only; failed
captures include bounded tails. `--stream stdout|stderr|both` explicitly requests
logs for any outcome. `--summary-only` avoids logs even on failure. Inspection
exit zero means the files were read, not that the captured command passed.

`--max-bytes` defaults to **4096 UTF-8 excerpt bytes total across both streams**
(maximum 65536), before JSON escaping and fixed metadata. Empty/short streams
give unused budget to the other. `excerpt_truncated` marks excluded excerpt data;
the full logs stay unchanged. Escaping Unicode/control characters can increase
the serialized response size, so leave room in the enclosing tool's reply budget.

`--scan-bytes` defaults to the last 65536 bytes per selected stream, at most
1 MiB. Repeated `--match TEXT` values are case-sensitive literal OR filters within
that window, not regexes or whole-file searches. The reader returns the most
recent matching lines, preserving a hit when a line exceeds its excerpt budget.
`scan_truncated` and `scan_start_bytes` show the search boundary; no match in a
partial window does not establish absence elsewhere. Increase the window or
inspect a targeted earlier portion of the retained log when needed. Size changes
during collection are flagged; this is not a pinned snapshot of an active log.
`utf8_replaced` marks decoding loss, including a window starting inside a
multibyte character. Use the retained bytes when exact encoding matters.

The reader accepts capture schema 1, bounds summary size to 64 KiB, and reads
only adjacent `stdout.log`/`stderr.log`. It ignores stored absolute log paths so
copied evidence remains local to its folder. Symlink/nonregular artifacts and
invalid outcomes are refused. Reports and excerpts are private evidence, not
redacted output; inspect before sharing.

## Select evidence before returning it

- Discover filenames or symbols before reading files. Respect complete-file
  reading rules before editing; choose chunks that fit the remaining reply budget.
- Use capability/application selectors for manifests and doctors. Load a skill
  entrypoint once and follow only references needed for the current operation.
- Discover relevant tool names and short descriptions before requesting their
  detailed schemas. Avoid dumping the entire available-tool catalog.
- In orchestration code, inspect every result's status, then emit selected fields
  and relevant failures. Avoid repeating structured data in its text envelope.
  A separate cap per parallel call does not bound their combined reply.
- Preserve full artifacts before a transport truncates output. A truncated result
  is not a complete file read, complete search or passing check. Retrieve the
  missing relevant evidence instead of rerunning the underlying operation.

## Keep the original run

The saved initial `in_progress` status has no exit code and does not prove a job
is alive. Capture is foreground collection, not durable supervision. Keep the
original tool/task handle and wait for its actual completion; do not replay a
command because output was quiet, a wait expired, or a terminal disconnected.
For uncertain cleanup or desktop mutation, inspect owned processes/task state
before further action. Use the platform's existing durable supervisor when needed.
