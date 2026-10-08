# Antistatic development on CachyOS

Use `agent_cachyos` on an existing, fully updated CachyOS KDE workstation.
The primary workstation role is remote Godot development and playtesting; see
the [remote development guide](CACHYOS_DEVELOPMENT.md). This guide covers the
secondary Antistatic/native/Electron workflow: editing and running the core
game, its direct TypeScript dependencies, Blender source assets, and Antistatic
Animator. Production builds, publishing, and servers use separate systems.
Rolling desktop tools need not reproduce a release SDK or Debian's reviewed
compiler, formatter, graphics, or packaged-runtime baseline.

For a first installation, use the [fish installer](CACHYOS.md#quick-start)
with the options below after `--local-setup agent_cachyos`. If the launcher is
already installed, preview your first development selection in fish as the
normal desktop account, without `sudo`:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop \
  --node --python --git-lfs --game-dev --blender --dry-run
```

Remove `--dry-run` to apply. `--node` and `--python` select the general language
tools. Project runtime installs prepare NVM on demand; optionally add
`--node-versions` to prepare it during setup.
See [local setup](CACHYOS.md) for T3 modes and package prompts, and
[maintenance](CACHYOS_MAINTENANCE.md) for later changes to a saved selection.

## Development packages

| Option | Capability |
| --- | --- |
| `--game-dev` | CMake, Ninja, Python, pkg-config, GDB, ccache; SDL3/SDL3_image, FreeType, Vorbis, libusb, GLEW, OpenAL; Xvfb/xauth/OpenGL diagnostics; GTK3/NSS/audio/X11 host libraries used by project-installed Electron |
| `--node` | Node/npm/pnpm when missing; existing PATH runtimes retain their original manager |
| `--node-versions` | User-local NVM for explicit per-project Node installs; no runtime download, shell initialization, or default-version change during setup |
| `--git-lfs` | Git LFS and missing user filter defaults for model source repositories |
| `--blender` | Native Blender, Wayland decorations, and existing task-scoped KDE automation prerequisites |
| `--av-tools`, `--gl-tools` | Optional asset processing and additional Vulkan/API-trace diagnostics |

`--game-dev` also supplies the existing KDE automation prerequisites for native
Animator work. It starts no GUI, capture, input session, or service. Packages
come from the configured repositories, with their normal headers and distro
versions; setup neither synchronizes pacman databases nor replaces installed
packages. Run CachyOS's normal full update first. Antistatic's portable
`npm run bootstrap:linux` remains available independently of Basaltwater.

Xvfb and xauth allow optional project captures without using the personal
desktop. The workstation's existing graphics stack supplies OpenGL/GBM and
any software renderer. Basaltwater does not select GPU drivers or force a Mesa
variant. Hardware rendering and captures remain project checks, not setup gates.
The [Arch Xvfb package](https://archlinux.org/packages/extra/x86_64/xorg-server-xvfb/files/)
provides both `Xvfb` and `xvfb-run`.

The bundle installs host libraries for project-installed Electron.
Animator's lockfile owns its Electron version and supported desktop
baseline; launch it through its npm scripts. GTK3/NSS are also runtime
dependencies of [Arch's Electron package](https://archlinux.org/packages/extra/x86_64/electron44/).
No sandbox override is configured. Compiler/formatter/shader-validation pins,
managed Playwright, Windows cross-compilation, containers, and storefront
publishing are outside this development setup.

## Select each project's Node version

Antistatic and Animator intentionally use different development pins. Their
`package.json` engines and `.nvmrc` files remain authoritative; do not copy the
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
basaltw node install --project "$HOME/repos/antistatic"
basaltw node install --project "$HOME/repos/antistatic-animator"
basaltw node status --project "$HOME/repos/antistatic" --json
basaltw node status --project "$HOME/repos/antistatic-animator" --json

cd "$HOME/repos/antistatic"
basaltw node exec -- npm ci
basaltw node exec -- npm run doctor
basaltw node exec -- npm run build

cd "$HOME/repos/antistatic-animator"
basaltw node exec -- npm ci
basaltw node exec -- npm run dev:electron
```

`basaltw node exec` selects the project runtime for each command and works in
fish without sourcing Bash's `scripts/use-node.sh` or NVM initialization.
Repeat `node install`/`node status` for a sibling when its own pin requires
another runtime. An explicit `--version` can override a pin while still
respecting its Node engines. If a project needs a different npm or another
package manager, install its reviewed exact version with
`basaltw node install --project PATH --package-manager npm@VERSION` (or
`pnpm@VERSION`/`yarn@VERSION`); consult the project's metadata first.
See [project tooling](PROJECT_TOOLING.md) for runtime selection details.
Selection prefers a matching installed NVM runtime and falls back to `node`
on `PATH` when it satisfies both the project pin and Node engines. `node status`
shows the runtime source (NVM or PATH) separately from the pin or engine that
selected it; JSON exposes this as `runtime_source`. A PATH runtime is not
assigned an NVM maintenance owner merely because its version matches one.
Setup probes language versions outside repositories with Corepack downloads
and project policy disabled; these host probes do not establish project readiness.

## Repositories and native authoring

Keep these primary checkouts beside one another under `~/repos`, or the root
selected with `--agent-workspace /absolute/path`:

- `antistatic`, the game.
- `antistatic-animator`, the Electron authoring tool.
- `capacitor`, `easing`, `trace`, and `antistatic-translations`, direct packages.
- `antistatic-assets`, editable Blender sources.
- `basaltwater`, an optional source checkout for host-tool development and
  references; the installed CLI uses its own managed source directory.

Use repeated `--repo HTTPS_URL` during setup to clone missing repositories.
Authenticate private repositories locally first. Setup retains existing
checkouts; it does not pull them, install project dependencies, or run their
scripts. Each checkout's `AGENTS.md` owns its maintenance and validation rules.
No website, sandbox, database, or matchmaking server is required for this setup.
See [project workspace conventions](PROJECT_TOOLING.md#related-checkouts-and-host-roles)
for existing origins, managed worktrees, and separate T3 project registration.
Cloning a sibling does not change the tagged dependency installed by `npm ci`.

Use Animator's Electron mode for native file access and live sync. Its Vite
browser mode remains useful when an available browser can reach the local URL.
For agent-driven native GUI work, start the existing KDE portal session with
`basaltw desktop --native start` and obtain the owner's monitor/input consent;
see [native desktop work](CACHYOS_DESKTOP.md). Package setup grants no consent.

`basaltw local cachyos-doctor --json` inventories the saved selections, native
development commands/packages, system pkg-config modules, and NVM loadability.
It never launches the game or Animator, builds repository code, or tests the GPU.
Run the game's doctor/build and open Animator after setup. Difficult native,
formatter, screenshot, and full-suite validation can remain on the Debian VM;
record the host on which a check actually ran.

Use the game's [environment guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development-environments.md)
for capture display isolation and fresh-checkout lobby test preparation. These
checks belong to the game and work independently of Basaltwater.

## Capture backends and post-refresh checks

Choose the backend for the evidence needed, and verify the actual SDL driver
and GL renderer in the application's logs:

| Backend | Useful checks | Limits |
| --- | --- | --- |
| Native KDE Wayland | Interactive rendering, monitor scale and desktop behavior | Requested window/capture sizes can exceed the compositor's backing surface; inspect full image content |
| Isolated Xvfb/X11 with Mesa llvmpipe | Repeatable project screenshots and software reference comparisons | Does not qualify the hardware GPU, native Wayland or monitor behavior |
| SDL offscreen, when the application supports GL with that driver | Hardware captures independent of desktop window size | Driver support and GPU/software selection must be verified by the project; does not qualify desktop interaction |

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

Antistatic's screenshot scripts own this selection. For a hardware capture
larger than the desktop, its Linux renderer supports:

```fish
cd "$HOME/repos/antistatic"
env SDL_VIDEODRIVER=offscreen basaltw node exec -- npm run screenshot:stage -- \
  --stage 'Visual Test' --cpus 0 --frame 180 --resolution 3840x2160 \
  --no-xvfb --verbose --out /absolute/new-artifact/visual-test-4k.png
```

Inspect the reported GL renderer and complete scene content, including corners
and UI where requested. In the observed Antistatic SDR path, oversized native
Wayland PNGs could have the requested dimensions while containing clipped/black
regions; offscreen captures produced complete 4K content on Intel Mesa. Keep the
workaround until that project uses an independent SDR presentation/capture
framebuffer. See the game's
[headless capture guide](https://github.com/bluehexagons/antistatic/blob/main/docs/headless-mode.md)
for current limitations and fixture settings. A working offscreen capture does
not establish native desktop, HDR, monitor-scaling or end-to-end FPS behavior.

After refresh, run `basaltw local cachyos-doctor --json` for prerequisites, then
the project's build and declared graphics-check recipe. The optional
[visual check command](VISUAL_COMPARISONS.md#run-a-project-check) retains the
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
put shared evidence in the project's active ignored artifact directory. Compare
like backends; software references and native GPU captures serve different checks.
