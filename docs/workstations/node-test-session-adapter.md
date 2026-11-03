# Project-owned Node test-session adapter example

The runnable [adapter](../../examples/game-testing/node-session/adapter.mjs)
starts one disposable [synthetic worker](../../examples/game-testing/node-session/worker.mjs).
It demonstrates the [Windows test-session contract](windows-test-sessions.md)
without a game, GUI, dependencies, manifest discovery or an installed Basaltwater.
Use an installed Node supporting `--import` and the built-in test runner (the
repository's Node 24/26 targets). Run from the repository root:

```text
node --test examples/game-testing/node-session/adapter.test.mjs
```

The acceptance suite uses temporary directories and real owned Node processes.
It checks live readiness, exact JSON Token, explicit child profile use, rejection
of wrong/absent synthetic assertions, early exits zero/seven, failure after
readiness, cooperative cleanup, retained evidence, and a preload path containing
spaces, Unicode and URL metacharacters. It runs on native Windows or Linux; it
does not qualify a game's behavior or Basaltwater's containment implementation.

## Explicit paths and readiness

The adapter requires absolute `BASALTWATER_TEST_PROFILE` and
`BASALTWATER_TEST_EVIDENCE` paths. Supply fresh private directories for each run.
The evidence directory must be disjoint from the profile. Paths are trusted
caller-owned directories, without symlink/junction aliases; lexical validation
is not a filesystem sandbox. The example never discovers or opens an ordinary
user profile. It refuses an existing runtime directory or readiness marker.

| Path | Owner and lifetime |
| --- | --- |
| `$BASALTWATER_TEST_PROFILE/runtime` | Explicitly passed as the child's `BASALTWATER_TEST_PROFILE`; contains only synthetic `state.json` |
| `$BASALTWATER_TEST_READY_FILE` | Adapter control marker inside the outer profile, outside `runtime`; survives a reset limited to `runtime`, removed when the adapter settles |
| `$BASALTWATER_TEST_EVIDENCE` | Retained artifacts outside the profile: `worker.json`, `assertions.json`, `worker-stopped.json`, `adapter-result.json` as each phase completes |

A worker process restart alone leaves disk state unchanged. A project reset
limited to `runtime` leaves the control marker and evidence intact; resetting the
whole outer profile would destroy the control marker and is forbidden during a
session. The acceptance test exercises a synthetic runtime deletion while the
worker is live. This fixture does not implement an automatic restart policy.
Reuse needs a fresh runtime or an explicit project-owned reset after confirmed
cleanup. Basaltwater removes the whole generated profile on settled cleanup and
retains evidence; a native caller owns its temporary profile's later removal.

When supplied, `BASALTWATER_TEST_READY_FILE` and
`BASALTWATER_TEST_READY_TOKEN` must appear together. The adapter validates its
worker's IPC assertion, explicit runtime path, preload execution and synthetic
state on disk, then writes `{ "Token": "invocation-token" }` to a new sibling
temporary file and renames it atomically to the ready path (at most 1024 bytes).
It checks the child is live around publication. The guardian must independently
observe a live launcher; an application can exit immediately after any liveness
check. No game assertions, save format or scene checks are implied. A real
project replaces these synthetic assertions with its own acceptance criteria.

The worker's `--import` value is constructed with `pathToFileURL`, including on
Windows; passing an unconverted drive-letter path to an ESM preload is unsafe
for module resolution. The project selects the worker and preload explicitly.
No commands or manifests are inferred from the checkout.

## Native launch without Basaltwater

This PowerShell example creates fresh private paths under the native temporary
directory. It sets no ordinary profile variables and needs only installed Node:

```powershell
$sessionRoot = Join-Path ([IO.Path]::GetTempPath()) ('synthetic-session-' + [guid]::NewGuid().ToString('N'))
$env:BASALTWATER_TEST_PROFILE = Join-Path $sessionRoot 'profile'
$env:BASALTWATER_TEST_EVIDENCE = Join-Path $sessionRoot 'evidence'
Remove-Item Env:BASALTWATER_TEST_READY_FILE -ErrorAction SilentlyContinue
Remove-Item Env:BASALTWATER_TEST_READY_TOKEN -ErrorAction SilentlyContinue
node.exe examples/game-testing/node-session/adapter.mjs
```

The native run performs the same assertions without publishing a control marker.
It cooperatively stops at its 30-second total lifetime, or on handled SIGINT /
SIGTERM. On Linux set the same two absolute environment paths and run the same
Node command. These variables are the example's explicit private-profile API;
Basaltwater itself is optional. Inspect retained `adapter-result.json` before
removing your private directory. No profile or evidence removal is automatic in
native mode. Environment changes above apply to the current shell; clear them
after use if continuing other work there.

## Optional supervised launch and integration hook

From a project checkout, submit the explicit adapter command through the existing
Windows runtime (use an overall timeout larger than the example's lifetime and
cleanup allowance):

```powershell
basaltw desktop develop exec --project 'F:\Repos\synthetic-project' --test-session --ready-timeout 10 --timeout 60 -- node.exe examples/game-testing/node-session/adapter.mjs
```

Record the returned task ID and use the existing `ready`, `status`, `logs` and
`wait` commands. Actual supervised cancellation/Job containment is owned and
tested by Basaltwater, not by this example. Windows force termination need not
deliver JavaScript signal handlers. This adapter is not a second process-tree
supervisor: it owns exactly one direct child, which starts no descendants. It
requests cooperative stop over IPC, waits up to one second, then terminates
only that direct child if needed and waits one more second. An unconfirmed
cleanup is a failure requiring inspection, never permission to retry. The
fixture also exits if its IPC parent disappears. Real applications with child
trees require the runtime's OS containment, including outside graceful shutdown.

The exported `runSession` accepts bounded readiness, lifetime and cleanup budgets
(50–300000 ms), an AbortSignal, a preload path and a synchronous `onReady` hook
for project integration/tests. Child exit seven is propagated as seven; early
exit zero is an adapter failure. Wrong assertions and readiness deadline expiry
never publish a marker. The CLI's explicit `--fixture-mode` accepts `exit7`,
`exit0`, `wrong`, `absent`, `fail-after-ready` or `success` for synthetic checks.
It defaults to `success`. Logs inherit the caller's streams and evidence remains
private; review artifacts before sharing.

The `desktop` and Node compatibility test selections run this acceptance suite
through `tests.test_node_session_adapter`; it is also part of full CI.
The suite's malformed/missing synthetic
readiness checks complement the runtime's separate wrong/missing Token tests;
they do not replace guardian acceptance tests.
