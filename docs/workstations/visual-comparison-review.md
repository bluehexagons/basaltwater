# Compare and review visual captures

[Topic index](visual-comparisons.md)

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

PNG comparisons and batch galleries default to **T3 compact**, with
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

### Compare linear HDR PFM captures

Use two `.pfm` files for linear HDR comparisons:

```bash
basaltw agent visuals compare before.pfm after.pfm \
  --exposure -2 --relative-floor 0.000001 --json
```

The PFM reader supports RGB `PF` and grayscale `Pf`, little- and big-endian
float32 samples, bottom-to-top rows, comments in the header and a finite,
nonzero scale. The scale's sign selects byte order; its magnitude multiplies
samples. Negative and HDR values are retained. NaN, Infinity, scale overflow,
truncated payloads and trailing payload bytes fail before output creation.
Inputs must be regular files with no final symlink. Each file is bounded to
16 MiB, 16 megapixels and 16384 pixels per side; the header is limited to
4096 bytes. The byte limit also bounds decoded sample storage.

`comparison.json` records hashes, dimensions, channels, scale, sample range,
finite-value checks and linear error metrics: mean/max absolute error, root
mean square error, mean/max relative error and changed sample count. Metrics
include every channel, using **before** as the reference. Relative error is
`abs(after - before) / max(abs(before), relative_floor)`; `--relative-floor`
must be finite and positive and controls behavior near zero. It is a numerical
denominator floor, not a rendering acceptance threshold. Unrepresentable
metrics fail explicitly. Equal dimensions and channel counts are required for
metrics, overlays and differences; mismatches retain side-by-side viewing and
record unavailable metrics. PNG/PFM mixed pairs are refused.

The standalone PFM viewer provides synchronized scroll, zoom, a 50% linear
overlay, absolute linear differences and exposure from -32 through 32 stops.
`--exposure` sets its initial exposure. Display multiplies linear values by
`2^exposure`, clamps to `[0, 1]`, then encodes sRGB; negative values display
black and bright values clip. Exposure changes presentation only. Metrics
always use the original scaled linear samples. The tool assumes the project
provided linear samples in matching color spaces; PFM has no color profile.

No error metric selects pass/fail. Projects own rendering tolerances, fixture
interpretation and acceptance. A
successful command means comparison evidence was generated. Batch galleries
and managed revision capture continue to accept PNGs; project recipes can
produce PFM pairs and invoke `visuals compare` separately.

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
