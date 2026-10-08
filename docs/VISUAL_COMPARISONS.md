# Check rendering and compare captures

## Run a project check

After a workstation refresh or graphics/toolchain change, use an explicit
project-owned check rather than treating installed packages as proof of rendering.
Declare a reviewed recipe in the project's `basaltwater-agent.json`:

```json
{
  "version": 1,
  "recipes": {
    "graphics-smoke": {
      "description": "Check fixed-frame captures and complete 1440p/4K image content",
      "argv": ["./scripts/check-graphics.sh"],
      "requires": ["python3"]
    }
  }
}
```

Then run it deliberately, using the project's selected toolchain:

```bash
basaltw agent visuals check graphics-smoke --repository ~/repos/my-game \
  --settings ~/capture-settings.json --timeout 600 --json
```

The recipe runs in its declared repository-relative directory in the current
checkout. Basaltwater does not switch branches, install dependencies, select a
Node runtime, or interpret shell operators/placeholders in its argv. Use a
reviewed script for shell logic and the project's normal runtime selection.
See [environment declarations](AGENT_ENVIRONMENT.md) for recipe validation.
Missing declared requirements and unknown recipes fail before execution.

`check.json` records the recipe, argv, working directory, UTC start/end times,
duration, timeout, return code and outcome. `check.log` retains combined stdout
and stderr. `environment.json` retains the read-only environment manifest,
including executable paths, commit and dirty state; it also records OS/kernel,
architecture and an allowlist of display/SDL/Mesa environment settings. When
pacman is available, a bounded read-only query records installed Mesa, SDL,
graphics-driver and Xvfb package versions from a fixed list. Missing optional
packages or an unavailable package query do not fail the project check.
No tool-version shims or renderers run as part of this metadata collection.

The child inherits the caller's environment, including `SDL_VIDEODRIVER`.
`BASALTWATER_VISUAL_EVIDENCE` points to the new evidence directory and
`BASALTWATER_VISUAL_SETTINGS` points to its private, read-only settings copy
(an empty JSON object when `--settings` is omitted). Project scripts can use
these paths for PNGs, observed settings and renderer logs. Changing the settings
copy fails the check. Requested settings and inherited environment are context;
the project must record the actual backend, GPU/software renderer, runtime,
dimensions and settings used by its rendering process, including child workers.

Success means that the declared recipe exited zero with its recorded settings
intact. The project owns image decoding, complete-content checks, renderer
assertions and performance thresholds. Generic GPU and UI readiness remain
unverified; a prerequisite check, PNG header or exit code alone cannot verify
them. Use 1440p/4K fixtures with known content near every edge to detect clipped
or black regions, and retain the actual rendering observations with the captures.

Checks have a 1–3600 second process-group deadline (default 600), disconnect stdin,
return nonzero on failure, and retain evidence on failure, timeout or interruption.
They run as the invoking account and execute trusted project code, without a
sandbox or automatic desktop-portal access. Review logs/settings before sharing;
only allowlisted environment values are recorded, but project logs can contain
anything the project prints. No evidence is published automatically.

Output uses the same private storage and new-directory policy as comparisons
below. Use `--output` for the active ignored project artifact directory. Neither
setup, refresh, doctor nor manifest automatically runs these checks. For native
capture backend selection, see [CachyOS game development](CACHYOS_GAME_DEVELOPMENT.md#capture-backends-and-post-refresh-checks).

## Compare existing captures

`basaltw agent visuals compare` turns two PNG captures into a standalone HTML
viewer with synchronized scrolling and zoom, an adjustable overlay, amplified
pixel differences, changed-pixel counts, and capture settings beside each image.
It uses the existing capture tools; no graphics application or browser is
installed by this command.

```bash
basaltw agent visuals compare before.png after.png \
  --before-settings before.json --after-settings after.json --json
```

Open the returned `viewer` file in a browser. Images and metadata are embedded;
the viewer needs no server, external scripts, or network access. Follow the
[browser session policy](BROWSER_AUTOMATION.md#choosing-playwright-or-collaborative-preview)
when automating it, or open it on the managed native desktop when that is the
requested surface. A viewer intended for the T3 client needs an explicitly
requested managed publication because its browser cannot read VM-local files.

Output defaults to a new private directory under
`~/.local/state/basaltwater/visuals/`. `--output DIRECTORY` chooses a new artifact
directory and refuses existing paths. PNGs are read without following their
final symlink, with limits of 16 MiB, 16 megapixels, and 16384 pixels per side.
Metadata JSON must be a regular file no larger than 64 KiB and contain an object
with finite numeric values.
The original images and settings remain unchanged. `comparison.json` records
their paths, dimensions and SHA-256 hashes alongside the supplied settings.

Overlay and difference require equal dimensions; mismatches remain visible
side by side and disable those modes rather than rescaling the images.
Differences use browser-decoded RGBA pixels, including alpha changes. Counts
are observations, not automatic pass/fail thresholds. Record scene, camera,
seed, simulation tick/frame, resolution, renderer, device and application
version when those affect the comparison. Different rendering backends or
timing can produce differences without a regression.

## Capture two revisions

Use a project-owned capture harness that builds and renders from its current
working directory, accepts the same scene/camera settings file on both runs,
and writes exactly one PNG to its requested output path:

```bash
basaltw agent visuals capture \
  --repository ~/repos/my-game \
  --before HEAD~1 --after HEAD \
  --settings ~/capture-settings.json --timeout 600 --json -- \
  ./tools/capture-scene --settings '{settings}' --output '{output}'
```

Both revisions are resolved to commits before capture starts. Each runs in its
own managed `agent/visual-…-before` or `agent/visual-…-after` worktree; the primary
checkout and its assets are never switched. The same argv and settings values
are used for both captures. `{output}` becomes that run's absolute PNG path;
`{settings}` becomes the shared absolute settings file. Arguments are passed
without shell evaluation. Use a stable external harness path when the harness
does not exist at both revisions. The caller's active toolchain and environment
are inherited; the harness owns dependency installation, building, resource
imports, deterministic input, scene/camera selection, and rendering.
The shared settings copy is read-only. A command that changes its contents
stops the pair rather than generating a comparison with inconsistent settings.

For example, a game harness might consume:

```json
{
  "scene": "lighting-test",
  "camera": {"position": [0, 2, 4], "target": [0, 1, 0]},
  "seed": 42,
  "simulation_tick": 120,
  "resolution": [1280, 720],
  "renderer": "mesa-software"
}
```

The settings schema belongs to the harness. Basaltwater records and replays it;
it cannot establish that an arbitrary harness applied every setting. A fixed
camera and seed do not promise identical physics across toolchain versions.
Recorded commits include tracked assets at each revision, not uncommitted
changes from the primary checkout. Git LFS and external assets must be made
available by the project's normal asset workflow. Keep mutable build caches
and generated assets separate between worktrees.

Each command has a 1–3600 second process-group deadline (default 600). A failure
stops the pair, returns nonzero status, and retains `capture.json`, available
PNGs, private per-run logs, settings, and worktree paths for diagnosis. Success
also produces the viewer and resolved commit metadata. Capture commands run as
the invoking account; only provide a trusted project capture command and
non-secret settings. No command publishes evidence automatically.

Worktrees are deliberately retained on success and failure so capture output
or build changes cannot be silently discarded. Inspect them using
`basaltw agent workspace status WORKTREE --json`; use the normal managed removal
workflow after resolving changes and ensuring the task branch is merged into
the primary checkout. See the installed workspace and shared-assets skills.
