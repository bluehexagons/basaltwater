---
name: basaltwater-t3-preview-testing
description: Test through T3 server or desktop preview and use configured alternatives when unavailable; managed Playwright is not provisioned.
metadata:
  managed-by: basaltwater
---

# T3 browser testing

This setup supplies T3 guidance, without managed Playwright. Use only browser tools and
desktop/project alternatives actually available under the active session policy.

When T3-first policy applies, call `preview_status`, then `preview_open` once
if no capable tab is attached. A status without a tab is not a disconnect.
Fallback is allowed for absent tools, an explicit alternative-browser request,
or an explicit unsupported/unavailable host from open. Correct actionable errors;
follow the active session's additional fallback rules, including two failures
on the same step when specified, and quote the raw error when switching.

Read [preview interaction, networking, and evidence](references/collaborative-preview.md)
before T3 navigation or input. Retain the tab ID and verify resulting state.
T3 v0.1.3's server tools automate tabs marked `runtime: "server"`. A listed tab
with desktop or missing runtime is outside those tools' reach: report that
limit and open a server tab without passing that tab ID. Desktop automation
requires tools that explicitly support it. The server browser reaches server
loopback directly and can continue after client disconnection. Diagnose routes
and trust on the selected browser host before requesting a gateway forward or
client CA enrollment.

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
