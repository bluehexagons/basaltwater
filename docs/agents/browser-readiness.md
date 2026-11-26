# Browser readiness and operating limits

[Documentation index](browser-automation.md)

## Offline-cache freshness

When an updated game appears unchanged, distinguish the copied publication
from the browser's offline cache. First compare a served artifact with the
build as described in [Internal HTTPS sites](../services/internal-web.md). Then pass this
read-only expression to `preview_evaluate` in the same tab:

```javascript
(async () => {
  if (!('serviceWorker' in navigator)) return {supported: false};
  const worker = value => value
    ? {scriptURL: value.scriptURL, state: value.state} : null;
  const registration = await navigator.serviceWorker.getRegistration();
  return {
    supported: true,
    pageURL: location.href,
    controller: worker(navigator.serviceWorker.controller),
    registration: registration ? {
      scope: registration.scope,
      active: worker(registration.active),
      installing: worker(registration.installing),
      waiting: worker(registration.waiting),
      updateWaiting: Boolean(registration.waiting)
    } : null
  };
})()
```

`controller` identifies the worker controlling this page; `getRegistration()`
selects the registration matching its URL rather than unrelated applications
on the shared gateway origin. A waiting worker is an installed update that
has not activated. These fields expose cache context, not the contents or age
of cached responses. See the [controller API](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/controller)
and [waiting worker API](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/waiting).

Use the app's normal update flow or reload once, then capture the worker state
and expected changed content again. Avoid awaiting `serviceWorker.ready`, which
can remain pending when no worker activates. Do not unregister workers, delete
caches, or force `skipWaiting` merely to diagnose freshness. Report a pending
update or unresolved freshness check instead of treating it as a failed build.

## Verification

For the panel, pairing, approval, and disposable Guacamole application checks,
use the developer-machine `make browser-test` workflow in
[browser qualification](browser-testing.md). It records each engine separately
and keeps intensive tests out of ordinary CI. Native Debian Firefox ESR has a
separate release checklist there; neither runtime doctor establishes that
coverage. Projects retain their own [browser policy](project-manifests.md#project-browser-tests).

### Engine and context coverage

Use small, read-only discovery before selecting engine coverage:

```bash
basaltw agent manifest /home/agent/repos/example --capability browser --json
```

The `browser.contexts` entries distinguish stable managed headless Chromium and
optional Firefox, the separate managed MCP context, project-installed Playwright Firefox, interactive desktop
Firefox/Firefox ESR, and the connected client's collaborative preview.
Discovery reports runtime presence and leaves launch/rendering readiness
unverified; browser-cache directories do not prove that an engine works.
For Playwright contexts, `discovered` reports the installed package. Run the
context's `verification_argv` to check for the matching engine executable and
test it. The `browser.managed_playwright` summary describes the `managed-mcp`
launcher. Inventory also reports `launchable` (unknown until a launch check),
`mcp_registered` (client configuration), and `application_tested`. Desktop
discovery probes native Firefox/Firefox ESR and `flatpak info org.mozilla.firefox`,
including Flatpak-only installs, and reports versions when available. A connected
preview has engine `unknown` until the session establishes it. Never infer Firefox
coverage from a viewport preset or an unknown preview.
The ordinary browser capability doctor below qualifies managed Chromium and
its MCP integration only. Its result includes the engine, context, and
network origin explicitly, also retained in redacted readiness and support
records. Older records without those fields leave the context unknown.

When Firefox coverage is requested, check the selected existing runtime:

```bash
basaltw agent browser doctor --engine firefox --context project \
  --project /home/agent/repos/example --json
```

The engine doctor includes a `desktop` inventory for the selected engine. For a
native installation, `test_argv` opens a supervised browser with a disposable
profile and private downloads. Append `--url URL` to test a page. It requires an
active, configured desktop. Use the [desktop browser workflow](../workstations/debian-development.md#test-with-a-disposable-browser)
for window checks and cleanup. Use
`basaltw agent manifest --application firefox-esr --json` or
`--application firefox` to discover one named application.

If the selected runtime reports `browser_payload_missing`, run its exact
`provisioning_argv` to install and test the matching payload:

```bash
basaltw agent browser install --engine firefox --context project \
  --project /home/agent/repos/example --json
```

These commands use the existing package under that directory's `node_modules`
(`playwright`, `@playwright/test`, or `playwright-core`), including pnpm package
links, and the project's supported Node executable selected by `basaltw node`.
Use the owning package directory
for a monorepo. They do not run npm/npx, add packages, change a lockfile, upgrade
Playwright, or install OS dependencies. Project `PLAYWRIGHT_BROWSERS_PATH` and
custom CA settings are retained; Node preload/module overrides are omitted.
The managed MCP and smoke launchers omit those overrides too, while retaining
custom CA settings, so their Node environment agrees with the engine doctor.
Optional downloads disable browser garbage collection so other projects'
payloads remain available. Missing project dependencies require the project's
normal lockfile installation first.

On a Debian VM with managed Playwright, `--context managed` uses the pinned
stable package and its fixed user cache instead. `--context managed-mcp` checks
MCP's separate alpha package and matching payload. Optional Firefox provisioning
leaves the default MCP registration on Chromium. Project context also works on
CachyOS and Ubuntu WSL without assuming a Debian managed browser stack.
An unexpected managed package version reports `runtime_version_mismatch`
before installing or launching a payload; rerun setup to restore the pin.
The platform must already provide the selected engine's OS dependencies;
a failed download or smoke test returns a nonzero status with `issue` and
`remediation`. Use the host's authorized dependency setup when needed.
System Firefox is a separate desktop context: [Playwright uses patched Firefox](https://playwright.dev/docs/browsers#firefox),
so an installed desktop browser does not satisfy its payload requirements.

Each explicit engine doctor launches an isolated, in-memory page, checks a
button interaction and screenshot, and closes the browser. Successful results
record Playwright and browser versions and verify local rendering; application
behavior, desktop integration, and connected-client coverage still need their
own tests. Session browser policy continues to govern application automation;
these commands do not expose additional browser tools to an agent session.

### Opt-in managed Firefox tools

After updating the managed launchers through the saved setup, run as the setup
user (without sudo):

```bash
basaltw agent browser install --engine firefox --context managed-mcp --json
basaltw agent browser register --engine firefox --agent-tool codex --json
# Or select --agent-tool opencode; repeat to register both clients.
basaltw agent browser doctor --engine firefox --context managed-mcp --json
```

Installation downloads only the pinned Firefox payload. It never installs OS
dependencies; missing libraries require the host's explicit dependency setup.
Registration first verifies startup and rendering and checks the managed
launchers, then adds the separate `basaltwater-playwright-firefox` server. It
preserves `basaltwater-playwright` as Chromium and unrelated settings. Start a
new agent session to discover the Firefox tools; registration alone does not
expose tools to a running session.

Both MCP servers use the same pinned alpha Playwright package, headless isolated profiles,
explicit matching executables, certificate verification, and bounded private
evidence. Firefox evidence lives under `playwright-mcp/firefox`; its separate
configuration omits Chromium launch flags. Engine doctors report runtime
launch/render results and registrations separately and always leave
`application_tested` false. Use application tests to establish that coverage.
Registration always qualifies `managed-mcp`, even when the default context is
omitted. A stable engine doctor does not qualify an MCP browser payload or its
registration. An unavailable configuration parser leaves registration
unknown. If registration fails after a successful runtime check, the command
returns nonzero with `browser_registration_failed` and retains the engine,
version, and rendering results for diagnosis.
The general capability doctor reports `registration_unverified` with
`use_python_with_toml_support` when its Python lacks `tomllib`; run it with
Python 3.11 or newer to inspect Codex configuration.

### Managed Chromium integration

Run the browser check on the configured VM as the setup user:

```bash
basaltw agent doctor --capability browser --json
```

This checks the browser capability without requiring unrelated terminal tools.
Add `--tool codex` or `--tool opencode` when that client must also be healthy;
each explicit tool is required, even when absent.

The capability is healthy only when both managed
launchers are executable, root-owned regular files without group or world write
access; explicit managed-Chromium selection and current private, bounded
evidence, safe-coordinate, and one-second-settle defaults are present; every
installed compatible agent has the managed MCP registration; exactly one
Basaltwater-managed Playwright-capable browser workflow skill is installed as
a bounded, user-owned file without group or world write access;
active managed MCP processes use those same safe defaults; and the local
interaction/rendering smoke test passes. A stale launcher is unhealthy even
when its smoke test passes. Inspect an unsafe path, then rerun the saved setup
to reconcile it. A stale active process instead requires
restarting the agent session that owns it.

In an agent sandbox, Linux may show an unmapped host owner as the overflow UID
(normally `65534`/`nobody`). The doctor checks `/proc/self/uid_map` and the kernel's
configured overflow UID before classifying ownership. If root is not mapped and
ownership is ambiguous, it reports `launchers_unverified`,
`launchers_secure: null`, and `verify_launchers_from_host`. Unreadable security
metadata also leaves security unverified. A visible non-root owner, a linked or
non-regular launcher, or group/world write permission remains unsafe.

The doctor skips its launcher smoke test when launcher security is unsafe or
unverified; `smoke_test` and `launchable` remain `null`, without a
`smoke_test_failed` issue. Readiness still returns nonzero until verification
succeeds. Verify numeric ownership and modes from an authorized host context;
an unmapped owner alone is not grounds to change ownership, permissions, or
reinstall. Readiness records and support bundles preserve the unknown security
value and the verification recommendation. A smoke-test failure after verified
launcher security describes the current invocation; sandbox restrictions can
still prevent browser startup and do not establish a host installation defect.

JSON results include a stable `issues` list and one primary `remediation` code.
`mcp_browser_selection_missing` identifies a launcher that can pass the direct
Chromium smoke test while MCP still defaults to an unavailable Chrome channel;
`rerun_saved_setup` refreshes stale managed defaults;
`rerun_setup_with_browser_automation` restores missing launchers or agent
registration; `inspect_launcher_security_then_rerun_saved_setup` requires
reviewing an unsafe managed path before reconciliation;
`workflow_skill_missing_or_stale` identifies absent, incompatible, or multiple
managed browser workflow variants and is repaired by the saved setup;
`restart_agent_sessions` identifies MCP processes that were started before a
launcher or trust-store update and must be restarted rather than reconfigured;
and `inspect_browser_runtime` identifies an installation whose local Chromium
smoke test failed. These fields are also retained in redacted readiness records
and support bundles.

For lower-level checks:

```bash
/usr/local/bin/basaltwater-playwright-doctor
codex mcp list
```

Inspect `~/.config/opencode/opencode.json` or `opencode.jsonc` for OpenCode's
`mcp.basaltwater-playwright` entry. The managed OpenCode entry uses a 30-second tool
discovery timeout to accommodate cold VM startup. Do not print agent auth files
while troubleshooting.

## Security and operating limits

Browser automation is not a security boundary. A page can contain untrusted
content designed to influence an agent, and a browser-enabled agent can act
with the network access, repository access, and application credentials
available to its Unix user. Prefer scoped repository and website credentials,
restrict VM egress where appropriate, and require human review for sensitive
or irreversible actions.

The managed server uses local stdio rather than an exposed network listener.
Chromium retains its browser sandbox; VM targets are recommended. Browser
namespaces and sandboxes may not work in every container policy, so use a VM
when browser automation must be reproducible. Rerunning setup reconciles the
managed registration and package version. Version changes are delivered through
a Basaltwater update and should be reviewed like other executable dependency
updates.
