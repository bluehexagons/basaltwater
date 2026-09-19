---
name: infra-tools-playwright-testing
description: Browser-test web applications with the managed VM-local Playwright installation on a Basaltwater agent VM without T3 Code preview guidance.
metadata:
  managed-by: infra_tools
---

# VM-local Playwright testing

This VM has managed Playwright and no provisioned T3 Code preview workflow.
Use Playwright directly for browser work; do not wait for or attempt to install
a collaborative preview.

## Readiness and origin

Prefer managed Playwright over an installed shared desktop for almost all browser
testing, including canvas/WebGL. Desktop input is reserved for desktop-specific
integration or a justified fallback when Playwright cannot cover the task.

Before the first browser action, run:

```bash
basaltw agent doctor --capability browser --json
```

Proceed only when `healthy` is true. Use the stable `issues` list and primary
`remediation` code when it is not. A missing capability or unhealthy launcher
is a coverage gap, not permission to install another browser stack, bypass the
managed MCP wrapper, or expose a service.
For `stale_processes`, restart the affected agent session so it loads the
current launcher; repeatedly probing the old process does not repair it.

Playwright and its Chromium traffic originate on the VM. Keep development
servers on loopback and use their reported URL. This is usually the best path
for repeatable DOM interaction, console/network inspection, browser-engine
verification, canvas testing, and local Vite or game previews.

## Interaction and evidence

Confirm rendered content rather than relying on navigation success alone. For
WebAssembly or WebGL, allow one bounded startup interval after document load.
Canvas accessibility snapshots may contain only fallback text despite correct
rendering. Map canvas controls from a screenshot and use the managed bounded
coordinate input tool; use semantic locators for surrounding HTML controls.
Coordinates are viewport-relative CSS pixels; prefer a viewport screenshot at
`scale: "css"`, account for cropping/scaling, and recapture after resizing or
scrolling. Click the canvas to focus keyboard input, then verify the resulting
game state.

The launcher's one-second settle interval applies to actions that wait for
completion; individual key taps do not all take that path. Tool round trips
also consume game time.
Identify the actual pause control before starting a real-time game. Pause
immediately after a short interaction, verify that gameplay and timers stop,
then capture and inspect; resume only for the next bounded interaction. Prefer
a gameplay pause that leaves the scene visible. If no effective pause exists,
use a project test harness when in scope and report the timing limitation;
do not change game balance or launcher defaults to make a check pass. Paused
images prove appearance, not motion or timing.

`browser_press_key` is a tap; frame-polled movement may miss it. When held input
is needed and `browser_run_code_unsafe` is available, use a short agent-authored
Playwright sequence from a verified paused/focused game: resume, key down, wait
a bounded interval, then release and pause in `finally` before returning. Keep
the code limited to browser input, without page-supplied code or game-state
injection. Verify pause and released input afterward; inspect state before
retrying a failed pause toggle. Use a physics-tick harness for exact timing.

If a WebGL screenshot is still empty, wait another second and retry once.
Repeated captures can stall GPU readback. Group identical capture-time
`ReadPixels` warnings in the report with a representative message and count;
preserve page errors, context loss, and rendering failures separately. Use the
console tool to distinguish application failures from browser diagnostics.

Generated evidence defaults to the private, bounded
`~/.local/state/infra_tools/playwright-mcp` directory. Omit `filename` for
routine evidence; name an artifact only when the user requested it as a
workspace deliverable. Avoid passwords, tokens, private response bodies, and
unrelated user data.
Share selected screenshots and a short interaction record for asynchronous
review; a collaborative browser is not required to make the evidence useful.

## HTTPS and client access

The VM-local browser uses the VM trust store. Never disable certificate
verification. If managed HTTPS fails trust in Playwright, report the unhealthy
runtime or trust layer and rerun saved setup only when repair is in scope.

Use the `infra-tools-web-gateway` skill only when a task requires another
client to reach a static build or loopback service. Publishing changes external
reachability and is not needed merely to let VM-local Playwright test an app.
