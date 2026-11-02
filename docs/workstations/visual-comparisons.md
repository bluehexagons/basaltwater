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
# Queue in the existing CachyOS KDE session, retaining results after disconnect:
basaltw desktop --native develop check graphics-smoke --project ~/repos/my-game \
  --settings ~/capture-settings.json --timeout 600 --json
```

The recipe runs in its declared repository-relative directory in the current
checkout. Basaltwater does not switch branches, install dependencies, select a
Node runtime, or interpret shell operators/placeholders in its argv. Use a
reviewed script for shell logic and the project's normal runtime selection.
See [environment declarations](../agents/agent-environment.md) for recipe validation.
Missing declared requirements and unknown recipes fail before execution.

`check.json` records the recipe, argv, working directory, UTC start/end times,
duration, timeout, return code and outcome. `check.log` retains combined stdout
and stderr up to 16 MiB, while continuing to drain excess output. The report's
`log_limit_bytes` and `log_truncated` record that bound and any truncation;
the recipe's outcome is retained even when its output exceeds the quota.
Project-created artifacts have no automatic quota. `environment.json` retains
the read-only environment manifest, including executable paths, commit and dirty state; it also records OS/kernel,
architecture and an allowlist of display/SDL/Mesa environment settings. When
the host's package manager is available, a bounded read-only query records
installed Mesa, SDL, graphics-driver and Xvfb package versions from a fixed list:
Pacman on Arch/CachyOS, or `dpkg-query` on Debian and its derivatives. Debian
records retain architecture qualifiers and exclude removed or unpacked packages.
Source-built project libraries are outside this package inventory. Missing optional
packages or an unavailable package query do not fail the project check.
No tool-version shims or renderers run as part of this metadata collection.

The native development wrapper returns a task ID and `check_report` path under
the task's private directory. Use `develop status TASK_ID --json` for lifecycle
state and inspect the check report for the recipe outcome. See the
[remote development guide](cachyos-development.md) for preview, stopping,
runtime selection and Godot/Electron/native harness conventions.

The child inherits the caller's environment, including `SDL_VIDEODRIVER`.
`BASALTWATER_VISUAL_EVIDENCE` points to the new evidence directory and
`BASALTWATER_VISUAL_SETTINGS` points to its private, read-only settings copy
(an empty JSON object when `--settings` is omitted). Project scripts can use
these paths for PNGs, observed settings and renderer logs. Changing settings
values or types fails the check, including replacing `1` with `true` or `1.0`.
Whitespace and object-key ordering do not affect this comparison.
Requested settings and inherited environment are context;
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
capture backend selection, see [CachyOS game development](cachyos-game-development.md#capture-backends-and-post-refresh-checks).

## Gate captures on runtime and build evidence

Both `visuals check` and `visuals capture` accept `--verify-evidence`.
Add `--require-artifact-kind native --require-artifact-kind typescript
--require-artifact-kind asset` for a game using all three build inputs; each
selector implies verification. Missing receipts, unapplied requested settings,
wrong revisions, missing kinds or changed artifact bytes fail before creating
a revision comparison. Without these options, verification is explicitly
`unverified`; ordinary recipe exit-status checks remain available.

The harness receives the same environment contract in both commands:
`BASALTWATER_VISUAL_SETTINGS`, `BASALTWATER_VISUAL_EVIDENCE`,
`BASALTWATER_VISUAL_REVISION` and `BASALTWATER_VISUAL_RECEIPT`. The last path is
new for each run (and each side of a revision capture). Write this receipt there
after rendering, using actual renderer observations and retained build records.

`BASALTWATER_VISUAL_VERIFY` is `1` when `--verify-evidence` or any required
artifact kind is selected, otherwise `0`. It overrides an inherited value on
every invocation. Adapters should use it to opt into strict build preparation;
the presence of the receipt destination alone does not request verification.
A version-1 receipt contains:

```json
{
  "version": 1,
  "revision": "0123456789abcdef0123456789abcdef01234567",
  "effective_settings": {"quality": "high", "frame": 120},
  "artifacts": [{
    "path": "build/game.node",
    "kind": "native",
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }]
}
```

Every requested top-level setting must match in value and JSON type; effective
settings may include additional observations. Each artifact must name the
intended revision and a unique file below the repository. Digests are checked
against current regular files, limited to 4096 files, 512 MiB each and 2 GiB
total. Include all consumed outputs and asset companions, not just a sample.
Custom lowercase artifact kinds are supported in receipts.

The project owns build-time revision stamping and runtime resource identity.
Do not relabel stale outputs with the current Git HEAD or hash arbitrary files
after capture as a substitute for a build record. This gate checks project
attestations and file bytes; it cannot prove compilation history or which bytes
an arbitrary process loaded. Use isolated output/cache directories and prevent
concurrent writes for the entire build and capture. The receipt and verified
observations are retained with the report. A headless pass still leaves Wayland
capture, input, GPU and UI readiness unverified.

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
[browser session policy](../agents/browser-automation.md#choosing-playwright-or-collaborative-preview)
when automating it, or open it on the managed native desktop when that is the
requested surface. A viewer intended for the T3 client needs an explicitly
requested managed publication because its browser cannot read VM-local files.

Individual comparisons and batch galleries default to **T3 compact**, with
neutral dark panels, smaller controls and metadata collapsed below each image.
Expand **Before metadata** or **After metadata** to inspect the full records;
choose **Roomy** in Layout for larger spacing. Compatibility warnings and manual
inspection controls remain visible. Layout changes affect presentation only;
they do not change the embedded evidence or verification results.

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

Matching requested settings or PNG hashes alone do not establish a controlled
comparison. Check the actual frame, applied graphics settings, backend/device
and complete image content. If a project batch manifest is incomplete, preserve
the strict comparison's refusal. Original per-image runtime records can support
a separate settings check followed by pixel comparison; record that method and
its limits explicitly. Do not invent missing metadata or report the refused
strict check as passed. If runtime observations are missing, repair the project
harness and recapture. Keep randomized scenes or unsettled presentation out of
whole-image equality claims unless the fixture controls them.

## Review a batch

`basaltw agent visuals batch review.json --json` builds one portable gallery
for project-owned stages, cameras and quality levels. Image paths are relative
to the manifest. Each pair supplies actual observed settings; `compare_keys`
selects top-level values that must exist, be non-null and match in value and
type. Use nested objects for camera and renderer observations when appropriate.
Revision and stage labels can differ without making the cameras incompatible.

```json
{
  "version": 1,
  "title": "Prop revision review",
  "compare_keys": ["camera", "quality", "renderer"],
  "pairs": [{
    "id": "rear-high",
    "category": "silhouette",
    "before": {
      "image": "before/rear.png",
      "settings": {"camera": "rear", "quality": "high", "renderer": "project-renderer"}
    },
    "after": {
      "image": "after/rear.png",
      "settings": {"camera": "rear", "quality": "high", "renderer": "project-renderer"}
    },
    "crops": [{"name": "attachment junction", "rect": [120, 80, 160, 120]}]
  }]
}
```

The project owns its stage/camera recipes and the accuracy of observations.
Missing or different comparison settings and image dimensions refuse the batch
before output creation. `--allow-incompatible` explicitly retains these pairs
for side-by-side inspection with a warning, disabling overlay and differences.
It does not establish a controlled comparison or repair missing evidence.

The gallery reuses synchronized zoom/scroll, overlays and pixel differences.
Named crops frame a shared `[x, y, width, height]` rectangle in capture pixels;
they must fit both captures. They focus the viewer and do not redact image
content. Inspect the full view and each crop, enter observations or unresolved
issues, then **Download inspection record** before closing/reloading the tab.
The record includes each manual inspection timestamp and notes, the batch
identity, image hashes and incompatibilities. State is held only in the tab;
no inspection is marked automatically. Downloaded records are private evidence,
not a correctness verdict, and are not written back to the source manifest.

Use categories `silhouette`, `underside`, `junction`, and `detail` across the
batch. The viewer reports absent categories; category presence alone does not
establish coverage. Include front/rear silhouettes, undersides, attachment
junctions and close-ups at useful scales, across relevant quality levels and
in the consuming application. Record defects even when captures succeeded.

Batches accept 1–256 pairs, up to 32 named crops per pair, a 1 MiB manifest,
and at most 128 MiB of embedded image data. Split larger runs. PNG limits and
private output rules match individual comparisons. `batch.json` records
observations, hashes, crops, compatibility issues and missing categories;
`index.html` embeds the images without external resources.

### Review transition sequences

Add `"sequence": {"fps": 12}` at the top level of a batch manifest and list
its pairs in chronological order. Each pair is one synchronized before/after
frame. Include the observed simulation frame/time in each side's settings and
in `compare_keys`, alongside camera and renderer observations. The project
owns pairing, equal capture steps and deterministic transition preparation;
file names alone do not establish synchronization.

The gallery adds a keyboard-accessible frame scrubber and Play/Pause control.
Playback advances both images together, retains zoom/scroll and the selected
comparison mode, and stops at the last pair or a decode error. Incompatible
pairs still disable overlays/differences. Scrubbing, changing views, entering
notes or hiding the tab pauses playback. Playing through a frame never marks
it inspected. Notes and inspection records remain per frame/crop; download
the record to retain them. Playback starts only when requested.

`fps` accepts a finite number greater than zero through 60 and is included in
the batch identity and downloaded record. The browser waits for both images
before advancing; decoding and pixel-difference computation can slow playback.
This is visual sequence review, not a measurement of display cadence. Existing
batch limits apply, including 256 pairs and 128 MiB embedded image data; split
longer transitions. No GIF/video conversion, server or network is required.

## Export and inspect the consumer

Keep interchange recipes in the asset's project. Use the existing declared
`visuals check` recipe boundary to orchestrate this reusable sequence and retain
the result under `BASALTWATER_VISUAL_EVIDENCE`:

1. Save a working `.blend` checkpoint without replacing the source. Record its
   hash, frame and selected objects; inspect it with `agent blender inspect`.
2. Export into a new directory using the project's format, axes, scale,
   modifier, UV, material/baking and animation options. Record actual options
   and require exporter completion plus all companion files.
3. Copy the export and its companions to a relocated directory, then reimport
   into an empty scene. Force texture loading or render to expose missing
   references. Save the imported scene as another checkpoint and inspect it.
4. Compare evaluated bounds, triangle counts, normals, UVs and material-to-face
   assignments using the project's tolerances/mapping. Seams and triangulation
   can change vertex/object counts; equal counts do not prove equivalence.
5. Invoke the project-owned consumer capture recipe on the exported asset.
   Record the actual camera, renderer/device, quality and applied materials.
   Produce a batch manifest with silhouettes, undersides, junctions and details
   for before/after captures. Include both modeling diagnostics and consumer
   captures as appropriate, comparing each with compatible observations.
6. Inspect those views and crops, download the inspection record, and report
   geometry differences, appearance defects and unverified checks separately.

The recipe owns export/import APIs, object identity mapping and consumer
launch/camera semantics; Basaltwater does not infer these from a file extension
or a successful Blender render. A procedural Blender shader may require baking
or a consumer-specific material assignment. Use project-native commands for
manual execution too. Run the declared recipe deliberately, for example:

```bash
basaltw agent visuals check asset-roundtrip --repository ~/repos/my-game \
  --settings ~/asset-review-settings.json --timeout 600 --json
```

`check.json` establishes recipe completion and retained settings; the
project's round-trip report and manual inspection establish the narrower
checks actually performed. Fail the recipe for missing imports/captures or
unapplied settings. Keep intentional intersections configurable in project
settings and pass designated pairs to Blender inspection as described in the
[Blender reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/blender.md).

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
The shared settings copy is read-only. A command that changes its values or
types stops the pair rather than generating a comparison with inconsistent
settings, using the same comparison rules as project checks above.

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
