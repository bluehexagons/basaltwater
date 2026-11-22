---
name: basaltwater-browser-testing
description: Choose between T3 preview, managed Playwright, and available desktop browsers.
metadata:
  managed-by: basaltwater
---

# Browser testing

Follow the active session's browser policy and tool schemas. Consult only the
workflow needed for the task.

| Need | Workflow |
| --- | --- |
| T3 collaborative preview, private routes, or Chrome host problems | [Shared preview reference](../basaltwater-project-development/references/collaborative-preview.md) |
| Managed Playwright readiness or repeatable browser checks | [Playwright](references/playwright.md) |
| Canvas/WebGL controls or gameplay timing | [Canvas testing](references/canvas.md) |
| Native dialogs, desktop integration, or visual fallback | [Desktop browser](references/desktop-browser.md) |

When session policy permits fallback, use a ready configured option or the
project's existing browser tests. Managed Playwright needs healthy host
readiness and tools exposed to the session. Verify actual behavior and report
the browser, origin, and any coverage gap. Check state before retrying
uncertain input. Keep TLS, loopback binding, and desktop pause intact.
