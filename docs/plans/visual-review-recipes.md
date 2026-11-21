# Visual review recipes and tool preflight

Start with a project recipe that produces both fixed poses and a sequence.
Run it through the existing commands before extending the runner. Today
`visuals check` retains recipe results and
environment evidence, `visuals batch` reviews a prepared set of pairs, and
`visuals capture` runs one PNG per revision in managed worktrees. Use those
interfaces for the first pilot. See the [capture and receipt guide](../workstations/visual-comparison-capture.md)
and [sequence viewer](../workstations/visual-comparison-review.md#review-transition-sequences).

Antistatic is the pilot consumer. Its recipe owns the named views, frame
pairing, applied-setting assertions, assets and acceptance judgments.
Basaltwater owns invocation, generic tool checks, evidence retention and
review presentation. See the
[Antistatic plan](https://github.com/bluehexagons/antistatic/blob/main/docs/plans/material-authoring-review.md).

## Review a recipe's output

- Use the existing batch manifest as the handoff. Pilot a way for the
  recipe result to identify that manifest and make the generated gallery
  easy to open from the check report. Keep existing standalone batch usage
  available. If the pilot needs whole capture sets from two commits, extend
  managed revision capture to accept that declared output while keeping its
  one-image mode.
- Check the manifest and its referenced images within the retained recipe
  evidence. Reuse the gallery's metadata compatibility checks and the
  runner's optional origin receipts. Leave setting meanings and deliberate
  comparison axes to the project. Preserve frame order, crops and the
  original observations. Preserve the failed run's report and logs when
  output or evidence is missing, malformed or incompatible.
- Exercise a fictional recipe with a still and a short sequence, then the
  consumer pilot. Cover identical replay settings, a deliberate setting
  change with verified values, a missing frame, an incompatible pair and a
  missing manifest. Check that existing single-image recipes still run and
  that gallery generation uses the saved evidence without another render.

## Report the executable a recipe will use

The [manifest](../agents/agent-environment.md) currently declares executable
names for discovery. Pilot an opt-in recipe check for a project-selected
executable and version using Blender. The project selects and pins the binary
per recipe. Basaltwater should surface the selected path, observed version and
result in the check report. Reuse bounded version probes where they fit, and
make discovery safe to run without selecting tools, downloading packages or
changing PATH. Distinguish a missing tool, an unexpected version, a failed
probe and a verified match before expensive work starts. Verify on CachyOS
with two recipe selections and a deliberate mismatch, then cover the portable
check with executable fixtures. Leave schema parity and bake provenance to the
consuming project's authoring checks.

## Repeat on another renderer

Use the existing [graphics diagnostics](../workstations/graphics-diagnostics.md)
and [performance sessions](../workstations/performance-sessions.md) for a
repeat on a second available hardware renderer. Run the same reviewed recipe
at the same revision and retain each host's own runtime renderer observations,
environment and captures. Treat the results as separate driver evidence.
Defer new host orchestration until the pilot demonstrates a missing capability.
