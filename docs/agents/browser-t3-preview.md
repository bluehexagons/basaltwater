# T3 preview and private routing

[Documentation index](browser-automation.md)

## T3 browser host preparation

T3 0.1.3 uses its own headless Chrome for server tabs and `html_preview`. The
first use downloads it into T3's data directory; managed Playwright uses a
separate payload/cache. Follow the tool's installation/retry message before
declaring the host unavailable. A healthy T3 HTTP endpoint or Playwright doctor
does not establish that this Chrome can launch or render text.

For missing libraries, fonts, or AppArmor sandbox diagnostics, run
`t3 browser setup` as the service account without sudo to inspect what is needed.
Use the server's `T3CODE_HOME` or `--base-dir` for a nondefault data directory,
including CachyOS web's isolated home.
Before Chrome is downloaded it cannot inspect that executable's libraries, so
repeat inspection if the first launch reports missing dependencies. The elevated
command can install Debian/Ubuntu packages/fonts and an AppArmor profile; use
the exact command printed by the active runtime through the platform's authorized
privileged workflow. CachyOS uses its native package workflow for missing
libraries/fonts. Keep the server under its existing nonroot account and Chrome's
sandbox enabled. Verify the failed operation after repair; a readable screenshot
is needed to catch missing fonts. See the [upstream setup procedure](https://github.com/pingdotgg/t3code/blob/v0.1.3/docs/user/remote-access.md#browser-host-setup).

T3's inline HTML pages are thread artifacts, distinct from product browser tests
or gateway publication. The project-development skill covers
[project actions and visual replies](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md).

## Collaborative preview and private networks

T3 Code 0.1.3's server tools automate server-hosted tabs. Requests use the environment
host's network and certificate trust; clients view the streamed tab. A
desktop-hosted tab instead uses that desktop's routes and trust. Managed
Playwright uses the agent VM. Identify the runtime from the active tool/host
metadata or a controlled loopback check before attributing a failure to the
client. A disconnected viewer does not establish that the server browser failed.

Before reusing a tab ID from status or `t3_preview_list`, check its `runtime`.
Only `runtime: "server"` entries can be passed to this environment's
`preview_*` tools. An entry with desktop or missing runtime belongs to the user's
local app: report that the server cannot access it, then call `preview_open`
without that ID to open a server tab. Do not retry the desktop ID or diagnose
networking, TLS, or stale presentation from that rejection. Desktop automation
instructions below apply only when the active tools explicitly support it.
See the [upstream tab-list contract](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/server/src/mcp/toolkits/previewControls/tools.ts).

For a development server in the current environment, prefer the native target
with the retained tab ID:

```json
{"tabId":"TAB_ID","target":{"kind":"environment-port","port":5173,"path":"/"}}
```

Pass exactly one of `url` or `target`. The target defaults to HTTP and accepts
`protocol: "https"` for TLS. The server browser resolves `environment-port`
directly to `localhost:PORT`, so VM loopback needs no gateway forward. See the
[server resolver and preferred-host registration](https://github.com/pingdotgg/t3code/blob/v0.1.3/apps/server/src/preview/ServerBrowser.ts).

In 0.1.11, desktop tabs opened by the user against another environment can
render locally and tunnel `localhost` to that environment. Server tabs and
web clients still stream from the host. Desktop port forwarding and mobile
**Open on this device** use the same preview tunnel. A forwarded port is
reachable by apps on the client and stops when the forward closes or the
desktop app quits. Private HTTPS or system-proxy-dependent connections may
require a streamed tab. See
[remote browser and port forwarding](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/remote-access.md#browser-on-a-remote-environment).

The preview tunnel defaults on in 0.1.11. The host's **Preview tunnel** setting
can disable it, and managed policy `limits.previewTunnel: false` takes
precedence. A client needs `terminal:operate` to use the tunnel. Streamed
tabs can still reach the host's loopback. Review both paths when assessing
access to an environment's development services.
[Managed policy](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/managed-policy.md#other-capabilities)
· [Tunnel authorization](https://github.com/pingdotgg/t3code/blob/v0.1.11/apps/server/src/preview/PreviewTunnel.ts)

Use the core Basaltwater CLI to check readiness on a Linux T3 host.

```bash
basaltw agent browser resolve-preview --port 8080 --path '/app/?view=details#summary' --json
```

Success returns `navigation.target` for the native environment port. The probe
tries IPv4 loopback first and uses IPv6 loopback when the TCP connection fails.
Use `--host` to check one address explicitly. Omit `--path` for `/`. For a local
HTTPS server, add `--protocol https` to check its certificate for `localhost`
with the probe host's trust store. Gateway provisioning is optional for this check.
See the [CLI reference](../reference/cli-agent-diagnostics.md#native-t3-preview-readiness).

On native Windows, use the project's readiness check on the selected host.
When a T3 project action starts the server, set its **Preview URL** to the
exact development address. T3 uses that address for readiness and navigation.
See [T3 local servers](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/remote-access.md#local-servers).

If local desktop or mobile rendering still fails, inspect the host's
**Preview tunnel** setting and use `t3 policy show` with the service's data
directory to inspect installed and loaded policy. Check the connected client's
grant, transport and trust, and try a streamed tab where available.

When the task calls for a separately reachable HTTPS URL for a VM loopback
service, resolve the gateway route:

```bash
basaltwater-web preview resolve --route gateway --port 8080 --json
```

Open the returned `navigation.url` in the desktop browser, using
`preview_navigate` only when its tools support that runtime. If `issue` is
`forward_missing`, run the exact returned `command` when
client-visible preview is in scope, then resolve again. The read-only resolver
checks owned HTTPS forwards without changing bindings or access policy.
Failures identify the upstream or gateway, distinguish refused connections,
timeouts, HTTP errors and certificate failures, and include recovery advice.
Use `--host ::1` for IPv6 or `--profile godot` for cross-origin isolation.
See [preview resolution](../services/internal-web.md#forward-an-existing-loopback-service).
VM-side success does not verify the client's network or certificate trust.

An internal gateway URL can pass VM checks yet fail on a desktop with no LAN
route, an excluded source address, or missing CA trust. Attribute that boundary
only after establishing the browser host. Keep development servers on loopback
and preserve the saved access policy.

When the task selects collaborative preview, agents should call preview status
first and open a preview once when no automation-capable tab is attached. A tab
with `visible: false` has no active viewer; server-browser navigation, input,
and capture can still work. With tools that support desktop automation,
desktop-hosted capture can depend on the client window. Attempt one snapshot
after navigation. If capture fails or times out while the tab remains available
and invisible, do not repeatedly
retry or diagnose the application, WebGL, TLS, or network from that result.
Use the healthy VM-local fallback when session policy permits it and its network origin is
appropriate, or finish with non-browser checks and report the coverage gap.
When collaborative evidence is important, restoring T3 should make the
existing tab capturable; recheck status and retry that tab rather than opening
duplicates.

Preview availability and tab attachment are separate states. A status response
with `available: true` and `tabId: null` confirms an automation host but means
the current agent session has no selected tab. Open once, retain the returned
tab ID, and use it explicitly after an agent-session boundary. Do not describe
that state as a detached or unavailable preview.

Once a tab is attached, use a snapshot-first workflow. Server hosts list every
tab in the thread, including tabs opened by the user. After verifying a supported
runtime, read the requested tab by `tabId`; act on your own or an unclaimed tab
only while no human controls it.
Snapshot output provides visible text, accessibility data, semantic locators, console and
network diagnostics, a screenshot, and recent action history. Prefer role/name
locators from that output. Treat a successful type, press, click, or scroll
response as proof that T3 accepted the helper, not that the page received an
event or changed: verify the expected text, URL, or rendered state with a
bounded wait or follow-up snapshot. Programmatic typing can establish DOM focus
before the connected preview has pointer-activated page input. If a subsequent
keyboard helper produces no expected event or application change, semantically
click its intended target and retry the key once. If it still has no effect, use
the equivalent semantic click when the test allows it or record the keyboard
path as unverified. Do not inject mutations through page evaluation merely to
turn an unverified interaction green.

A failed input call can also leave a changed page. Before retrying a press,
click, or submission, inspect the expected state. If the change occurred,
report successful input with uncertain acknowledgment; retain the tool error
separately. If state cannot be inspected, report an unverified outcome.
Distinguish automation transport failure from a deadline timeout using the
actual error details; do not infer either from a generic failure alone.

Tag diagnostic evidence with tab ID, requested URL, navigation time or action
ID, and component: preview host (including Electron/preload), gateway/network,
or application. Snapshots can retain errors from a previous `about:blank`
navigation. Keep those historical host errors separate from current page
errors, and preserve uncertain attribution when no URL or timestamp proves
which navigation produced a message. Basaltwater provides this reporting
workflow; native tool error schemas and snapshot tagging are owned by T3.

Light, dark, and system appearance emulation changes `prefers-color-scheme`.
Restore the system setting after a bounded comparison unless the user asked
to leave an override.

The current `preview_recording_stop` tool transfers a compressed collaborative
recording once, up to 50 MiB, into an evidence file in the agent environment.
After successful transfer, check that the returned local path is readable and
link it for review. Keep the recording short enough for the transfer limit;
do not claim local evidence when transfer fails. Older tools can return
client-only artifacts, so follow the attached tool's description and metadata
rather than assuming every returned path is local. Start recording only when
video adds useful evidence, stop it promptly, and avoid credentials or unrelated
user data. A snapshot's action timeline can show the snapshot currently being
constructed as `running`.
When the response itself has returned, that self-entry is expected; use prior
completed actions and the returned page state to assess success.

With tools that support desktop automation, a narrower failure is possible
when `preview_open` returns a usable background tab but the desktop application
never mounts a sidebar or floating preview: status remains `visible: false`,
lightweight DOM operations can succeed, and
snapshot capture fails or times out. This is stale T3 preview-presentation
state, not proof of an application, route, certificate, or VM browser failure.
Do not create more tabs. Restarting the desktop client may preserve the stale
tab registry in the VM-side service. Use managed Playwright when session policy
permits fallback. If collaborative coverage is required, warn that every active T3
session will be interrupted and, only with the user's approval, restart the
server as the target user:

```bash
systemctl --user restart t3code.service
```

Reconnect the desktop client and retry status/open once. Do not use a full VM
reboot as the first recovery step. Explicit setup refreshes restart a healthy
managed service unless the updater already started a fresh invocation; finish
active work before a setup refresh. Internal non-refresh checks reuse a healthy
service.

For WebAssembly and WebGL applications, successful browser navigation can
precede runtime initialization. A snapshot containing only a splash screen or
progress bar confirms document rendering but not application readiness. Wait
one bounded startup interval and capture the expected application frame once;
do not turn ordinary runtime startup into an unbounded polling loop.

## Canvas input and viewport emulation

Preview coordinate clicks use viewport-relative CSS pixels. For the inline
image, use `screenshot.width` and `screenshot.height` from `preview_snapshot`
when those fields are exposed. If you requested `fullScreenshot` and use the
larger image, use that image's dimensions. For a saved capture, read the PNG's
dimensions.
Use the measured `viewport.width` and `viewport.height` from `preview_status`
on the same tab at capture time, or from `preview_resize` if the viewport has
stayed the same since resizing. If the tool omits them, read
`[innerWidth, innerHeight]` with `preview_evaluate` on an agent-controlled tab.
Convert each axis separately.

```text
cssX = imageX × cssWidth  / imageWidth
cssY = imageY × cssHeight / imageHeight
```

For a 1280×768 image of an 800×480 viewport, image point (640, 384) maps to
CSS point (400, 240). For a 1280×853 image of a 900×600 viewport, image point
(640, 640) maps to about (450, 450.18). For a 750×1334 image of a 375×667
viewport, both coordinates halve. If measuring in a viewer, remove any margins
and scale from the displayed image rectangle. Capture again after resize, zoom,
or scroll.
[T3 snapshot contract](https://github.com/pingdotgg/t3code/blob/v0.1.12/packages/contracts/src/previewAutomation.ts#L502-L534)

When the session permits a managed Playwright fallback, a viewport
`browser_take_screenshot` with `scale: "css"` gives a CSS-sized image.

After a `preview_wait_for` text timeout, check whether the expected game state
appears under the canvas's name in the accessibility tree. The `text` condition
checks body text. Use a role/name locator if the snapshot reports a usable role.
For example, a canvas exposed as `img "Board. Round ready"` can use this wait.

```json
{"tabId":"TAB_ID","locator":"role=img[name*=\"Round ready\"]","timeoutMs":2000}
```

If the canvas exposes the state via `aria-label`, this selector also works.
Omit `text` from either call so the wait only checks the locator or selector.
The wait succeeds when a matching element exists in the page.

```json
{"tabId":"TAB_ID","selector":"canvas[aria-label*=\"Round ready\"]","timeoutMs":2000}
```

Inspect the result visually if the test also depends on what the game drew.
[T3 wait implementation](https://github.com/pingdotgg/t3code/blob/v0.1.12/apps/server/src/preview/ServerBrowserPage.ts#L1154-L1193)

If a coordinate click is acknowledged but the expected target has no effect,
check pointer lock before repeating it. On an agent-controlled tab,
`preview_evaluate` can read
`({ locked: document.pointerLockElement !== null, target: document.pointerLockElement?.localName ?? null })`.
For an embedded game, check the document that owns the canvas where the
runtime permits it. While locked, pointer events can keep targeting the
locked element. Use the game's unlock control or the browser's unlock gesture,
then verify the lock status and recapture before another absolute click.
Relative camera movement needs a browser path that delivers and verifies
relative deltas while locked. Use an attended browser or the project's
input test harness and check the observed `movementX` and `movementY`.

T3's `press` is a tap. Its locator drag takes `source` and `target`. T3 v0.1.12
sends intermediate mouse moves during server-browser locator drags. The
`Missing key at ["source"]` error means the drag expects a locator.
[T3 drag implementation](https://github.com/pingdotgg/t3code/blob/v0.1.12/apps/server/src/preview/ServerBrowserPage.ts#L1035-L1055)

For a right-button coordinate drag on an unlocked canvas, an authorized
Playwright fallback can run the whole sequence in one call. Focus the game
first and substitute points and a key for its bindings.

```js
async (page) => {
  await page.mouse.move(160, 140);
  try {
    await page.keyboard.down("w");
    await page.mouse.down({ button: "right" });
    await page.mouse.move(250, 200, { steps: 8 });
    await page.waitForFunction(
      () => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))
      ),
      undefined,
      { timeout: 750 },
    );
  } finally {
    try {
      await page.mouse.up({ button: "right" });
    } finally {
      await page.keyboard.up("w");
    }
  }
}
```

The wait gives the game a chance to draw a frame. Verify the resulting game
state afterward. For precise timing, use the project's test harness.
[Playwright frame polling](https://playwright.dev/docs/api/class-page#page-wait-for-function)

A device preset in `preview_resize` is viewport emulation for layout checks.
Before a touch pass, inspect `navigator.maxTouchPoints` and
`matchMedia("(pointer: coarse)").matches`. Use a touch-enabled device or a
project browser test with a touch-enabled context for touch input. Playwright
supports taps with `hasTouch: true`. Panning needs a gesture with touch start,
move, and end events. Verify that the game received touch input and panned.
[Playwright touch input](https://playwright.dev/docs/api/class-touchscreen) · [Chromium touch gestures](https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchTouchEvent)

Native support for these gaps belongs in T3 Preview. Qualification should
check a per-image CSS transform or a CSS-sized capture at both capped and 2×
scales. It should also verify visible lock state, relative deltas while locked,
and a bounded right-button coordinate drag with overlapping key hold and a
frame wait. Check key and button release on cancellation. Exercise an
accessible-name wait and its timeout message, and qualify a touch-enabled
session independently of the viewport presets.

## Diagnose private URLs

For private-URL failures, verify the exact URL and a non-sensitive artifact
from the VM with normal TLS verification, then inspect the attached preview's
snapshot and network error. Use the observed failure to choose the next step:

| Observation | Next step |
| --- | --- |
| Listed tab has desktop or unspecified runtime | Report that server tools cannot access it and open a server tab without its ID; desktop automation needs explicit tool support. |
| No selected automation tab | Call open once and retain its tab ID; status alone does not prove host unavailability. |
| Open explicitly reports a disconnected/unavailable host, or preview tools are absent | Automatically select a ready configured alternative within session policy; report the missing coverage for that browser host. |
| Unavailable capture with an attached tab | Follow bounded capture diagnostics; use an alternative when session policy permits it. |
| Explicit `ERR_CERT_AUTHORITY_INVALID` | Inspect trust on the browser host. For a desktop-hosted tab, offer `basaltwater-web ca` and [Client CA trust](../services/client-ca-trust.md) when client access is wanted. A server-browser error needs host-side trust diagnosis. |
| Timeout, refused connection, or unreachable address | Check routing from the browser host and any gateway source policy; CA enrollment will not repair connectivity. |
| Desktop-hosted background DOM works but no visible surface or snapshot | Follow the bounded stale-preview recovery above. |

Before using provisioned Playwright as the fallback, run
`basaltw agent doctor --capability browser --json`. If the capability is
absent or unhealthy, inspect configured project tests or desktop readiness
before declaring browser coverage blocked. If no usable alternative remains,
continue non-browser checks and report the missing coverage. Do not install
another browser stack merely to fill that gap.

Report which network origin passed and which failed. A client-only failure
does not establish that the hosted site is down. Preserve TLS verification,
loopback bindings, and the intended access policy; client CA enrollment is
optional and does not block unrelated work.
