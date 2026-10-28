# Windows game tooling

Native Windows workstations primarily cover compatibility tests, CI and
packaging. CachyOS remains the main interactive Antistatic development and
asset workstation. Basaltwater prepares host tools; each repository owns its
runtime requirements, library pins, npm dependencies and build commands.

## Select host tools

`-GameDev` adds Visual Studio 2022 Build Tools with the C++ workload and
recommended MSVC/Windows SDK components, CMake/CTest, Ninja, LLVM's compiler,
formatter, analyzer and symbolizer, and 7-Zip. It also selects Python/uv, Go
and fnm. Existing tools are reused and checked; setup does not silently upgrade
them. If an existing Visual Studio 2022 installation lacks C++ or its SDK,
complete it with Visual Studio Installer > Modify, then rerun setup.
VS 2022 is the provisioning baseline, not a new game compiler pin.
See Microsoft's [component catalog](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-build-tools?view=vs-2022).

`-ShaderTools` independently adds the Vulkan SDK's `glslangValidator` for shader
validation. The SDK is larger than the game needs at runtime; omit this option
when only building or running the focused Windows gate. LLVM tools are
unversioned on Windows; check the game's formatter baseline before accepting
format changes from a different LLVM major. Installing a validator does not
establish GPU or driver readiness.

`-NodeVersions` independently prepares [fnm](https://github.com/Schniz/fnm).
Setup installs no project runtime through fnm and changes no shell profile
or Node default. The ordinary `-Node` selection supplies LTS, which may be
older than a game's minimum. Antistatic currently requires Node 26.4 or newer;
its `.nvmrc` selects major 26 and its build manifest owns the exact packaging
runtime. Use those project files when versions change.

`-Godot` independently prepares the standard Godot editor and `godot` command
through `GodotEngine.GodotEngine`. Existing engines are retained and checked
headlessly. The version probe waits for the actual process, including a GUI
executable, and fails on a nonzero exit or a 30-second timeout.
When WinGet extracts its portable package without creating an
alias, setup supplies a per-account command launcher. Reconciliation refreshes
that launcher against the installed package. Match each project's required
engine version; this host selection does not override repository pins.
Install matching export templates through **Editor > Manage Export Templates**.
C# projects additionally need Godot .NET and the project's .NET SDK; Android and
other export SDKs remain separate. See the official
[Windows downloads](https://godotengine.org/download/windows/) and
[export guide](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html).

For a fresh Antistatic/Godot PC, paste this block into PowerShell as the coding
account. It includes T3 Code, Git/GitHub CLI/Codex, Node LTS, Python/uv, Go,
fnm, native game tools, shader validation and Godot. Package installers may
show UAC or require a restart.

```powershell
irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/windows/Install-GameWorkstation.ps1 | iex
```

The convenience entry point downloads the installer to a unique temporary
file, checks download/setup failures, and removes the file afterward. Existing
native installations resume with an explicit source update and preserve saved
storage. No drive letter is assumed on fresh PCs. Restart T3 Code and terminals
after setup. Review the script or use a checked-out copy when preferred:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File windows/Install-GameWorkstation.ps1 -Plan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File windows/Install-GameWorkstation.ps1 -StorageDrive F:
```

`-StorageDrive F:` selects `F:\Basaltwater` on a mounted NTFS drive for
runtimes/caches and new checkouts. See the repeatable split-storage setup below.
From a reviewed checkout, use
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File windows/Setup-AgentWorkstation.ps1 -Node -GameDev -ShaderTools -Godot`.
Pinned installer state saves the selections. Once installed, use
`basaltw refresh` to update Basaltwater and reconcile them; add
`--game-dev --shader-tools --godot` to adopt game tools on an existing account.
The one-line entry point also upgrades older native installations lacking
`basaltw` or the new Godot selection. Older installer copies do not recognize
new flags; use the current download for that first update.

## Storage

For systems with a separate development drive, select its letter explicitly.
This version of the download command accepts setup flags:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/windows/Install-GameWorkstation.ps1))) -StorageDrive F:
```

Replace `F:` with the mounted development drive on each PC. Both `F` and `F:`
are accepted. `-StorageDrive` works with `install.ps1 -Profile agent_windows`,
`windows/Setup-AgentWorkstation.ps1` and the game-workstation entry point.
Add `-Plan` to preview. For a custom directory, use
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
Subsequent `basaltw refresh` and installer `-Resume` reuse it without flags.
The volume and every managed subdirectory are checked before replacing saved
selections or entering setup. An absent/unhealthy drive, non-NTFS filesystem,
invalid path or junction fails with the previous saved setup intact. Restore
the drive or explicitly select a new letter; setup never falls back to C:.

The resolved root explicitly selects account-level `FNM_DIR`, `UV_CACHE_DIR`,
`npm_config_cache`, `GOMODCACHE`, `GOCACHE` and `VCPKG_DEFAULT_BINARY_CACHE`.
It creates `repos` for new checkouts and places a newly installed VS Build Tools
instance under `tools\vs2022`. Existing installations, repositories and caches
are not moved or deleted. Existing values for these six variables are replaced
by this explicit selection. Restart T3 and terminals to inherit them.
Keep the drive connected and its letter stable whenever using these tools.

For `-StorageDrive F:`, the layout is:

| Location | Purpose |
| --- | --- |
| `F:\Basaltwater\node` | fnm-managed project Node versions |
| `F:\Basaltwater\cache\npm`, `cache\uv` | JavaScript and Python download caches |
| `F:\Basaltwater\cache\go-mod`, `cache\go-build` | Go modules and build cache |
| `F:\Basaltwater\cache\vcpkg` | Native dependency binary cache |
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
Put the game checkout on the storage drive too: its `.build`, `node_modules`
and release output otherwise remain with the checkout.

## Antistatic qualification

Use the [local qualification report](windows-qualification.md) to record the
tested revisions, tool pins, commands and open manual checks.

Use the game's [setup](https://github.com/bluehexagons/antistatic/blob/main/docs/development/setup.md)
and [Windows testing guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development/testing.md)
at the revision being tested. Start an x64 developer shell from the Basaltwater
checkout so MSVC, headers, libraries and SDK tools agree:

```powershell
powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -File .\windows\Enter-GameDevShell.ps1
```

The helper preserves the prior `VCPKG_ROOT` (including an unset value) because
Visual Studio's shell otherwise injects its own bundled vcpkg checkout.

In that shell, enter the game checkout and select its reviewed runtime. This
example reads the project manifest instead of copying a pin into Basaltwater:

```powershell
Set-Location F:\Basaltwater\repos\antistatic
$nodeVersion = node --input-type=module -e "import {nodeBootstrap} from './scripts/build/manifest.mjs'; console.log(nodeBootstrap.version)"
if ($LASTEXITCODE -ne 0) { throw 'Could not read the project Node pin' }
fnm install $nodeVersion
if ($LASTEXITCODE -ne 0) { throw 'Node installation failed' }
fnm exec --using=$nodeVersion -- node --version
fnm exec --using=$nodeVersion -- npm.cmd --version
fnm exec --using=$nodeVersion -- npm.cmd run bootstrap:windows
if ($LASTEXITCODE -ne 0) { throw 'Native dependency bootstrap failed' }
fnm exec --using=$nodeVersion -- npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
fnm exec --using=$nodeVersion -- npm.cmd run doctor
if ($LASTEXITCODE -ne 0) { throw 'Project doctor failed' }
fnm exec --using=$nodeVersion -- npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Game build failed' }
```

If no base Node is installed, obtain the explicit version from the checked-out
manifest and use it with fnm. `fnm exec` selects the runtime for one command
without replacing the system Node or requiring shell-profile initialization.
The game bootstrap owns its vcpkg checkout, baseline, static triplet and SDL,
FreeType, OpenAL and other libraries. Do not globally set `VCPKG_ROOT`, triplets
or CMake toolchain overrides to replace them.

Before the documented `npm.cmd run test:windows` gate, verify the project's Go
requirement and build its locked lobby dependency as its Windows workflow does:

```powershell
go version
go -C node_modules/@bluehexagons/antistatic-server-protocol build -o antistatic-server.exe .
if ($LASTEXITCODE -ne 0) { throw 'Lobby test server build failed' }
fnm exec --using=$nodeVersion -- npm.cmd run test:windows
```

For C analysis, select Ninja with `$env:CMAKE_GENERATOR = 'Ninja'` **before the
first native configure**, inside the developer shell. Visual Studio generators
do not export `compile_commands.json`; see
[CMake's limitation](https://cmake.org/cmake/help/latest/variable/CMAKE_EXPORT_COMPILE_COMMANDS.html).
Do not switch generators in an existing build directory without following the
project's build-state cleanup procedure. Shader checks can deliberately skip a
missing validator; verify its executable before claiming that coverage.

## Godot project qualification

`-Godot` supplies the standard engine, not a project's exported builds. Check
the project's engine pin against `godot --headless --version`; install matching
export templates using Godot's Export Template Manager. On native Windows the
ordinary template directory is `%APPDATA%\Godot\export_templates\VERSION.stable`;
a self-contained editor uses its own `editor_data` directory instead. See
[Godot's export instructions](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html).

For [snake2d](https://github.com/bluehexagons/snake2d), enter its native checkout
and use its project-owned PowerShell commands:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test.ps1 source
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test.ps1 windows-export
```

Source tests require no templates. The native scripts resolve the actual Godot
executable from `BASALTWATER_GODOT_EXE` when no project override is supplied;
use `GODOT_BIN` for a different project pin. Git Bash does not find `godot.cmd`
as `godot`. Batch launchers also expand literal `%NAME%` argument text, so use
the real executable for such paths. A successful source suite/export smoke
does not replace a rendered game, real input and audio checks.

## Related repositories

Animator uses its own `npm ci`, Node requirement and Electron package; no global
Electron installation is needed. Use its documented Electron launch for native
dialogs and filesystem behavior. Blender and `antistatic-assets` are optional
authoring tools; ordinary game builds use committed exports. For asset work,
install the Blender version required by that checkout (currently 5.2.3 LTS),
using Blender's official distribution, and run its guarded export/check commands.
Do not replace that pin with the latest generic Blender package.
The adjacent Go matchmaking checkout is optional for ordinary game builds.
The protocol dependency installed by the game lockfile supplies its Windows
netplay test server source; a sister clone is not needed for that gate.

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
