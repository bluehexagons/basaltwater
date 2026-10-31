# Local Windows qualification

Use this procedure on the Windows account that owns the native workstation.
Windows testing can remain local; GitHub Actions is not a prerequisite. Keep
Basaltwater host checks separate from project-owned builds and tests.

Copy the [report template](../examples/windows-qualification-report.md) into
private local evidence for each run. Record the exact source revisions, tool
versions, commands, exit codes and unresolved checks. Use `Pass`, `Fail`,
`Blocked` or `Not run`; identify whether evidence came from a command, direct
observation or a user report. A successful reboot reported by the operator is
useful evidence, without implying that interruption recovery was exercised.

## Record the host and Basaltwater source

Run from a reviewed Basaltwater checkout in PowerShell. These commands create
an evidence directory and read selected host/state fields; they do not apply
setup. The source checkout and saved installation can be different revisions.

```powershell
$evidence = Join-Path $env:LOCALAPPDATA ('Basaltwater\qualification\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $evidence -Force
$revision = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Run from the Basaltwater checkout' }
$changes = @(git status --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Basaltwater worktree' }
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw | ConvertFrom-Json
$windows = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
$graphics = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion)
[ordered]@{
    CheckedUtc = [DateTime]::UtcNow.ToString('o')
    SourceRevision = $revision
    SourceModified = $changes.Count -ne 0
    SavedRevision = $state.Revision
    SavedPhase = $state.Phase
    PowerShell = $PSVersionTable.PSVersion.ToString()
    Windows = $windows
    Graphics = $graphics
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'host.json') -Encoding UTF8
```

Keep raw logs and JSON private: executable paths can identify the account,
and project diagnostics can contain local paths or configuration. Publish only
a reviewed summary. Do not include credentials, environment dumps or personal
hostnames in a committed qualification record.

## Check host health and offline behavior

Use the current checkout's doctor before applying a source update. It reads
the account's saved selections and probes their tools. Capture its exit status
even when a report was produced:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Test-AgentWorkstation.ps1 -Json |
    Set-Content -LiteralPath (Join-Path $evidence 'doctor.json') -Encoding UTF8
$doctorExit = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $evidence 'doctor-exit.txt') -Value $doctorExit
```

An installed current CLI also supports `basaltw doctor --json`. A healthy
report establishes selected executable health and SDK/desktop discovery, not
provider login or project correctness. See the [doctor scope](windows.md#verify-this-account).

Run the complete offline suite after Basaltwater setup/diagnostic changes.
It uses temporary state and mocked installers and requires Python 3.11+:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1 *> (Join-Path $evidence 'native-tests.log')
$nativeExit = $LASTEXITCODE
Set-Content -LiteralPath (Join-Path $evidence 'native-tests-exit.txt') -Value $nativeExit
```

Inspect every nonzero result before rerunning. Preserve failure logs and use
the relevant focused test entry point for diagnosis. A successful retry does
not erase the earlier failure. Documentation-only changes need link/content
review; they do not require rebuilding every workload.

## Check real workloads

Run commands from each project's root at a recorded commit and inspect its
worktree first. Use the project-owned pins and Windows instructions; these
checks must remain usable without Basaltwater.

| Workload | Required local evidence |
| --- | --- |
| Antistatic source/build | Select the Node pin from its manifest; enter the supported x64 developer shell, run locked dependency installation, project doctor and build. Record the existing CMake generator; do not switch it in a populated build directory. |
| Antistatic source/gameplay | Run `npm.cmd test` under the project Node for the public-protocol gameplay suites and source checks. Record aggregate budgets, failures and explicit skips. Use the focused startup suite to diagnose worker lifecycle failures, then repeat the relevant enclosing gate. Follow the project's required `test:all` policy for native/build changes. |
| Antistatic Windows gate | Build the lobby dependency using its Go requirement, then run `npm.cmd run test:windows` under the selected Node. Preserve native, unit, netplay and project-check results, including explicit skips. |
| Antistatic package | Run its local package/archive commands and smoke checks. Check a fresh extraction outside the source tree, then exercise actual engine initialization and inspect asset diagnostics. A launcher fixture and exit code zero alone do not prove that audio/assets loaded. Record whether development DLL directories remained on PATH; that distinction matters for clean-target claims. Do not invoke release/tag/storefront publication commands. |
| snake2d | Match the project's Godot pin and export templates; run `test.ps1 all` for source suites, Windows export and exported startup outside the checkout. Preserve the selected engine path and template provenance. |
| Interactive behavior | Use each project's manual QA checklist for rendering, input, audio, resizing/scaling and persistence. Automated startup or GPU fixtures do not establish these results. |

See [Windows game tooling](windows-game-development.md#antistatic-qualification),
Antistatic's [testing guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development/testing.md)
and [native QA](https://github.com/bluehexagons/antistatic/blob/main/docs/qa/native.md),
and snake2d's [Windows commands](https://github.com/bluehexagons/snake2d#native-windows)
and [manual checks](https://github.com/bluehexagons/snake2d/blob/main/docs/manual-testing.md).
Tests that reset settings or scores must use disposable data or backed-up saves.

## Lifecycle and fresh-install boundaries

After an operator-authorized reboot, start a new terminal and T3 session,
check tool discovery, and repeat the relevant workload. Record reboot/login
success separately from setup recovery after an interrupted package install.

Use mocked tests for unavailable drives, unsafe paths and interruption cases
on an active workstation. Live interruption, WinGet repair and missing-package
installation belong on a disposable system. A fresh account on an existing
machine tests account setup while retaining machine-wide packages; a clean VM
is needed to establish their installation from scratch. Leave untested cases
open rather than inferring them from reconciliation on a configured PC.

## Recorded local run: 2026-10-28

Windows 11 Home 10.0.26100 x64, Windows PowerShell 5.1.26100.9711,
NVIDIA GeForce GTX 1070, Windows driver 32.0.15.8278. Tested Basaltwater
source: `f92b1d6a3cd8c460705dcd0e4060441c3b56ef7c`; the saved installation
was complete at `9c9b66f6c5e4b59a2cefadf01ab2c99d86d77e51`. The current
checkout's doctor was used to inspect those saved selections. The changes
accompanying this record are documentation only. Raw logs and reports remain
private.

| Check | Result and evidence |
| --- | --- |
| Reboots | Pass, reported by the operator before this run. No restart was initiated by the qualification commands. |
| Host doctor | Pass, 23 selected checks. Host Node 24.22.1, npm 11.19.1, Python 3.15.2, uv 0.13.6, Go 1.27.0, fnm 1.39.0 and Godot 4.7.2. |
| Basaltwater offline suite | Pass, complete `Run-NativeTests.ps1` invocation. |
| Antistatic | Pass at `885c69c9267f5457c04dddacda85f8fc076277d9`: locked install, doctor, build, lobby build, Windows gate and local package. Project Node 26.12.1/npm 11.20.0 were selected through fnm; the lobby binary records Go 1.27.3. |
| Antistatic gate details | All 38 native CTests, 261 unit suites, three selected netplay scenarios and TypeScript/project checks passed, alongside native headless, shader, replay and graphics-memory smokes. Linux launcher and offscreen resize checks explicitly skipped on Windows. |
| Antistatic Unicode correction | The Ogg filename regression failed on the baseline and passed with `0af9759a215e3ca3e8dd029322432a755233ad9d`. The complete Windows gate, rebuilt package and strict extracted-engine startup check passed with those corrected runtime/test files. |
| Antistatic broader aggregate | The initial `test:all` exhausted its five-minute JavaScript stage budget. After correction `1c47ec13942d3165ff2032978562dfa7464272da`, `npm test` passed in about 363 seconds and `test:all` in about 461 seconds. The shared JavaScript budget is now ten minutes; the successful source run needed about 355 seconds for that group. Original failure reports remain retained. |
| Antistatic gameplay/readiness | The initial standalone run had 148 passes, one skip and one live-STUN disconnect cancellation. Stress reproduced an early controller acknowledgement before the first STUN packet. The worker correction waits for subsequent complete live frame attempts; a held-frame regression failed before it and passed afterward, as did the startup suite and 32 stress trials with four workers. Full-gate gameplay now reports 150 passes, one opt-in public-STUN skip and no failures/cancellations. The 15-second cancellation deadline and four-second shutdown assertion are unchanged. |
| Antistatic follow-up limits | The passing full gate explicitly skipped Linux launcher, offscreen resize, automatic software-GL fallback and OpenGL 3.3 smokes. C formatting and shader checks passed; C static analysis skipped because this Visual Studio build has no `compile_commands.json`. It still needs a separately configured Ninja build. See Antistatic's qualification record for tooling platform skips. |
| Antistatic hardware fixtures | Pass on the actual NVIDIA renderer using the Windows SDL driver, OpenGL 4.0.0 NVIDIA 582.78 and GLSL 4.00. Integrated lighting/material/transport fixtures passed. |
| Antistatic package extraction | Bundled runtime, native addon, launcher fixture and actual packaged-engine initialization/shutdown passed from a fresh archive extraction outside the checkout, in a path containing spaces and Unicode, with child PATH restricted to Windows system directories. Engine startup used isolated data, dummy video and null audio. System-installed runtimes remained available. |
| snake2d | Pass at `d44b43bf0430fdccdc0a8799e538211487dc0193`: `test.ps1 all`, including eight source suites, composed-scene smoke, Windows release export and headless exported startup outside the checkout. Standard Godot 4.7.2 and previously verified matching templates used an isolated self-contained editor. |

The corrected local Antistatic archive SHA-256 is
`660c58064a7ce118899c852a1432068ac5cb085db7a8b3e44d5e888df62f9762`.
It was built from the precommit correction worktree, so its version file records
`885c69c9.dirty`; its runtime/test changes match correction commit `0af9759a`.
The original archive and failing diagnostics are retained privately.
An additional packaged-engine check reproduced failed Ogg loads from a Unicode
installation path despite a successful process exit. Antistatic's Windows
decoder now opens UTF-8 filenames explicitly, with a regression that checks an
ASCII control, Unicode audio, rejected files and successful retries. Startup
markers and process exit status must be checked alongside asset diagnostics.
Manual desktop presentation, physical input, audible audio, full packaged-game
asset traversal and a clean
machine installation remain open. Reboot success does not establish setup
interruption recovery. See the corresponding
[Antistatic qualification record](https://github.com/bluehexagons/antistatic/blob/main/docs/qa/native.md#verified-windows-local-qualification)
and [snake2d record](https://github.com/bluehexagons/snake2d/blob/main/docs/manual-testing.md#recorded-windows-automated-qualification--2026-10-28)
for project-specific boundaries.

## Recorded local run: 2026-10-30

The refreshed Windows 11 Home 10.0.26100 account was inspected before applying
the accompanying Windows integration changes. Saved setup was complete at
`007e581c8242f22e61e8576d37854168e7aeb7ae`. Private evidence remains under
the account's Basaltwater qualification directory.

- Host doctor passed all 27 checks, including T3 login startup, Sunshine and
  configured storage. GitHub/Codex authentication and Git identity/long-path
  configuration passed. The system and development volumes reported healthy.
  No required reboot or current Device Manager error was reported.
  The updated checkout doctor passed all 28 checks after adding bundled Git
  Bash diagnostics; its actual Bash executable also passed a version probe.
- Both recent detached refresh receipts were complete. The extra visible console
  was confirmed to contain only T3 processes and hidden without closing T3.
  The changed launcher passed disposable console/GUI executable fixtures:
  no console allocation and a visible GUI. An actual T3 relaunch using the new
  launcher remains a separate check.
- The complete native offline suite passed. Focused refresh, CLI, desktop launch,
  provider dependency and drive-root argument checks passed. CLI progress tests
  cover queued/running/completed status, incremental output without repeats,
  retained job duration, reconnecting, detachment and failure exit status.
  Concurrent receipt tests retained an old snapshot across atomic replacement
  and exercised bounded retry for readers denying delete sharing.
- Both documentation checkers passed on native Windows. The CLI checker now
  reads parser registrations without importing the POSIX controller. Portable
  regression tests cover imported builders, aliases, nested commands, unsupported
  patterns, missing docs and stable diagnostic paths. Runtime parser parity
  remains a POSIX-only check.
- Windows recorded one shutdown-time `0x9F` driver power-state failure on
  October 28, before the latest refresh. Its first parameter was `0x4`, a
  Plug-and-Play synchronization timeout. The initial audit did not read the
  protected minidump. The operator subsequently identified the Xbox controller
  driver during USB hardware inspection, reported recovery after reboot, and
  considers the incident resolved for now. No USB fault reproduction was run
  during the follow-up health checks. See Microsoft's
  [bug-check reference](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/bug-check-0x9f--driver-power-state-failure).

## Recorded local run: 2026-10-31

The [Windows workstation improvement pass](../plans/workstation-feedback-windows.md#windows-completion-record-2026-10-31)
was qualified on Windows 11 Home build 26100 using PowerShell 7.6.9,
Windows PowerShell 5.1.26100.9711 and Python 3.15.2. Complete native validation
tested clean implementation `82d5313369abed89f16a1b8a4d559e0a763a8e00`.
This record and the plan completion record are subsequent documentation changes.

- `Run-NativeTests.ps1` passed in 175.690 seconds, including native process-tree
  fixtures and all offline workstation suites. The eight Python capture tests
  had two explicit POSIX-only skips. Expected mocked runner warnings and a Git
  line-ending warning were inspected; the complete runner reported success.
- Focused skill/development contracts passed under both PowerShell versions.
  Owned native process fixtures exercised successful/failed jobs, cancellation,
  timeout, both-stream cap overflow, failed overflow, guardian loss and uncertain
  environment cleanup. Descendants terminated and an unrelated process survived.
  Scheduler, installer, notification and sleep behavior remained mocked.
- The actual checkout's project doctor returned its exact revision and clean
  tracked/untracked state through the Windows PowerShell CLI entry point, without
  executing project shims or GUI apps. Bounded metadata and untracked-dirty
  regressions passed. Both native documentation checkers passed.
- Only the Windows workspace skill bundle was updated with the guarded installer.
  Its entrypoint and supporting resources match the tested checkout, and delivery
  completion is confirmed. Saved setup/source remains at
  `116e9c78f809421ae8c624ce9ab2a932457a431d`; freshness correctly reports the newer
  bundle as different, four other installed bundles as legacy completion unknown,
  and five absent optional Claude bundles as informational. Loaded session state
  remains unknown. The installed CLI was not replaced; checkout diagnostics were
  used. No full refresh or reboot was performed.
- All 17 pre-existing development receipts were terminal after qualification.
  No owned live/uncertain task or Scheduler registration remained. Private logs,
  initial failed attempts, durations, source/catalog reports and implementation
  handoff remain in this account's qualification evidence directory.

Recipe integration remains deferred until a reviewed native consuming workflow
justifies it. Physical display/GPU, streaming, audio/input, lock/reconnect,
real Scheduler independence, fresh installation and live installer/reboot
interruption were not qualified by these fixtures.

### Post-refresh follow-up: 2026-10-31

After the operator refreshed to `92b2b5e266ae665e91cf8c086c97fd1ab22e9e12`,
the installed CLI passed all 28 workstation checks. All five installed Windows
skill bundles compared current against that saved source; absent unselected
Claude bundles remained informational. Desktop readiness, GitHub/Codex login,
configured volumes and selected services passed their checks. The refresh
receipt was complete and T3's completion receipt reported `restarted`. No
required reboot was reported; pending file operations were advisory only.

A real Scheduler smoke exposed a launcher boundary missed by the direct-API
fixtures: `basaltw.cmd` stripped embedded quotes from Python `-c` code before
task planning. The failed job retained accurate timing/logs and cleaned its
private request and Scheduler registration. The native launcher correction in
`392cb47aee388107d932dd742a2c4092ec9c1e7b` transports strings into the existing
PowerShell CLI without batch parsing. Regressions verify literal/empty/Unicode
arguments, task-plan delimiters, text-only output, failure exit codes, compilation
rollback and replacement while an old launcher is still executing.

The complete native suite passed the corrected implementation in 185.995 seconds,
again with two explicit POSIX-only Python skips. The final extended launcher
regressions passed separately in 8.019 seconds; later changes were tests and
documentation. Documentation and diff checks passed. The active session's
automatic approval review blocked installing the new executable in this account
with the reason `blocked by policy`; installed files and saved source remained
unchanged. The next normal refresh supplies the fix.

Real limited interactive Scheduled Tasks were subsequently qualified through the
existing PowerShell 7 script entry point, bypassing the batch argument boundary:

- A successful job preserved spaces, an empty argument, embedded quotes, percent
  text and Unicode exactly, with complete timing and clean-stream accounting.
- A deliberately failed job wrote 4 MiB plus 12,345 bytes to **each** stream,
  returned exit 23, retained exactly the cap and reported discarded bytes and
  separate 64 KiB tail omission correctly.
- Cancellation settled a live native process tree, confirmed the recorded
  descendant was gone, and published terminal timing only after cleanup.

These jobs completed after their submitting CLI processes exited. All four owned
receipts, including the original quoting reproducer, were terminal afterward;
all 21 account receipts had no retained private request, and owned Scheduler
registrations were removed. This establishes real Scheduler execution and
cancellation in the current login session. T3 closure/reconnect during an active
job, live installer interruption and physical display/audio/input/streaming
qualification remain separate. Private evidence is retained with this account's
post-refresh qualification report.
