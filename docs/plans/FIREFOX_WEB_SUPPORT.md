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

## Implementation

Delivered: engine/context inventory with separate discovery,
launch, MCP registration and application coverage states; native and Flatpak
Firefox discovery; opt-in per-user Firefox MCP registration using the pinned
runtime and isolated launcher; a local application suite and project/agent
guidance. Use [browser qualification](../BROWSER_TESTING.md) for maintained
commands, evidence boundaries, and the native ESR release checklist.

Local qualification on Debian 13.7 amd64 used Node 20.19.2 and pinned Playwright
`1.63.0-alpha-2026-08-05`: Chromium `152.0.7977.8` and Firefox `153.0`.
The shared suite passed all 14 cases across both engines, including the
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

1. **Add honest browser inventory and health reporting.** Report engine,
   version, context, origin, and separate discovered / launchable /
   MCP-registered / application-tested states. Detect apt and Flatpak desktop
   Firefox. Do not infer Firefox from an unknown connected-client preview.
2. **Create a first-party browser test harness.** Run the panel, pairing, and
   approval pages against local fixtures, synthetic state, and mocked system
   calls. Run the same core interactions in pinned Playwright Chromium and
   Firefox on development machines. Cover navigation, keyboard/focus,
   responsive layout, theme, disclosures, forms, auth, redirects, nonce/CSRF
   behavior, downloads, console errors, and failed required requests. Bound
   parallel workers to the host's available resources; retain traces and
   screenshots on failure in private local output. Never approve a real
   command or enroll a real device in tests.
3. **Keep CI inexpensive and qualify native ESR separately.** Do not download
   browser payloads or run the intensive browser suite on CI servers. Keep
   routine CI on fast unit, service, and render checks. Use a clean Debian
   Firefox ESR profile on a development machine to check panel layout,
   authentication, and documented internal-HTTPS CA flow before release. Keep
   invalid certificates rejected and record Firefox/OS versions and results.
4. **Improve Chromium and offer Firefox agent automation as opt-in.** Preserve
   the exact Playwright-to-Chromium pairing, isolated profile, private evidence
   directory, safe defaults, and Chromium MCP as the default. Improve its
   application-level verification and report engine/version/context clearly.
   Extend the provider and doctor for Firefox, registering a distinct Firefox
   MCP configuration only when selected. Keep project installs bound to
   project lockfiles; do not add packages or system dependencies implicitly.
5. **Fix only reproduced first-party issues.** Check `:has()` layout,
   disclosure markers, meter appearance, wrapping/overflow, zoom, and dark
   mode at supported desktop and narrow viewports. Use standards and feature
   detection; avoid user-agent branches. Keep vendor CSS only with a tested
   reason.
6. **Document the project and upstream boundary.** Update browser-testing and
   Basaltwater project guidance with the Chromium / Firefox ESR / Playwright /
   project / unknown-preview decision path. Recommend that projects made with
   Basaltwater run intensive browser suites on developer machines, with
   concise local commands and separate fast CI checks; projects may document a
   different policy when their cost and release needs justify it. Qualify only
   the Guacamole embed/session boundary in Chromium and Firefox, using a
   disposable service; route upstream UI defects to their owners. Defer T3 Code,
   Gogs, HomeBox, and other upstream UI checks. Defer a global project
   browser-support manifest field until multiple consumers need it.

## Acceptance criteria

- Pinned Playwright Chromium remains the default automation baseline; Firefox
  is separately selectable and reports its own availability and failures.
- First-party panel, pairing, and approval browser checks run with synthetic
  fixtures in both engines from a documented developer-machine command.
- Default CI does not download browser payloads or run the intensive browser
  suite; fast unit, service, and render checks remain in CI.
- Guidance for projects made with Basaltwater recommends local intensive
  browser testing and separate fast CI checks, with the local command and
  browser targets documented.
- A documented native ESR qualification covers panel behavior, auth, and
  internal HTTPS trust before release without bypassing certificate checks.
- Chromium and Firefox qualification covers the Guacamole embed/session
  boundary against a disposable service; other upstream application UIs are
  out of scope.
- Browser inventory distinguishes discovery, launch, MCP exposure, and an
  application test; connected previews can report engine `unknown`.
- Any browser-specific UI fix has a reproduction or regression test.
- Results distinguish Basaltwater-owned pages, upstream app integration, and
  project-owned UI. No browser-default, trust-store, or network-exposure change
  is part of this work.

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

Mobile Firefox and a global per-project browser support schema are out of the
first slice. The implementation agent can choose the fixture layout and
version-pinning details within the repository's current Playwright/provider
architecture.

## References

- [Agent browser automation](../BROWSER_AUTOMATION.md) and
  [workstation browser choices](../WORKSTATIONS.md)
- [Web panel](../WEB_PANEL.md), [internal HTTPS](../INTERNAL_WEB.md), and
  [client CA trust](../CLIENT_CA_TRUST.md)
- [Device pairing](../DEVICE_PAIRING.md),
  [privilege approvals](../PRIVILEGE_APPROVALS.md), and
  [Guacamole](../GUACAMOLE.md)
- [Playwright browser support](https://playwright.dev/docs/browsers#firefox)
- [Playwright MCP configuration](https://github.com/microsoft/playwright-mcp#configuration)
