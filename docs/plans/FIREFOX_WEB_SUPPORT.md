# Chromium and Firefox support: implementation brief

Status: implementation delivered and locally qualified 2026-10-15; native ESR
and disposable managed-deployment release qualification remain open. Scope:
Basaltwater-owned web surfaces, agent browser tooling, and browser-testing
guidance for projects made with Basaltwater.

## Goal

Make Chromium and Firefox compatibility testable and visible without changing
the Chromium default, silently changing certificate trust, or claiming support
for upstream application UIs. Use the pinned Playwright Chromium build as the
primary automation baseline and Firefox as a separately reported cross-browser
target. Keep these environments distinct:

- **Playwright Chromium and Firefox:** pinned browser builds for repeatable
  application tests and optional agent automation. Record their Playwright and
  browser versions; do not equate them with desktop browsers.
- **Firefox ESR:** native Debian desktop browser; qualifies the operator
  experience and local trust setup separately.
- **Project browser:** projects use their own locked dependencies and declare
  their support target in their repository.

## Starting state (before implementation)

- Workstation profiles install Firefox ESR; desktop discovery may miss Flatpak.
- Managed Playwright MCP uses a pinned Playwright package and matching
  Chromium build in an isolated headless context. Its doctor checks a local
  interaction/render smoke, not application behavior. `agent browser
  doctor/install --engine firefox` checks a Firefox runtime, but does not
  register Firefox MCP tools or test an application.
- The web panel is server-rendered and largely usable without JavaScript.
  Existing checks are service/render tests; no Firefox-driven UI suite was
  found. No confirmed Firefox defect was found in the panel CSS review, and no
  shared Chromium/Firefox application suite was found.
- Pairing and privilege approval have security-sensitive cookie, redirect,
  authentication, and origin behavior that needs real-browser coverage.
- Guacamole is an upstream embedded client. Other hosted apps and arbitrary
  project sites own their UI compatibility.

Relevant code: [panel templates](../../common/service_tools/web_panel_templates.py),
[panel service](../../common/service_tools/web_panel_service.py),
[device pairing](../../common/service_tools/device_pairing_service.py),
[privilege approval](../../common/service_tools/privilege_approval.py), and
[agent browser checks](../../lib/agent_browser.py).

## Delivered implementation

Delivered: engine/context inventory with separate discovery,
launch, MCP registration and application coverage states; native and Flatpak
Firefox discovery; opt-in per-user Firefox MCP registration using the pinned
runtime and isolated launcher; a local application suite and project/agent
guidance. Use [browser qualification](../agents/BROWSER_TESTING.md) for maintained
commands, evidence boundaries, and the native ESR release checklist.

Local qualification on Debian 13.7 amd64 used Node 20.19.2 and pinned Playwright
`1.63.0-alpha-2026-08-05`: Chromium `152.0.7977.8` and Firefox `153.0`.
The shared suite passed all 16 cases across both engines, including the
disposable Guacamole boundary using the reviewed Guacamole 1.6.0 client.
Browser profiles and fixture CA trust were temporary; certificate verification
remained enabled. Native ESR and real guacd/XRDP/deployment checks are not
implied by those results.

Reproduced fixes: Firefox's implicit favicon loading generated CSP console
errors on pairing/approval pages, so those pages now declare and serve the
first-party SVG with same-origin image permission. Enlarged text at a 390px
viewport overflowed pairing grids in both engines; grid minima now shrink to
their container. Both cases have regression coverage. No unconfirmed vendor
CSS or user-agent-specific branches were added.

Follow-up review tightened registration checks so a disabled or wrong-engine
Codex entry cannot qualify Chromium, retained unknown registration and runtime
diagnostics, rejected a mismatched managed Playwright pin, and aligned launcher
Node environments with the doctor. Setup now enforces the pinned Playwright
package's Node 20 minimum before installation or reuse. Fixture readiness is
now atomic and refuses nonempty or shared directories. Qualification summaries
derive per-engine results and browser versions from completed tests and reject
missing or incomplete reports. Additional browser checks cover denial, expiry
of an existing form, and pending approval layout without depending on a
previous test's decision.

The implementation work described above is delivered. The local Playwright
results cover the disposable browser checks; native Debian ESR and managed-
deployment qualification remain release checks.

## Completion record

- **Met:** Pinned Playwright Chromium remains the default; Firefox is opt-in
  and reports its own availability and failures.
- **Met:** First-party panel, pairing, and approval checks run with synthetic
  fixtures in both engines from the documented local command.
- **Met:** Routine CI stays browser-payload-free; the intensive suite runs on
  development machines.
- **Met:** Project and agent guidance documents the browser selection path,
  local command, and upstream UI boundaries.
- **Open release check:** Qualify panel behavior, authentication, and internal
  HTTPS trust in a clean native Debian Firefox ESR profile without bypassing
  certificate checks.
- **Met:** Chromium and Firefox checks cover the Guacamole embed/session
  boundary against a disposable service.
- **Met:** Inventory separates discovery, launch, MCP exposure, and application
  testing; connected previews may report engine `unknown`.
- **Met:** First-party browser fixes have regression coverage, and results
  distinguish Basaltwater pages, upstream integrations, and project-owned UI.
- **Scope held:** Browser defaults, trust stores, and network exposure were not
  changed.

## Settled scope decisions

- Pinned Playwright Chromium stays the managed default; Firefox automation is
  separately selectable and opt-in.
- The intensive Chromium/Firefox application suite runs on development machines,
  not CI servers. CI remains fast and browser-payload-free by default.
- Native Debian ESR is qualified on a disposable development desktop before
  release, not as a CI gate.
- Guacamole's Chromium and Firefox embed/session boundary is the only upstream
  integration in the first slice. T3 Code, Gogs, HomeBox, and other upstream
  UI checks are deferred.
- Basaltwater project guides and generated agent/contributor instructions
  recommend running intensive browser suites on developer machines and keeping
  CI fast; projects can explicitly choose another policy.

Mobile Firefox and a global per-project browser support schema remain out of
scope. The completed fixture and version-pinning choices use the repository's
current Playwright/provider architecture; revisit them only when new consumers
or qualification evidence justify a change.

## References

- [Agent browser automation](../agents/BROWSER_AUTOMATION.md) and
  [workstation browser choices](../workstations/WORKSTATIONS.md)
- [Web panel](../services/WEB_PANEL.md), [internal HTTPS](../services/INTERNAL_WEB.md), and
  [client CA trust](../services/CLIENT_CA_TRUST.md)
- [Device pairing](../services/DEVICE_PAIRING.md),
  [privilege approvals](../agents/PRIVILEGE_APPROVALS.md), and
  [Guacamole](../workstations/GUACAMOLE.md)
- [Playwright browser support](https://playwright.dev/docs/browsers#firefox)
- [Playwright MCP configuration](https://github.com/microsoft/playwright-mcp#configuration)
