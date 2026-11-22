---
name: basaltwater-playwright-testing
description: Test web applications with managed Playwright or a configured VM-local alternative.
metadata:
  managed-by: basaltwater
---

# VM-local browser testing

Use the active session's browser policy. Follow [Playwright readiness and origin](references/playwright.md)
before acting. Managed Playwright requires healthy readiness and tools exposed
to this session. If it is unavailable and policy permits fallback, use existing
project tests or a ready configured desktop browser. Check its skill, readiness,
and pause state before launch or input. Preserve the managed launcher, TLS,
loopback binding, and unrelated desktop applications. Use existing dependencies
and keep service restarts outside the fallback workflow.

For canvas, held input, timing, or screenshots, open [interaction and evidence](references/interaction.md).
Inspect state before repeating uncertain input. Report browser, origin, observed
behavior, and coverage gaps. VM-local results establish the VM's route and trust.
