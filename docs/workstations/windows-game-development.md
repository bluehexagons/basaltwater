# Windows game tooling

Native Windows workstations support development, compatibility tests, CI and
packaging. Choose a host according to the project's supported platforms.
Basaltwater prepares host tools; each repository owns its runtime requirements,
library pins, npm dependencies and build commands.

## Select host tools

`-GameDev` adds Visual Studio 2022 Build Tools with the C++ workload and
recommended MSVC/Windows SDK components, CMake/CTest, Ninja, LLVM's compiler,
formatter, analyzer and symbolizer, and 7-Zip. It also selects Python/uv, Go
and fnm. Setup and refresh install available updates for selected host packages
through WinGet, without a separate update check. Existing Visual Studio 2022
editions are updated using their matching package identity. If an existing
Visual Studio 2022 installation lacks C++ or its SDK,
complete it with Visual Studio Installer > Modify, then rerun setup.
VS 2022 is the provisioning baseline, not a new game compiler pin.
See Microsoft's [component catalog](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-build-tools?view=vs-2022).

`-ShaderTools` independently adds the Vulkan SDK's `glslangValidator` for shader
validation. Omit this option when the project's build and checks do not need
shader validation. LLVM tools are unversioned on Windows; check the project's
formatter baseline before accepting
format changes from a different LLVM major. Installing a validator does not
establish GPU or driver readiness.

`-NodeVersions` independently prepares [fnm](https://github.com/Schniz/fnm).
Setup installs no project runtime through fnm and changes no shell profile
or Node default. The ordinary `-Node` selection supplies LTS, which may be
older than a project's minimum. Read its version files, package engines and
packaging manifest before selecting a runtime.

`-Godot` independently prepares the standard Godot editor and `godot` command
through `GodotEngine.GodotEngine`. The host engine is updated and checked
headlessly. The version probe waits for the actual process, including a GUI
executable, and fails on a nonzero exit or a 30-second timeout.
When WinGet extracts its portable package without creating an
alias, setup supplies a per-account command launcher. Reconciliation refreshes
that launcher and its `BASALTWATER_GODOT_EXE` selection against the validated
engine, including when a real PATH command supersedes the portable package.
Match each project's required
engine version; this host selection does not override repository pins.
Install matching export templates through **Editor > Manage Export Templates**.
C# projects additionally need Godot .NET and the project's .NET SDK; Android and
other export SDKs remain separate. See the official
[Windows downloads](https://godotengine.org/download/windows/) and
[export guide](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html).

For a fresh native game-development PC, paste this command into PowerShell as
the coding account. It includes T3 Code, Git/GitHub CLI/Codex, Node LTS, Python/uv, Go,
fnm, native game tools, shader validation and Godot. Package installers may
show UAC or require a restart.

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1))) -Profile agent_windows -Node -GameDev -ShaderTools -Godot
```

These are optional flags on the same Windows installer used for a basic
workstation. Append `-Sunshine` for Moonlight desktop streaming, or `-Plan` to
preview. The same command updates existing native installations and preserves
saved selections and storage. No drive letter is assumed on fresh
PCs. Setup queues T3's restart; reopen other terminals afterward.
From a reviewed checkout, preview through the root installer:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Profile agent_windows -Node -GameDev -ShaderTools -Godot -Plan
```

`-StorageDrive F:` selects `F:\Basaltwater` on a mounted NTFS drive for
runtimes/caches and new checkouts. See the repeatable split-storage setup below.
Pinned installer state saves the selections. Once installed, use
`basaltw refresh` to update Basaltwater and reconcile them; add
`--game-dev --shader-tools --godot` to adopt game tools on an existing account.
Use the same current root installer download to
upgrade an older installation whose saved source or CLI predates these flags.

## Storage

For systems with a separate development drive, select its letter explicitly.
Append the storage flag to the same download command:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1))) -Profile agent_windows -Node -GameDev -ShaderTools -Godot -StorageDrive F:
```

Replace `F:` with the mounted development drive on each PC. Both `F` and `F:`
are accepted. Add `-Plan` to preview. For a custom directory, use
`-StorageRoot 'F:\Development\Basaltwater'` instead; the two options are
mutually exclusive. Omit both to retain an existing saved choice or, on a
fresh PC, use normal account locations.

On an installed workstation, update Basaltwater first if its CLI predates this
option, then select or preview the drive:

```powershell
basaltw refresh
basaltw refresh --storage-drive F: --dry-run
basaltw refresh --storage-drive F:
```

Setup saves only the resolved `StorageRoot`, such as `F:\Basaltwater`.
Subsequent `basaltw refresh` and installer reruns reuse it without flags.
The volume and every managed subdirectory are checked before replacing saved
selections or entering setup. An absent/unhealthy drive, non-NTFS filesystem,
invalid path or junction fails with the previous saved setup intact. Restore
the drive or explicitly select a new letter; setup never falls back to C:.
An explicit empty `-StorageRoot` is rejected; it cannot clear a saved selection
while leaving the account's cache settings on the old drive.

The resolved root explicitly selects account-level `FNM_DIR`, `UV_CACHE_DIR`,
`npm_config_cache`, `GOMODCACHE`, `GOCACHE`, `VCPKG_DEFAULT_BINARY_CACHE`,
`TEMP` and `TMP`. The temporary directory is used by this account's new
processes and installers that honor these variables; system services and
applications with their own temporary paths can still use C:.
It creates `repos` for new checkouts and places a newly installed VS Build Tools
instance under `tools\vs2022`. Existing installations, repositories and caches
are not moved or deleted. Existing values for these variables are replaced
by this explicit selection. Restart T3 and terminals to inherit them.
Keep the drive connected and its letter stable whenever using these tools.

For `-StorageDrive F:`, the layout is:

| Location | Purpose |
| --- | --- |
| `F:\Basaltwater\node` | fnm-managed project Node versions |
| `F:\Basaltwater\cache\npm`, `cache\uv` | JavaScript and Python download caches |
| `F:\Basaltwater\cache\go-mod`, `cache\go-build` | Go modules and build cache |
| `F:\Basaltwater\cache\vcpkg` | Native dependency binary cache |
| `F:\Basaltwater\temp` | Account temporary files for applications that honor TEMP or TMP |
| `F:\Basaltwater\repos` | New project checkouts |
| `F:\Basaltwater\tools\vs2022` | A newly installed VS Build Tools instance |

Setup validates the volume and directory paths, refusing non-NTFS volumes and
junctions/symlinks. It does not repartition, format, mount or pick disks. A Linux
filesystem needs a separately authorized conversion before native Windows use.
Use a directory owned by this coding account; do not share writable tool/cache
paths with untrusted accounts.

The game bundle requires at least 15 GiB free on the system/account drives and
25 GiB on the selected storage drive before installing missing tooling. A
complete existing bundle still receives version checks on reconciliation,
without requiring the fresh-install allowance again. Discovery checks both
PATH and the vendor locations used during setup, including 7-Zip, CMake,
LLVM and the Vulkan SDK; a vendor CLI outside PATH does not by itself require
a new installation. Without separate
storage, the larger floor applies to the account drive. These are conservative
bootstrap floors; vcpkg sources, intermediate builds, packages, captures and
multiple checkouts need additional space. Shared Windows SDK components,
vendor installer caches and some packages still use C:.
Place large checkouts on the storage drive too when their dependencies,
intermediate builds and release output are stored beside source files.

## Native project qualification

Use the [local qualification procedure](windows-qualification.md) to record
tool versions, project revisions and open manual checks. Start the supported
x64 developer shell before native builds:

```powershell
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -File .\windows\Enter-GameDevShell.ps1
```

The helper preserves the prior `VCPKG_ROOT` because Visual Studio may supply
its own bundled checkout. Keep the project's runtime, dependency manager and
compiler/toolchain requirements authoritative. Choose the CMake generator before
initial configuration; changing it in a populated build directory needs the
project's cleanup procedure. Use a generator that supports
`compile_commands.json` when the analysis workflow needs it.

For game-specific bootstrap, lobby tests and packaging, use the optional
[Antistatic Windows guide](../integrations/antistatic/windows.md).

## Godot project qualification

`-Godot` supplies the standard engine, not a project's exported builds. Check
the project's engine pin against `godot --headless --version`; install matching
export templates using Godot's Export Template Manager. On native Windows the
ordinary template directory is `%APPDATA%\Godot\export_templates\VERSION.stable`;
a self-contained editor uses its own `editor_data` directory instead. See
[Godot's export instructions](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html).

Use the project's native source-test, export and exported-startup commands.
A source suite does not qualify a packaged export, rendered interaction or audio.
The optional [snake2d example](../integrations/snake2d.md) illustrates the
project-owned commands and engine-launcher boundaries.

## Validate Basaltwater changes

The existing native workstation test entry point includes compiler discovery,
workload arguments, missing tools, failure propagation, cache path validation,
plan behavior, disk floors and saved selection recovery with mocked host calls:

Use an installed Python on PATH for the native argv fixture, which verifies
PowerShell 5.1 quoting without invoking a package installer.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File windows/tests/Run-AgentWorkstationTests.ps1
```

Real downloads, UAC, reboot recovery, a game build, GPU behavior and packaging
need their own recorded Windows qualification. An installed tool or successful
Basaltwater test does not establish that the game passes its native gates.
