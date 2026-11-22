# Visual review recipes and tool preflight

The recipe runner supports a retained gallery and an optional executable
version preflight. The next consumer test is a recipe that produces both
fixed poses and a sequence. Use `visuals check` for the recipe and environment
evidence, `visuals batch` to reopen saved pairs, and `visuals capture` for one PNG
per revision in managed worktrees. See the [capture and receipt guide](../workstations/visual-comparison-capture.md)
and [sequence viewer](../workstations/visual-comparison-review.md#review-transition-sequences).

Antistatic is the pilot consumer. Its recipe owns the named views, frame
pairing, applied-setting assertions, assets and acceptance judgments.
Basaltwater owns invocation, generic tool checks, evidence retention and
review presentation. See the
[Antistatic plan](https://github.com/bluehexagons/antistatic/blob/main/docs/plans/material-authoring-review.md).

## Review a recipe's output

- Use the existing batch manifest as the handoff. Run the pilot recipe with
  [`visuals check --review-manifest`](../workstations/visual-comparison-capture.md#review-a-recipes-captures)
  to retain the gallery with the check report. If the pilot needs whole capture
  sets from two commits, extend managed revision capture to accept that declared
  output while keeping its one-image mode.
- The local fixture and regression tests cover sequence order, gallery
  generation from saved images, standalone batch replay, missing images or
  manifests, incompatible settings and evidence path containment. Failed
  gallery checks retain the recipe result and logs.
- In the consumer pilot, exercise a still and a short sequence. Cover
  identical replay settings and a deliberate setting change with verified
  observed values. Check representative crops against the original captures.
  Setting meanings and comparison axes belong to the project.

## Report the executable a recipe will use

The [recipe version preflight](../workstations/visual-comparison-capture.md#check-a-recipe-tool-version)
records the selected path, observed version line and outcome before running
the recipe. Discovery reports the declaration without running the probe.
Portable fixtures cover two executable selections, mismatch, absence and
probe failure. A Debian Blender fixture used the checked path for a Cycles
CPU render.

Repeat on CachyOS with two installed Blender selections and a deliberate
mismatch. Confirm the recipe invokes the checked path and the mismatch
prevents a render. Schema parity and bake provenance belong to the consuming
project's authoring checks.

## Repeat on another renderer

Use the existing [graphics diagnostics](../workstations/graphics-diagnostics.md)
and [performance sessions](../workstations/performance-sessions.md) for a
repeat on a second available hardware renderer. Run the same reviewed recipe
at the same revision and retain each host's own runtime renderer observations,
environment and captures. Treat the results as separate driver evidence.
Defer new host orchestration until the pilot demonstrates a missing capability.
