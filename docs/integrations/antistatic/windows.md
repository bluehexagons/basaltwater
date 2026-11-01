# Antistatic development on Windows

Prepare the host with [Windows game tooling](../../workstations/windows-game-development.md).
These steps are specific to Antistatic and do not define a general Basaltwater
qualification gate. Paths below are illustrative; use the actual checkout.

## Project qualification

Use the [local qualification report](../../workstations/windows-qualification.md)
to record the tested revisions, tool pins, commands and open manual checks.

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
Set-Location C:\work\antistatic
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

## Related repositories

Animator uses its own `npm ci`, Node requirement and Electron package; no global
Electron installation is needed. Use its documented Electron launch for native
dialogs and filesystem behavior. Blender and `antistatic-assets` are optional
authoring tools; ordinary game builds use committed exports. For asset work,
install the Blender version required by that checkout at that revision,
using Blender's official distribution, and run its guarded export/check commands.
Do not replace that pin with the latest generic Blender package.
The adjacent Go matchmaking checkout is optional for ordinary game builds.
The protocol dependency installed by the game lockfile supplies its Windows
netplay test server source; a sister clone is not needed for that gate.

## Qualification evidence

Run the project's broader gameplay and native gates when required by its
contributor rules. Record budgets, explicit skips and retained failure evidence
privately. For packages, test a fresh archive extraction outside the checkout,
with isolated player data and an intentional PATH. Inspect actual engine startup,
asset/audio diagnostics and Unicode paths; a launcher exit alone is insufficient.
Manual rendering, input, audible audio and clean-machine installation remain
separate checks. Keep detailed results in Antistatic's project-owned
[QA record](https://github.com/bluehexagons/antistatic/blob/main/docs/qa/native.md),
not in Basaltwater's generic workstation guide.
