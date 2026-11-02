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

Game packaging and package validation currently belong to CI and Debian.
CachyOS development still runs the relevant source, native, rendering and
desktop checks, including the normal tooling tests. Do not run local packaging,
archive, package-smoke or package ABI checks there. This also applies to
runtime-layout and packaging-code changes. Test a package on CachyOS only when
explicitly instructed to test a CI-built artifact; use that artifact rather
than rebuilding it locally. Follow the game's
[packaging host policy](https://github.com/bluehexagons/antistatic/blob/main/docs/development/testing.md#packaging-hosts).

## Capture backends

Use the game's
[development setup](https://github.com/bluehexagons/antistatic/blob/main/docs/development/setup.md#host-boundaries)
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
[headless capture guide](https://github.com/bluehexagons/antistatic/blob/main/docs/development/captures.md)
for current limitations. A complete capture does not qualify desktop interaction,
HDR, display scaling or end-to-end frame rate.

Keep detailed captures and run evidence private. Project recipes own image
assertions; the general [visual check command](../../workstations/visual-comparisons.md#run-a-project-check)
can retain their settings, logs and results without copying the harness here.

Antistatic's `basaltwater-agent.json` registers `capture-menus`,
`capture-lighting` and `capture-model-inspection`. Select its Node runtime,
then run `basaltw agent visuals check RECIPE --repository . --settings FILE`.
Its adapter reads native capture options from `{"args":["--software-gl"]}`
and places captures/metadata under the check's evidence directory. Follow the
project's [recipe contract](https://github.com/bluehexagons/antistatic/blob/main/docs/development/captures.md#optional-basaltwater-recipes)
for selections and a native invocation without Basaltwater. Updated adapters
also produce the optional strict receipt from successful native/TypeScript
build records, tracked asset inventories and complete runtime observations:

```fish
basaltw agent visuals check capture-lighting --repository . --settings FILE \
  --verify-evidence --require-artifact-kind native \
  --require-artifact-kind typescript --require-artifact-kind asset --json
```

This requires refreshed Basaltwater's explicit verification indicator and a
clean checkout at the intended full revision. Strict requests reject dirty
source before capture; ordinary checks keep normal captures without a receipt. The
adapter's optional `expect` settings assert observed frame, graphics and GL
identity across every capture. Literal `args` record capture-command options;
they do not substitute for renderer observations. See the project's
[build-origin receipt contract](https://github.com/bluehexagons/antistatic/blob/main/docs/development/captures.md#build-origin-receipts)
for artifact scope, native Windows invocation and limits. Isolate build outputs,
prevent concurrent writers and review original metadata before comparison.

For before/after effects, use the deterministic showcase fixtures and pin the
same frame, camera, graphics, data state and actual renderer. Some effect
batch manifests have omitted resolved graphics and GL identity even though
individual `.capture.json` records retained them. Keep a strict-comparison
refusal in the evidence. Until the project batch writer carries that metadata,
compare the original per-image records before interpreting pixel equality;
missing runtime evidence still requires a new capture. See the
[general comparison guidance](../../workstations/visual-comparisons.md#compare-existing-captures).

## Scripted desktop checks

Use the game's [agent-play protocol](https://github.com/bluehexagons/antistatic/blob/main/docs/development/agent-play.md)
and the [native task workflow](../../workstations/cachyos-development.md) in the
existing approved KDE session. Establish the intended controller roster and
scene readiness, then assert observable menu/controller state after each action.
Presentation countdowns and effects can need rendered frames between bounded
simulation steps; one long simulation-only step does not establish readiness.
Keep SDL processing/rendering until the parent has captured the monitor, then
quit the owned process and wait for the same task's final status. Give each run
fresh transcript, screenshot and capture-handshake paths.

For black monitor captures, inspect native control status, use the documented
[sleeping-display recovery](../../workstations/cachyos-desktop.md#agent-permission-and-portal-access)
while enabled, and inspect a fresh capture before input. Project-protocol input
and application screenshots do not establish physical gamepad, audible output,
display pacing or portal keyboard/pointer delivery.
