# Native shell and development tools

Use the project's Windows build/test commands and runtime pins. The prepared
shell and guardians use `pwsh.exe` (PowerShell 7); `powershell.exe` selects 5.1.
Run project scripts with `pwsh.exe -NoProfile -File SCRIPT.ps1` when they need 7.
PowerShell 7 supports `&&`; 5.1 does not. Prefer `npm.cmd` when execution policy
blocks `npm.ps1`. The native `basaltw.exe` launcher in PowerShell 7 preserves
literal argv; the explicit `.cmd` fallback and 5.1 binder have quoting limits.

`desktop doctor` reporting `worker: WindowsPowerShell-5.1` identifies its private
UI Automation helper, not your development shell. It uses installed .NET
Framework UIA assemblies. The public CLI isolates child module paths while
preserving the invoking shell's `PSModulePath`; no global shell/module-path
change or internal worker dot-sourcing is needed.

Use `Get-Command TOOL` for selected executable discovery, then `TOOL --version`
when runtime health is needed. The Store `python.exe` alias may lack Python;
verify the interpreter or use `uv run`/the project environment. Existing providers
can come from desktop apps instead of npm; preserve their installations/logins.

Git for Windows and Git Bash are required. `git-bash` opens the bundled desktop
shell; doctor probes its actual `bin\bash.exe`. Use that executable for Bash
scripts: a bare `bash.exe` can select WSL. Git's Unix utility directory is not
added to the Windows PATH.

`-GameDev` supplies C++/SDK, CMake, Ninja, LLVM and packaging tools. Enter the
supported x64 Visual Studio developer environment before Ninja builds/C analysis;
`cl.exe` on PATH alone does not supply SDK headers/libraries. `-ShaderTools`
separately supplies validation. `-Godot` / `refresh --godot` supplies standard
Godot; project engine pins and matching templates remain authoritative. Godot
.NET, its SDK and platform export SDKs remain separate requirements.

With `-NodeVersions`, use fnm for the reviewed project runtime and
`fnm exec --using=VERSION -- npm.cmd ...` per command. System Node LTS may be too
old. Keep project vcpkg/library pins and bootstrap authoritative; host tools do
not establish game/GPU readiness. See
[Windows game tooling](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-game-development.md).
