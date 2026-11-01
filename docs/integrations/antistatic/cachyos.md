# Antistatic development on CachyOS

Prepare the host with [CachyOS native game tooling](../../workstations/cachyos-game-development.md).
This optional workflow covers the game, TypeScript/Go components, Animator and
Blender sources. Keep project runtime/library pins and release requirements in
their owning repositories; host packages are development prerequisites.

## Related repositories

When using scripts that expect sibling repositories, place the checkouts below
under the selected workspace root (`~/repos` by default). Clone only what the
task needs; this is a dependency map, not a list of deployed systems.

- `antistatic`, the game.
- `antistatic-animator`, the Electron authoring tool.
- `capacitor`, `easing`, `trace`, and `antistatic-translations`, direct packages.
- `antistatic-assets`, editable Blender sources.
- `basaltwater`, an optional source checkout for host-tool development and
  references; the installed CLI uses its own managed source directory.

A sibling checkout does not replace a tagged package installed by `npm ci`.
Use the project's dependency policy for coordinated changes. Asset and Animator
checkouts serve authoring; ordinary game builds use committed runtime exports.
Authenticate any restricted repositories locally. Each checkout's `AGENTS.md`
and contributor guide remain authoritative.

## Select runtimes and run project checks

The game and Animator can select different Node pins. Read their version files
and package engines, then install/select each pin explicitly:

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

`basaltw node exec` selects a runtime for each command in fish without sourcing
NVM or Bash helper scripts. Use the project's portable bootstrap and doctor
after host setup. A successful host doctor does not establish that native
compiler/formatter versions match the project's baseline.
Use Animator's documented Electron launch for native dialogs and live sync;
its browser mode is another project-owned option. For Blender sources, use the
version and guarded export/check commands declared by the asset checkout.

## Capture backends

Use the game's
[environment guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development-environments.md)
for isolated displays and lobby test preparation. Its screenshot harness owns
SDL driver selection and render-worker inheritance. For a hardware capture
larger than the desktop, the Linux renderer supports:

```fish
cd "$HOME/repos/antistatic"
env SDL_VIDEODRIVER=offscreen basaltw node exec -- npm run screenshot:stage -- \
  --stage 'Visual Test' --cpus 0 --frame 180 --resolution 3840x2160 \
  --no-xvfb --verbose --out /absolute/new-artifact/visual-test-4k.png
```

Inspect the actual GL renderer and complete scene content. Recorded project
checks found that an oversized native Wayland PNG could have the requested
dimensions while containing clipped/black regions; an offscreen capture avoided
that presentation limit. This is a project workaround, not a guarantee for
all drivers. See the game's
[headless capture guide](https://github.com/bluehexagons/antistatic/blob/main/docs/headless-mode.md)
for current limitations. A complete capture does not qualify desktop interaction,
HDR, display scaling or end-to-end frame rate.

Keep detailed captures and run evidence private. Project recipes own image
assertions; the general [visual check command](../../workstations/visual-comparisons.md#run-a-project-check)
can retain their settings, logs and results without copying the harness here.
