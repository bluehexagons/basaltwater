# Capture images for a visual comparison

[Topic index](visual-comparisons.md)

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

The recipe must exit zero with its recorded settings intact. Requested version,
receipt and gallery checks also contribute to the result. The project owns
image decoding, complete-content checks, renderer
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

Output follows the [private storage and new-directory policy](visual-comparison-review.md#compare-existing-captures).
Use `--output` for the active ignored project artifact directory. Neither
setup, refresh, doctor nor manifest automatically runs these checks. For native
capture backend selection, see [CachyOS game development](cachyos-game-development.md#capture-backends-and-post-refresh-checks).

For performance recipes, opt in with `--performance-session` or explicitly
request `--power-profile performance|power-saver` for a command-scoped hold.
See [performance sessions](performance-sessions.md) for power preferences,
clock/thermal/workload snapshots, reservations, cleanup and measurement scope.
Project scripts retain all performance and image acceptance thresholds.

## Check a recipe tool version

Add an optional `version_check` object to the recipe in `basaltwater-agent.json`:

```json
"version_check": {
  "executable": "./tools/blender",
  "expected": "Blender 4.5.2"
}
```

Select an installed command name, a path relative to the recipe directory,
or an absolute path. Keep private host paths out of shared declarations.
Use the selected executable directly and keep any additional PATH prerequisites
in `requires`. Choose a tool whose `--version` prints its version on the
first stdout line without installing packages or activating a toolchain.

When an explicit visual check runs this recipe, it checks the selected tool
before launching the recipe or taking performance snapshots. The probe runs
in the recipe's directory with a five-second deadline and a 4 KiB stdout
limit. It matches the first line with surrounding whitespace removed.
The private `tool_version` record in `check.json` gives the declaration, selected
path, observed line, and outcome. A `missing`, `probe-failed` or
`unexpected-version` result stops the recipe and retains the report and
environment evidence. No recipe log is created before launch.

For a `verified` match, the recipe receives the selected absolute executable
path in `BASALTWATER_VISUAL_TOOL`. Its script should invoke
`"$BASALTWATER_VISUAL_TOOL"` so the checked executable is the one used.
PATH is inherited. Basaltwater discovery only displays the declaration.
The project still owns the record of which executable and renderer produced
its artifacts.

## Review a recipe's captures

When a recipe writes a [batch manifest](visual-comparison-review.md#review-a-batch),
generate the gallery as part of the same check:

```bash
basaltw agent visuals check pause-menu --repository ~/repos/my-game \
  --review-manifest captures/review.json --json
```

The recipe writes the selected manifest below `BASALTWATER_VISUAL_EVIDENCE`.
Image paths in the manifest are relative to it and must resolve inside the
same evidence directory. Keep that directory's `review-gallery` subdirectory
available for the viewer. Projects choose the views, their order, the observed
settings to compare, and any expected coverage categories.

After the recipe and any requested receipt checks pass, Basaltwater builds a
strict batch gallery from the saved captures. The `review` object in
`check.json` and the JSON response gives the manifest path, viewer path, batch
report path and batch ID on success. An unavailable or incompatible gallery
fails the opted-in check and retains the recipe's exit code, logs, and captures.
The gallery is private and ready for inspection. Visual acceptance still
depends on reviewing the captures and exercising the intended workflow.

Queued desktop checks accept the same flag. Read `review` in the returned
task's `check_report` after completion. To review an existing batch again,
run `visuals batch` on the retained manifest in a new output directory.

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
