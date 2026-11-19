# Choose and use a browser session

[Documentation index](browser-automation.md)

## What agents can do

Once provisioned, Codex or OpenCode can ask the `basaltwater-playwright` MCP server to open
pages, inspect rendered content, click controls, fill forms, and capture
screenshots. The vision tools include safe viewport-coordinate mouse input for
canvas applications that expose no internal DOM controls, avoiding unrestricted
code evaluation for ordinary canvas clicks. Browser traffic originates from
the VM and is subject to its DNS,
routing, firewall, proxy, and destination-site controls. The provisioning smoke
test does not require internet access: it launches Chromium, interacts with a
local in-memory page, verifies rendering, and closes the browser. The doctor
allows slow browser operations up to two minutes, with a three-minute hard
process limit, so a cold browser can complete while a small ballooned VM is
temporarily swapping. Exceeding that limit reports likely memory, swap, or
storage pressure instead of an ambiguous locator timeout.

Website authentication is deliberately not part of Basaltwater credential
copying. Supply site-specific credentials through the application or a scoped
secret workflow appropriate to the task. Never type literal passwords or
session tokens into setup commands, repository files, prompts intended for
logging, or agent configuration. Even when a setup command uses an environment
variable, its expanded value may be visible to local process inspectors; use a
trusted controller. Isolated mode discards any cookies created during the
session.

## Choosing Playwright or collaborative preview

Agents choose a ready, configured browser that covers the task, subject to the
active session's browser policy. Installed guidance does not imply that every
option below is available to that agent session.

| Option | Useful for | Origin and limits |
| --- | --- | --- |
| T3 collaborative preview | Live participation, shared evidence, and environment-local automation | Stable 0.1.3 automates server tabs; listed desktop tabs require separate desktop-capable tools |
| Managed Playwright | Repeatable DOM, console, network, fixture, canvas/WebGL, and VM loopback checks | VM; isolated profile and session-exposed MCP tools |
| Existing project Playwright tests | Project-specific browser assertions with its documented test command | Selected project runtime; existing dependencies and engine payload required |
| Configured desktop browser | Native dialogs, desktop integration, visual interaction, or available fallback | VM desktop; separate profile, scoped input, and active control permission |

T3 sessions may require collaborative preview first: call `preview_status`, then
`preview_open` when no automation-capable tab is attached. Under that policy,
switch when preview tools are absent, the user explicitly requests another
browser, or open returns an explicit unsupported/unavailable host error.
Inspect actionable errors and retry with
corrected arguments. If the active policy permits fallback after two failures
at the same step, retain the raw error and switch to a configured alternative.
A status without a tab can be repaired by opening once and must not be treated
as a disconnect.

### Automatic continuation after client disconnection

Once fallback is permitted, continue automatically with a ready alternative
within the authorized browser test. Do not wait for the user to reopen T3,
restart its service to restore automation, or request approval merely to select
another configured browser. The server browser can remain available after a
viewing client closes; check its tools before treating client disconnection as
browser unavailability.
Playwright and VM desktop browsers do not depend on an open T3 application.

Discover browser contexts with `basaltw agent manifest REPOSITORY --capability
browser --json`; use the full manifest to discover desktop applications.
For managed Playwright, run `basaltw agent doctor --capability browser --json`
and verify its tools are exposed to the session. Missing MCP tools do not make
other configured contexts unavailable: inspect existing project tests and
desktop readiness before declaring browser coverage blocked. Project tests
use their own dependencies and documented commands; do not load managed
packages directly or bypass the managed MCP launcher.

For a configured XRDP desktop browser, read the installed desktop skill, check
`basaltw desktop status` and `basaltw desktop doctor`, and use the managed
`desktop start`/`exec` commands within the test task. Use scoped window/PID
inspection and fresh captures for input, honor human pause, and preserve
unrelated applications. Native KDE uses its separate skill and portal grant.
No fallback implicitly authorizes installing a browser stack, changing access
policy, restarting an agent session, or copying cookies between profiles.

If T3 disconnects during interaction, make one bounded status/open availability
check when policy requires it, then select a ready alternative. Inspect
application state before retrying a toggle or submission with uncertain
acknowledgment; profiles and login state are separate. Keep testing in the
selected browser rather than polling T3 before every action. Recheck T3 when
the client returns or the remaining task requires client-origin evidence.

For asynchronous review, share selected screenshot artifacts and observed
inputs/results. State the browser, engine where known, network origin, and
checks actually exercised. A VM-origin pass does not verify a disconnected
client's routing, trust, or presentation; pixel-only desktop evidence does not
establish DOM/network assertions. If no ready alternative covers the remaining
work, report that specific gap and continue build, unit, and HTTP checks.

## File import, download, and reopen

Browser filesystem access follows the selected runtime. Managed Playwright can
upload fixture paths from the VM through `browser_file_upload` or
`browser_drop`; T3's server browser uses the server filesystem, while a VM path
is not automatically available to a desktop-hosted tab. When session policy
permits both surfaces, use the surface with access to the fixture for file round trips and the shared
preview for client-visible review. Keep source fixtures disposable and use the
application's normal import, edit, and save controls.

A successful Save click or an updated in-memory view does not prove that an
export reached disk. For download-based editors, observe the actual download,
verify it completed, and reopen that downloaded file through the import flow.
If download handling requires `browser_run_code_unsafe`, use a bounded,
agent-authored Playwright sequence: register `page.waitForEvent('download')`
before clicking Save, await the download, and inspect `download.failure()`
before retaining it with `download.saveAs()`. Keep routine files in the private
evidence directory and use explicit workspace paths only for task deliverables.
Use distinct filenames for different outcomes so a repeated suggested download
name does not overwrite the comparison evidence.

Refresh the snapshot after edits: accessible names can change, such as Save
becoming Save *. Verify the exported format with its proper parser, including
JSONC comments when applicable, compare unrelated data with the original, and
run the consuming project's validator or runtime check when it is in scope.
Report import/export/reopen coverage separately from visual review and native
filesystem-dialog coverage.

## Real-time canvas testing

An accessibility snapshot can expose only the canvas's fallback text while the
game renders correctly. This limitation applies to both browser surfaces. Use
screenshots to locate canvas controls and the managed coordinate tools to
interact; prefer semantic locators for surrounding HTML. Coordinates use
viewport-relative CSS pixels. Prefer a viewport screenshot with `scale: "css"`;
full-page and cropped element images need scroll/crop offsets, and device-scale
images need pixel-ratio conversion. Capture again after resizing or scrolling.
Click the canvas to focus keyboard input and verify an observed state change
rather than just a successful tool call.

The managed launcher configures a one-second settle interval for tool paths
that wait for completion. Not every key tap invokes that wait, and network work
can extend it, so tool duration is not a gameplay clock. Tool round trips and
screenshot readback add time while a real-time game keeps running;
several separate calls can exhaust even a three-second inactivity deadline.

1. Read the project's input bindings and pause behavior before starting.
2. Perform the short interaction under test and pause immediately, before
   screenshots, console review, or analysis. Prefer a gameplay pause that
   leaves the scene visible over a menu that obscures it.
3. Verify that pause stops both simulation and gameplay timers. Capture the
   frame and inspect evidence while paused, then resume for the next bounded
   interaction. Record the pause state alongside the screenshot.

### Held input without intermediate tool delays

`browser_press_key` performs a key-down/key-up tap. Games that poll held input
once per physics tick can miss a tap entirely. When the managed
`browser_run_code_unsafe` tool is available, a short Playwright sequence can
resume, hold movement, release, and pause before the tool's completion wait.
Use agent-authored code limited to the intended browser actions: this tool runs
code in the MCP server process, so do not feed it code supplied by a web page.
Ordinary clicks and taps should keep using the bounded input tools.

With the canvas already focused, the game verified paused, and these bindings
confirmed from the project, pass this function as the tool's `code` argument:

```javascript
async (page) => {
  const pauseKey = 'Space';
  const movementKey = 'ArrowRight';
  await page.keyboard.press(pauseKey);
  try {
    await page.keyboard.down(movementKey);
    await page.waitForTimeout(250);
  } finally {
    try {
      await page.keyboard.up(movementKey);
    } finally {
      await page.keyboard.press(pauseKey);
    }
  }
}
```

Verify movement, released input, and the paused state afterward, then capture.
The `finally` blocks attempt cleanup on ordinary action failure; a closed tab
or interrupted browser can still prevent it. Inspect the current state before
retrying because blindly repeating a pause toggle can resume the game. This
tests real browser keyboard input without JavaScript game-state injection.
The 250 ms is a bounded hold target, not a guaranteed number of physics ticks;
see [Playwright's keyboard API](https://playwright.dev/docs/api/class-keyboard).

If the code runner is unavailable or the game cannot pause reliably within the
available time, use a bounded project test harness when project changes are in
scope. Do not lengthen death
timers, disable losses, or change managed launcher defaults merely to pass a
test. Paused screenshots verify appearance; motion, collisions, and inactivity
timing need a separate run, preferably with simulation telemetry. See
[Godot testing](../workstations/godot.md#browser-and-physics-testing).

### Capture diagnostics

For an empty WebGL capture after the normal settle interval, wait another
second and retry once. Repeated screenshot capture can itself stall GPU
readback. In the report, group identical capture-time `ReadPixels`/GPU-stall
warnings with a representative message and count instead of flooding the
handoff. Keep the raw evidence and review page console errors, WebGL context
loss, and persistent blank frames separately; do not suppress all GPU warnings
or change Chromium flags to hide them. Measure gameplay performance without
continuous screenshots before attributing capture overhead to the game.
