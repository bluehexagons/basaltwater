# CachyOS native game tooling

Use `agent_cachyos` on an existing, fully updated CachyOS KDE workstation for
native game development, Godot, Node/Go tools and Electron asset authoring.
This guide covers host prerequisites, runtime selection and capture backends.
Each project owns its dependency pins, builds, tests and acceptance criteria.
Basaltwater's managed final-build and publication workflows use Debian; a
rolling development toolchain need not reproduce a release SDK.
For game-specific layouts and commands, use the optional
[Antistatic integration](../integrations/antistatic/cachyos.md).

For a first installation, use the [fish installer](cachyos.md#quick-start)
with the options below after `--local-setup agent_cachyos`. If the launcher is
already installed, preview your first development selection in fish as the
normal desktop account, without `sudo`:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --codex \
  --node --python --go --godot --git-lfs --game-dev --blender --dry-run
```

Remove `--dry-run` to apply. `--node` and `--python` select the general language
tools. Project runtime installs prepare NVM on demand; optionally add
`--node-versions` to prepare it during setup.
See [local setup](cachyos.md) for T3 modes and package prompts, and
[maintenance](cachyos-maintenance.md) for later changes to a saved selection.

## Development packages

| Option | Capability |
| --- | --- |
| `--game-dev` | CMake/Ninja, Clang/format/tidy, compiler-rt/LLVM sanitizer runtime and symbolizer, glslang, GDB/strace/Valgrind/ccache; SDL3/SDL3_image, FreeType, Vorbis, libusb, GLEW, OpenAL; Xvfb/xauth/OpenGL diagnostics; Electron host libraries |
| `--node` | Node/npm/pnpm when missing; existing PATH runtimes retain their original manager |
| `--node-versions` | User-local NVM for explicit per-project Node installs; no runtime download, shell initialization, or default-version change during setup |
| `--go`, `--godot` | Explicit Go tools and native Godot editor/runtime; project Godot/.NET requirements remain separate |
| `--git-lfs` | Git LFS and missing user filter defaults for model source repositories |
| `--blender` | Native Blender, Wayland decorations, and existing task-scoped KDE automation prerequisites |
| `--av-tools`, `--gl-tools` | Optional asset processing and additional Vulkan/API-trace diagnostics |

`--game-dev` also supplies the existing KDE automation prerequisites for native
desktop-authoring work. It starts no GUI, capture, input session, or service.
Packages come from the configured repositories, with their normal headers and distro
versions; setup neither synchronizes pacman databases nor replaces installed
packages. Run CachyOS's normal full update first. Project-native bootstrap
commands remain available independently of Basaltwater.

Xvfb and xauth allow optional project captures without using the personal
desktop. The workstation's existing graphics stack supplies OpenGL/GBM and
any software renderer. Basaltwater does not select GPU drivers or force a Mesa
variant. Hardware rendering and captures remain project checks, not setup gates.
The [Arch Xvfb package](https://archlinux.org/packages/extra/x86_64/xorg-server-xvfb/files/)
provides both `Xvfb` and `xvfb-run`.

The bundle installs host libraries for project-installed Electron.
Each project's lockfile owns its Electron version and supported desktop
baseline; launch it through its npm scripts. GTK3/NSS are also runtime
dependencies of [Arch's Electron package](https://archlinux.org/packages/extra/x86_64/electron44/).
No sandbox override is configured. Distro compiler/formatter/shader tools are
development prerequisites, not replacements for project pins. Arch's
[Clang tools](https://archlinux.org/packages/extra/x86_64/clang/files/),
[compiler-rt](https://archlinux.org/packages/extra/x86_64/compiler-rt/files/),
[LLVM symbolizer](https://archlinux.org/packages/extra/x86_64/llvm/files/) and
[glslang](https://archlinux.org/packages/extra/x86_64/glslang/files/) provide the
commands/runtime. Projects own actual sanitizer, formatting and shader checks;
package presence does not verify compiler/runtime compatibility. Managed
Playwright, Windows cross-compilation, release containers and storefront
publishing are outside this workstation role.

## Comparable development checks

Use the same project revision, runtime pins, fixtures and declared recipes on
both hosts. Comparable outcomes do not require matching rolling package versions.

| Work | Debian coding/build host | CachyOS hardware workstation |
| --- | --- | --- |
| TypeScript/Go and native correctness | Project Node/Go pins, CMake builds, tests and sanitizer recipes | The same project checks with selected runtimes and native tooling |
| Godot development | Matching project engine, asset import, bounded headless tests and software captures | Matching project engine, import/tests plus native editor and hardware playtests |
| Shader/capture review | glslang and controlled Xvfb/software references | glslang and software references plus explicitly tested Wayland/Xwayland/offscreen hardware contexts |
| Final builds/publication | Reviewed release SDK, ABI/export/package gates and publishing credentials | Handoff tested source/evidence; development builds remain local test binaries |

Discover native tools with `basaltw agent manifest PATH --capability development
--json`; use `basaltw local cachyos-doctor --json` for saved CachyOS selections.
For Godot, `basaltw desktop --native develop doctor --project PATH --json` reads
project/engine prerequisites; explicitly import assets before editor/run tests.
See [Godot project tasks](cachyos-development.md). Check the project's engine
version and .NET requirements rather than upgrading its project to match a
rolling package. Record actual compiler, shader tool, SDL source, engine,
renderer/backend, fixtures and recipe outcomes with private evidence.
Host diagnosis never runs project builds or grants capture/input consent.

## Select each project's Node version

Related projects can intentionally use different development pins. Their
`package.json` engines and version files remain authoritative; do not copy the
workstation's rolling Node version into project or release metadata.

`basaltw node install` prepares missing `~/.nvm` on demand with Basaltwater's
declared vendor installer policy, then installs the project pin. Agents can
use it directly when a project needs another runtime; users do not need to
select `--node-versions` beforehand. That setup flag remains available to
prepare NVM ahead of time. On-demand preparation retains the saved workstation
selection and does not rerun package, service, or agent setup.
Existing NVM installations are retained. Custom `NVM_DIR`
installations remain usable through their original manager: omit this setup
flag for them. Incomplete or unsafe default directories require explicit repair.
Preparation uses NVM's documented
[profile opt-out](https://github.com/nvm-sh/nvm#install--update-script), so Bash,
Zsh, Fish, and agent shells can keep their current startup behavior.

Install the checked-out pins deliberately, then select them for individual
commands:

```fish
basaltw node install --project "$HOME/repos/game"
basaltw node install --project "$HOME/repos/authoring-tool"
basaltw node status --project "$HOME/repos/game" --json
basaltw node status --project "$HOME/repos/authoring-tool" --json

cd "$HOME/repos/game"
basaltw node exec -- npm ci
basaltw node exec -- npm run doctor
basaltw node exec -- npm run build

cd "$HOME/repos/authoring-tool"
basaltw node exec -- npm ci
basaltw node exec -- npm run dev
```

`basaltw node exec` selects the project runtime for each command and works in
fish without sourcing Bash's `scripts/use-node.sh` or NVM initialization.
Repeat `node install`/`node status` for a sibling when its own pin requires
another runtime. An explicit `--version` can override a pin while still
respecting its Node engines. If a project needs a different npm or another
package manager, install its reviewed exact version with
`basaltw node install --project PATH --package-manager npm@VERSION` (or
`pnpm@VERSION`/`yarn@VERSION`); consult the project's metadata first.
See [project tooling](../agents/project-tooling.md) for runtime selection details.
Selection prefers a matching installed NVM runtime and falls back to `node`
on `PATH` when it satisfies both the project pin and Node engines. `node status`
shows the runtime source (NVM or PATH) separately from the pin or engine that
selected it; JSON exposes this as `runtime_source`. A PATH runtime is not
assigned an NVM maintenance owner merely because its version matches one.
Setup probes language versions outside repositories with Corepack downloads
and project policy disabled; these host probes do not establish project readiness.

## Repositories and native authoring

Keep related checkouts under the selected workspace root when the project's
scripts expect siblings. Use explicit paths for managed worktrees and let each
repository's instructions own its maintenance. Repeated `--repo HTTPS_URL`
clones missing repositories after local authentication; setup retains existing
checkouts without pulling, installing dependencies or running project scripts.
See [workspace conventions](../agents/project-tooling.md#related-checkouts-and-host-roles).

Use a project's documented Electron launch for native file dialogs and sync.
Browser authoring remains useful when a browser can reach its local URL.
For agent-driven GUI work, start `basaltw desktop --native start` and obtain
the owner's monitor/input consent; see [native desktop work](cachyos-desktop.md).
Package setup grants no consent.

`basaltw local cachyos-doctor --json` inventories the saved selections, native
development commands/packages, system pkg-config modules, and NVM loadability.
For selected game tooling it also records parseable `cc`, Clang, Clang-Format,
Clang-Tidy and LLVM symbolizer versions using bounded terminal-only probes.
Null versions remain unverified; an installed command does not establish
compatibility with a project's lint configuration or compiler baseline.
The bundle supplies distribution LLVM without choosing an exact LLVM major.
The doctor's unversioned tool observations do not establish a project's chosen
formatter/compiler baseline. Arrange and verify required versioned tools
separately. The doctor never launches a game or authoring tool, builds repository
code or tests the GPU. Run the project's declared checks after setup and record
the host on which they actually ran.

## Capture backends and post-refresh checks

`basaltw agent graphics doctor --context xvfb --json` explicitly probes an
isolated GLX context; `--context display` uses the invoking session's existing
display (GLX/Xwayland), leaving native Wayland unqualified. It reports process
and context outcomes, renderer/version and software/accelerated status without
claiming frame rendering, GPU timing, physical HDR or latency. Ordinary discovery
does not launch this probe. Add `--require-software` to fail unless that GLX
context reports a recognized software renderer; it does not change drivers or
qualify a separate application's context. Use `basaltw agent resources --json`
for the current process's affinity, visible inherited cgroup limits and
timestamped pressure.
See [native context checks and compiler caching](../agents/project-tooling.md#opt-in-native-graphics-context-check)
for shared Debian/CachyOS recipes. Existing container engines can use the
[storage/preflight commands](../agents/project-tooling.md#container-storage-and-sdk-headroom)
without adding container provisioning to this workstation profile.
`agent manifest PATH --capability workstation --json` reports Podman/Docker
PATH availability and any project-declared `container_engines` recipe wrapper,
including argv and cwd when the engine itself is not on PATH. Discovery never
executes wrappers; declarations remain unverified. See the lifecycle reference
below for the manifest example.
Keep Podman's `graphroot` and `runroot` outside searchable project trees,
including `~/repos` and managed worktrees, so image layers and runtime state do
not inflate repository search/indexing. This is a storage placement convention,
not a request to migrate or reconfigure an existing workstation store. See the
[container lifecycle reference](../../common/agent_skills/basaltwater-project-development/references/containers.md).

Choose the backend for the evidence needed, and verify the actual SDL driver
and GL renderer in the application's logs:

| Backend | Useful checks | Limits |
| --- | --- | --- |
| Native KDE Wayland | Interactive rendering, monitor scale and desktop behavior | Requested window/capture sizes can exceed the compositor's backing surface; inspect full image content |
| Isolated Xvfb/X11 with Mesa llvmpipe | Repeatable project screenshots and software reference comparisons | Does not qualify the hardware GPU, native Wayland or monitor behavior |
| SDL offscreen, when the application supports GL with that driver | Hardware captures independent of desktop window size | Driver support and GPU/software selection must be verified by the project; does not qualify desktop interaction |

`LIBGL_ALWAYS_SOFTWARE=1` and `GALLIUM_DRIVER=llvmpipe` are configuration requests,
not proof of software coverage. An SDL offscreen/EGL context can select a
hardware device despite those requests. Qualify each fixture from its actual
renderer. A separate llvmpipe GLX probe does not qualify an offscreen/EGL test;
fixtures that force a driver need their own verification.

For an isolated Xvfb capture, explicitly select `SDL_VIDEODRIVER=x11` and remove
`WAYLAND_DISPLAY` from the child environment so SDL does not prefer the personal
Wayland session. Preserve the selected graphics-capable driver in render-worker
forks; `dummy` is for non-rendering tests. Use private application data and let
the project harness own its virtual display and Xauthority. These choices apply
to a capture process, not to KDE's login/session configuration.

Check `xvfb-run --help` for `--auto-display`. When advertised, use `xvfb-run -d`:
the X server allocates the display number atomically, avoiding simultaneous
`-a` startup races. Older wrappers can use `-a`; serialize launches if their
allocation races. Query capabilities once per harness invocation rather than
starting a throwaway `xvfb-run ... true` availability probe before every capture.
Keep the actual capture server startup and diagnostics separate from that query.

Use the project's own capture harness. Inspect the reported renderer and full
scene content, including image corners and UI: requested PNG dimensions alone
do not prove complete rendering. An offscreen capture does not qualify native
desktop, HDR, monitor scaling or end-to-end frame rate. The optional
[Antistatic capture example](../integrations/antistatic/cachyos.md#capture-backends)
shows a project-specific route.

After refresh, run `basaltw local cachyos-doctor --json` for prerequisites, then
the project's build and declared graphics-check recipe. The optional
[visual check command](visual-comparisons.md#run-a-project-check) retains the
manifest, host/package context, capture settings, logs and timings together:

```fish
basaltw agent manifest /absolute/project --json
basaltw agent visuals check graphics-smoke --repository /absolute/project \
  --settings /absolute/capture-settings.json --json
```

The project must declare and implement that recipe first. Keep paired captures
at the same frame, camera, resolution, renderer, locale, graphics settings and
clean data state. Fix fixture palettes/seed and allow time-based UI transitions
to settle. Record observed runtime/GPU/backend in project outputs, distinguish
capture startup and CPU preparation timings from frame-rate measurements, and
put raw evidence outside Git or in the project's explicitly ignored artifact
directory. Compare like backends; software references and native GPU captures
serve different checks.
