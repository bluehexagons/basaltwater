---
name: basaltwater-t3-preview-testing
description: Test through T3 preview and use configured alternatives after client disconnection; managed Playwright is not provisioned.
metadata:
  managed-by: basaltwater
---

# T3 browser testing

This VM has T3 guidance, without managed Playwright. Use only browser tools and
desktop/project alternatives actually available under the active session policy.

When T3-first policy applies, call `preview_status`, then `preview_open` once
if no capable tab is attached. A status without a tab is not a disconnect.
Fallback is allowed for absent tools, an explicit alternative-browser request,
or an explicit unsupported/unavailable host from open. Correct actionable errors;
navigation, timeout, pane, and TLS failures alone do not establish host absence.

Read [preview interaction, networking, and evidence](references/collaborative-preview.md)
before T3 navigation or input. Retain the tab ID and verify resulting state.

Once policy permits fallback, choose a ready configured desktop browser or
existing project Playwright tests automatically. Read the installed desktop skill
and inspect readiness before its launch/input; no managed Playwright stack is
implied. Do not wait for the user to reopen T3 or restart T3 to restore testing.
If no alternative remains, complete non-browser checks and report the exact gap.

Check state before repeating uncertain submissions. Profiles/logins are separate.
Report browser, origin, and observed behavior; desktop pixels do not establish
DOM/network checks or disconnected-client trust. Keep TLS, loopback binding,
task scope, and human pause intact.

Keep an unknown preview engine `unknown`; a viewport preset does not establish
Firefox coverage. For projects created with Basaltwater, document target engines
and local browser-test commands in agent/contributor guidance. Prefer intensive
browser suites on developer machines with the project's locked dependencies and
fast CI checks, unless the project explicitly chooses another policy.
