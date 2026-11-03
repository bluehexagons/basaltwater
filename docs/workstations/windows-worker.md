# Warm Windows desktop workers

Start an explicit worker session for repeated observations and input:

```powershell
basaltw desktop worker start --idle-seconds 120 --session-seconds 1800
basaltw desktop windows
# Use fresh selectors with the usual window/input commands.
basaltw desktop worker status
basaltw desktop worker stop
```

An active worker serves existing desktop commands automatically. Without one,
commands retain their bounded one-shot workers. Host environment reports and
process diagnostics keep their separate workers. Nothing starts at login, and
starting this helper does not enable human control or grant additional access.
Starting requires currently enabled control; stopping remains available during
override. Idle lifetime is 30–600 seconds; total lifetime is at least the idle
limit and at most 3600 seconds. Defaults are 120 and 1800 seconds.

The worker runs as a limited interactive Scheduled Task, loads its native types
once, and uses a private source snapshot. Each request has a new mailbox, caller
PID/creation identity, account/logon/session checks, absolute QPC deadline and
control revision/generation. Window identities, foreground, geometry and
physical-input guards are still revalidated; observations are not cached to
extend their freshness. Input acceptance remains separate from application
assertions.
An event wakes the idle helper for requests and stop; a bounded fallback sweep
covers interrupted publication without a busy filesystem polling loop.

Changed source refuses further warm requests until explicit stop/start. A busy,
failed or timed-out worker never falls back by replaying the action in another
worker. Claimed requests are single-use. Expired queued requests and departed
callers cannot begin input. Warm input rechecks caller/deadline/control admission
after potentially slow target probes. Window changes and UI Automation mutations
also check admission after resolving their target or pattern. Raw Unicode text
is constructed in full and submitted in one bounded batch, with accepted-prefix
release after partial delivery. It is not paced typing or an IME composition test.

The [keyboard hold watchdog](windows-held-input.md) remains independent of this
worker. Warm holds reuse the driver and preparation path but still create their
own release watchdog. Stopping a worker requests cancellation of its active
hold; timeout/worker loss retains uncertain outcomes rather than replaying them.
If graceful shutdown fails, explicit stop validates the owned process identity
before termination and retires the registration only after the worker lease is
closed. Active or uncertain worker sessions block setup/reboot admission.

Worker metadata and transient request mailboxes live below
`%LOCALAPPDATA%\Basaltwater\desktop\workers`. Requests can contain typed text:
they are private and removed after response consumption. A caller that disappears
can leave a retained mailbox, including its text, after closeout; do not share
that directory without review. Closeout does not prune this private evidence.
Metadata describes worker cleanup, not proof that an application action succeeded.
If retiring a consumed mailbox fails, an acknowledged action retains its normal
receipt with `worker_cleanup.confirmed: false` and the retained directory/error.
Inspect that private evidence; do not repeat the action to repair cleanup.
Shutdown tolerates mailboxes concurrently consumed and removed by callers.
Hold preparation stops before a driver starts if its mailbox cannot be retired;
completed hold and inspection receipts retain any cleanup warning.
After a timeout, inspect application state before deciding on another action.

Host doctor retains the bounded desktop-readiness reason, including controller
contention. If another desktop operation is active, inspect its outcome and run
doctor again when idle; a busy result does not establish a locked desktop.

Preliminary read-only worker probes on a native Windows workstation took about
0.36–0.43 seconds after the first warm request, versus about 1.14–1.18 seconds for
the preceding cold path. These small samples are not a latency guarantee or an
input-to-display measurement. CLI startup and independent hold-watchdog startup
remain additional costs. Use actual send timestamps and project assertions.

`windows/tests/Run-WarmWorkerTests.ps1` exercises real owned helper processes
with mocked desktop APIs/Scheduler and temporary mailboxes. It checks process
reuse, source drift, disabled control, expired requests, timeout and closeout.
Real SDL navigation, chords, sequences and exact Unicode delivery passed through
the guarded public CLI. See the [acceptance record](windows-testing.md) for the
tested source and remaining application, display and device boundaries.
