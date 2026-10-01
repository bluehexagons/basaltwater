# Compare captures and Git revisions

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
