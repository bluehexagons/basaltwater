# Readiness and origin

Use `basaltw agent manifest REPOSITORY --capability browser --json` for concise
engine/context discovery. The managed capability doctor qualifies Chromium
and MCP; it does not verify project Playwright Firefox or desktop Firefox.
For explicitly requested Firefox coverage, run `basaltw agent browser doctor
--engine firefox --context project --project REPOSITORY --json`. An authorized
`browser install` with the same arguments downloads the engine for the existing
project package without changing package versions or lockfiles. Managed context
uses `--context managed` without `--project` and leaves MCP on Chromium.
These diagnostics do not expose browser tools to a session or replace its
browser policy. See the [engine/context guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#engine-and-context-coverage).

Explicit opt-in `basaltw agent browser register --engine firefox --agent-tool
codex --json` (or `opencode`) adds `basaltwater-playwright-firefox` after a
successful managed Firefox doctor and launcher reconciliation through saved
setup. It preserves Chromium's default registration. Require a healthy Firefox
engine doctor and its own exposed MCP tools before selecting it. New tool
registration needs a new session; native ESR and Flatpak Firefox remain separate
desktop contexts. Launch, registration and application-test results are distinct.

Choose the ready configured option that fits the task under its session policy.
Managed Playwright is useful for repeatable DOM/console/network checks, file
fixtures, loopback access, and canvas/WebGL. A desktop browser is useful for
native dialogs, desktop integration, visual interaction, or when automation
tools are unavailable. Neither VM-local option requires an open T3 application.

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

A healthy doctor result verifies the installation, not exposure of its MCP tools
to this session. This also applies to unattended panel prompts. If those tools
are absent or unusable, inspect configured project Playwright tests and the
installed desktop workflow before declaring browser coverage blocked. Project
tests use their existing dependencies and documented command; do not bypass the
managed launcher or load its private packages directly.

Continue automatically with a ready alternative within the authorized browser
test, instead of asking the user to reopen T3 or restart services. For a desktop
browser, discover applications with `basaltw agent manifest REPOSITORY --json`,
read the installed desktop skill, and check its status/doctor before managed
launch, scoped capture, and input. Honor human pause and preserve unrelated
applications. If no usable browser remains, continue non-browser checks and
report the exact missing coverage; do not install an unconfigured browser stack.

Keep using the selected option for the task. Inspect application state before
repeating input whose acknowledgment failed, and use normal authorized login
flows rather than copying cookies between profiles. Report browser and network
origin alongside the evidence. A desktop visual check does not establish
Playwright DOM/network coverage or the connected client's routing and trust.

Playwright and its Chromium traffic originate on the VM. Keep development
servers on loopback and use their reported URL. This is usually the best path
for repeatable DOM interaction, console/network inspection, browser-engine
verification, canvas testing, and local Vite or game previews.
## HTTPS and client access

The VM-local browser uses the VM trust store. Never disable certificate
verification. If managed HTTPS fails trust in Playwright, report the unhealthy
runtime or trust layer and rerun saved setup only when repair is in scope.

Use the `basaltwater-web-gateway` skill only when a task requires another
client to reach a static build or loopback service. Publishing changes external
reachability and is not needed merely to let VM-local Playwright test an app.
