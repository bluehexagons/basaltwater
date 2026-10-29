# Windows native desktop and tasks

Use the intended Windows account and interactive session. Discover the current
native command surface with `basaltw desktop --help`. `desktop doctor` checks
session/API readiness without capture, input or app launch; `desktop status`
also reports persistent human control state. A locked or secure desktop, other
accounts/sessions and UAC prompts are outside the adapter.

Run these commands from the prepared PowerShell 7 development shell. Doctor's
`worker: WindowsPowerShell-5.1` describes a hidden, bounded UI Automation helper
using Windows' .NET Framework assemblies; it does not describe your shell or
the PowerShell 7 guardians used for project tasks. Basaltwater isolates the
helper's module paths without changing your shell's `PSModulePath`. Keep using
the public `basaltw` commands rather than dot-sourcing its internal worker code.
Use `$PSVersionTable.PSVersion` to check your shell. Run project scripts with
`pwsh.exe -NoProfile -File SCRIPT.ps1` in PowerShell 7; `powershell.exe` selects
Windows PowerShell 5.1.

Use `desktop windows` for handles, identities, generation and physical geometry.
`desktop inspect --pid PID` returns bounded UI Automation controls and references.
Only explicit screenshot commands capture pixels; choose a new PNG path and
observed window selectors or an explicit monitor device. PrintWindow may not
capture hardware-rendered content correctly. Inspect the artifact before claiming
visual success; an API or process exit alone does not prove rendering/audio/input.

Window actions require `--window HWND --identity IDENTITY --generation GENERATION`.
Element actions use `--ref REF --generation GENERATION`. References expire after
60 seconds or a mutation; observe again before another action. Text/focus and
window geometry actions verify their outcomes, while invoke/raw input require
task-specific reinspection. Raw input uses observed window width/height and
window-relative coordinates and requires that window to remain foreground.
Do not blindly repeat an action after timeout or an uncertain outcome.

Human override/disabled control blocks mutations and artifact/terminal handoff.
Observation remains available. Only an explicit human decision authorizes a
`desktop control ... --human-approved` transition; the task alone is not approval
to change it. While enabled, perform requested work without asking for a lease
or repeated approval. There is one input controller at a time. Browser operations
must still follow the active session's browser-tool policy.

`desktop open PATH [--reveal]` opens a local artifact or selects it in Explorer.
`desktop terminal --project PATH [--msvc]` opens the prepared PowerShell 7 shell,
optionally with the supported x64 Visual Studio environment only in that child.
Use these handoff commands when the human needs the output or a project terminal.

`desktop develop doctor --project PATH` discovers project tools. `develop exec
--project PATH -- EXECUTABLE ARGV...` preserves literal arguments. `develop node
SCRIPT --project PATH` selects an installed fnm numeric pin and matching npm;
it rejects unsupported package-manager pins instead of using incompatible tools.
Godot `develop editor|run|import --project PATH` honors `GODOT_BIN`, then the managed
engine. Project pins, templates and build commands remain authoritative.

Tasks return IDs; use `develop status ID`, `logs ID` and `cancel ID`. `--dry-run`
does not create state. `--msvc`, `--env NAME=VALUE` and `--timeout SECONDS` apply
only to the task. Real executables are accepted directly; scripts/batch launchers
need an explicit executable wrapper. Scheduled guardians survive T3 closing,
contain child processes and settle cleanup before recording completion. Private
environment snapshots are removed afterward. Logs are bounded and require review
before sharing. No upload/signing credentials are implied by local tasks.

Active or uncertain tasks block setup/reboot; cancellation is a request, so verify
terminal status before retrying maintenance. Tasks temporarily prevent idle sleep
and release the request when finished; they do not change the power plan or lock
policy. A GUI process remains subject to the real display/session state.

See the [Windows desktop guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-desktop.md)
for command examples and qualification limits.
