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
