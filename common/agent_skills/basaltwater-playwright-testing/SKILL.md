---
name: basaltwater-playwright-testing
description: Test web applications with managed Playwright or a configured VM-local alternative.
metadata:
  managed-by: basaltwater
---

# VM-local browser testing

Managed Playwright is provisioned; this skill does not imply a T3 preview.
Choose the ready configured option that fits the task under its session policy.

Read [readiness and origin](references/playwright.md) before using managed
Playwright. Its doctor must be healthy and MCP tools must actually be exposed.
Read [interaction and evidence](references/interaction.md) for canvas, held
input, timing, screenshots, or asynchronous review.

If tools are missing or unusable, continue automatically with existing project
Playwright tests or a ready configured desktop browser when policy permits.
Read its installed desktop skill and check status/doctor before launch/input.
Do not wait for T3 to reopen, restart services, or install another browser stack
to fill the gap. Honor human pause and preserve unrelated applications.

Inspect state before repeating an uncertain submission. Profiles and logins are
separate. Verify actual behavior and report browser, origin, and coverage;
VM checks cannot prove connected-client routing or trust. Preserve TLS,
loopback binding, private evidence, and the managed launcher.
