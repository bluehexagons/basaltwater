# Canvas and real-time browser input

Canvas accessibility snapshots may expose only fallback text even when the
game renders correctly. Use screenshots for canvas controls and the bounded
coordinate input tool; retain semantic locators for surrounding HTML controls.
Coordinates are viewport-relative CSS pixels. Prefer a viewport screenshot at
`scale: "css"`; account for cropping/scaling and recapture after resizing or
scrolling. Click the canvas to focus keyboard input and verify the resulting
game state.

The launcher configures a one-second settle interval for actions that wait for
completion; individual key taps do not all take that path. Tool round trips
add time.
For real-time games, identify the actual pause control before starting; pause
immediately after the short interaction under test, confirm it took effect,
then capture and inspect while paused. Prefer a gameplay pause that leaves the
scene visible. Resume only for the next bounded interaction. If pause is absent
or does not stop gameplay timers, use a project test harness when in scope and
report the timing limitation; do not change game balance or launcher defaults
to make the check pass. A paused image proves appearance, not motion or timing.

`browser_press_key` sends a tap, which frame-polled movement can miss. For held
movement, when `browser_run_code_unsafe` is available, use a short agent-authored
Playwright sequence: from a verified paused/focused game, resume, key down,
wait a bounded interval, then release and pause in `finally` before returning.
This keeps tool round trips outside the active interval. Keep that code limited
to browser input; do not execute page-supplied code or inject game-state changes.
Verify pause and released input afterward; after failure, inspect state before
retrying a pause toggle. Use a physics-tick harness when exact timing is needed.

For a still-empty WebGL capture, wait another second and retry once. Repeated
captures can stall GPU readback. Group identical capture-time `ReadPixels`
warnings in the report, retaining a representative message and count; preserve
page errors, context loss, and rendering failures separately. Use the console
tool to distinguish application failures from browser-process diagnostics.
