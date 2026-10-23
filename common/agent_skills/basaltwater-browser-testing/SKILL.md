---
name: basaltwater-browser-testing
description: Choose a configured browser for web testing, including T3 server or desktop preview, and continue when a browser is unavailable.
metadata:
  managed-by: basaltwater
---

# Browser testing

This VM has T3 preview and managed Playwright; a desktop browser is optional.
Choose a ready option that covers the task under the session's browser policy.

| Need | Read only the matching workflow |
| --- | --- |
| Live collaboration or server-side browser automation | [T3 preview](references/collaborative-preview.md) |
| Repeatable DOM, console, network, fixture, or VM loopback checks | [Playwright](references/playwright.md) |
| Canvas/WebGL input, held keys, or gameplay timing | [Canvas testing](references/canvas.md), alongside the selected browser |
| Native dialogs, desktop integration, or an available visual alternative | [Desktop browser](references/desktop-browser.md), then its installed desktop skill |

When T3-first policy applies, call `preview_status`, then `preview_open` once
if no automation-capable tab is attached. Status without a tab is not proof of
disconnection. Use alternatives when tools are absent, the user requests them,
or open explicitly reports an unsupported/unavailable host. Inspect actionable
errors and follow the active session's additional fallback rules, including two
failures on the same step when specified; quote the raw error when switching.
T3 v0.1.2 prefers a server browser that reaches server loopback directly and can
continue after client disconnection. Diagnose routes and trust on the selected
browser host before requesting a gateway forward or client CA enrollment.

Once policy permits fallback, continue automatically with a ready configured
alternative. Do not wait for the user to reopen T3 or restart its service.
Inspect other configured options before declaring all browser coverage blocked.
Playwright needs a healthy capability doctor and tools actually exposed to this
session; missing MCP tools do not rule out existing project tests or a desktop.

After a disconnected or uncertain input, inspect state before retrying a
submission or toggle. Keep using the selected alternative rather than polling
T3 before every action. Profiles/logins are separate; do not copy cookies.

Verify rendered state and behavior, not merely successful helper responses.
Report browser, network origin, and observed checks. VM success does not verify
the disconnected client's routes or trust; pixel-only evidence is not a
DOM/network assertion. Keep TLS, loopback binding, private evidence, and human
desktop pause intact. Browser checks alone do not authorize publication.

For projects created with Basaltwater, document supported engines and the local
browser-test command in project agent/contributor guidance. Recommend intensive
Chromium/Firefox tests on developer machines and fast unit/service/render CI
checks unless the project explicitly chooses another policy. Use its locked
dependencies. Distinguish Playwright Firefox, native ESR, and preview engine
`unknown`; discovery or a launch smoke is not an application result.
