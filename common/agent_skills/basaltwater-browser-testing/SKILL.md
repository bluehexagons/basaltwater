---
name: basaltwater-browser-testing
description: Choose a configured browser for web testing and continue after T3 client disconnection.
metadata:
  managed-by: basaltwater
---

# Browser testing

This VM has T3 preview and managed Playwright; a desktop browser is optional.
Choose a ready option that covers the task under the session's browser policy.

| Need | Read only the matching workflow |
| --- | --- |
| Live collaboration or connected-client routing/trust | [T3 preview](references/collaborative-preview.md) |
| Repeatable DOM, console, network, fixture, or VM loopback checks | [Playwright](references/playwright.md) |
| Canvas/WebGL input, held keys, or gameplay timing | [Canvas testing](references/canvas.md), alongside the selected browser |
| Native dialogs, desktop integration, or an available visual alternative | [Desktop browser](references/desktop-browser.md), then its installed desktop skill |

When T3-first policy applies, call `preview_status`, then `preview_open` once
if no automation-capable tab is attached. Status without a tab is not proof of
disconnection. Use alternatives when tools are absent, the user requests them,
or open explicitly reports an unsupported/unavailable host. Inspect actionable
errors; a navigation, timeout, pane, or TLS failure alone is not host unavailability.

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
