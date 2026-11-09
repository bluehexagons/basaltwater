# Qualify recorder retention with a native capture-only wait

This is a repeatable qualification recipe, not a record that the live checks
passed. The [retention contract](../workstations/conditional-execution.md#verify-a-recorded-waits-ring-evictions)
has offline coverage. Live results must name client/helper revisions, profile,
geometry, recorder segments, retained evidence and each observed boundary.

## Disposable fixture

Use the existing workstation's Python with PySide6. No dependency installation,
helper start/refresh, consent change or input injection is part of this script:

```text
python3 scripts/conditional_fixture.py --output /absolute/private/new-fixture --seconds 600
```

When operating the native desktop, launch through the authorized native `exec`
route and retain its exact launch/PID identity. First establish sole controller
ownership, inspect helper/control state and preserve unrelated applications.
If another project has an active recorder or concurrent input/capture arrives,
resolve the handoff before continuing; enabled control alone does not establish
exclusive desktop ownership. Keep an interrupted segment and its real failures.

The fixture has a fixed 640×360 logical client area, a scene panel at
`40,100,180,180`, a green identity anchor at `400,100,120,120`, independent counts
of its own input events, and a small changing marker outside those regions.
Monitor pixels and crop origin still require an inspected real capture; Wayland
placement/scaling cannot be inferred from the widget's requested size.

Write `control.json` inside the new fixture bundle using atomic mode-0600 writes:
`{"scene":"blue","serial":1}` changes setup independently of input; use distinct
integer serials (0–1000000) for subsequent red/blue/gray episodes.
`{"quit":true}` closes cooperatively. `state.json` retains counts, setup events,
timestamps and exit reason. Records are limited to 128 events with explicit
truncation; malformed, unsafe or oversized control files close the fixture with
`control-error` and a nonzero exit. The 1–600 second lifetime closes normally.

For an independent offscreen script check, set `QT_QPA_PLATFORM=offscreen` in
that process's environment. Verify scene setup, zero input counts and cooperative
exit from its state file. This checks the fixture only and establishes no native
capture, display mapping or input behavior. Normal project tests do not require Qt.

## Native acceptance

1. Retain private preflight and explicit handoff records. Start a new recorder
   owned by the consuming project. Preserve any earlier recorder artifact and
   outcome; never silently merge concurrent projects into a clean qualification.
2. Inspect a native fixture capture. Calibrate literal scene/anchor regions;
   freeze templates, profile hashes and thresholds before separate held-out
   red/blue/gray episodes. Verify known scenes and gray abstention without input.
   This tiny fixture does not qualify real-game recognition.
3. Run a zero-input `event-wait` for blue while red, with three frames and one
   retained pair. Reserve enough frame/metadata/receipt storage and a bounded
   30-second operation budget. Inspect the actual stop condition and gaps; do
   not report a frame-limit pass if capture or recognition failed early.
4. Preserve the final wait receipt and returned SHA-256 separately. Confirm two
   complete ring evictions, one retained pair, and zero step/input events in
   both the helper recorder and the fixture's independent state.
5. Close the fixture cooperatively, verify normal launch completion and exact
   process absence, then finish/finalize the recorder. Default verification must
   show its two unavailable frames. Supply the explicit retention pin to verify
   and a **new** closeout linked to the prior failed one: expected evictions must
   be separate, retained PNGs must match, and unrelated losses must still fail.
6. For an unexplained-loss negative case, use a separate owned recorder and a
   clearly designated disposable capture. Preserve its bytes privately before
   intentionally removing only that test copy. Do not delete retained outcome,
   replacement or historical evidence. Confirm missing stays unhealthy even with
   the valid wait pin. Preserve both results and restore the reference from the
   exact verified copy afterward; never rewrite journals or replay inputs.

Record unavailable/interrupted cases, uncertain events and cleanup separately.
The expected-eviction count cannot make uncertainty or recorder truncation healthy.
Keep raw media, fixture state/control, profiles and receipts outside Git.
