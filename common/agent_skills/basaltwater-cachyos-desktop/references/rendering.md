# Native rendering evidence

Use the project's capture harness and current account. Choose a backend for the
check rather than changing the workstation's compositor or graphics packages.
Read the project's rendering and headless documentation first.

- Native Wayland checks interactive rendering and monitor behavior. Oversized
  windows can have a backing surface smaller than the requested capture size.
- Isolated Xvfb/X11 plus pinned llvmpipe supports repeatable software references.
  Explicitly set `SDL_VIDEODRIVER=x11` and remove `WAYLAND_DISPLAY` for the child;
  keep capture data and Xauthority private. This does not test the hardware GPU.
- Supported SDL offscreen GL can render independently of desktop window size.
  Verify the actual GL renderer; offscreen does not imply hardware acceleration
  and does not qualify native desktop interaction.

Preserve graphics-capable `SDL_VIDEODRIVER` settings through child/worker forks.
Clear an inherited `dummy` driver only when starting a rendering process. Do not
strip an explicit `x11` or `offscreen` selection as generic headless cleanup.

Query `xvfb-run --help` once per capture harness invocation. Use `-d` when
`--auto-display` is advertised: Xvfb allocates the display atomically. Otherwise
use the wrapper's supported `-a` mode and serialize if startup races occur.
Avoid an extra `xvfb-run ... true` probe: it starts another server before the
actual capture. Capability discovery and rendering checks are separate.

After a workstation refresh, discover declared project recipes:

```fish
basaltw agent manifest /absolute/project --json
basaltw agent visuals check graphics-smoke --repository /absolute/project \
  --settings /absolute/capture-settings.json --json
```

Run only a trusted recipe whose scope is authorized by the user's task. The
project must declare and implement it. The check preserves the current checkout,
toolchain and selected backend, retains private environment/settings/log/timing
evidence, and runs no setup. Project scripts may write observations/captures to
`BASALTWATER_VISUAL_EVIDENCE` and read `BASALTWATER_VISUAL_SETTINGS`. Requested
settings are context; verify that the rendering worker applied them.

Check decoded image content, especially known features near all four edges, at
1440p and 4K when those resolutions matter. PNG dimensions, a successful CLI,
installed packages and a zero recipe exit status do not establish complete
rendering. Retain actual renderer/GPU/backend, runtime, frame, camera, resolution,
scale, locale and graphics settings with captures. Fix palettes/seed, isolate
application data and settle time-based transitions before paired comparisons.
Use matching backends for comparisons, and report startup/CPU-preparation
timings separately from GPU work and end-to-end FPS.

Antistatic's observed oversized Wayland SDR PNG clipping is worked around with
`SDL_VIDEODRIVER=offscreen` plus its capture harness's `--no-xvfb`; retain that
workaround until its SDR capture/presentation framebuffer is independent of the
desktop window. Interactive HDR/scaling and desktop control require their own
checks; do not claim them from offscreen evidence or start a portal automatically.

See [CachyOS capture guidance](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_GAME_DEVELOPMENT.md#capture-backends-and-post-refresh-checks)
and [visual check behavior](https://github.com/bluehexagons/basaltwater/blob/main/docs/VISUAL_COMPARISONS.md#run-a-project-check).
