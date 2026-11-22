# Agent browser automation

Basaltwater can provision a browser that Codex and OpenCode use directly from
their agent sessions. This is separate from the workstation browser selected
with `--browser`: agent automation uses a pinned Playwright MCP package and its
matching Chromium build, while `--browser` installs an interactive desktop or
terminal browser with a separate profile and interaction workflow.

## Choose a topic

<a id="provisioning"></a>
<a id="installed-components-and-agent-configuration"></a>
- [Provision managed Playwright](browser-provisioning.md) Profile flags, installed components, and agent configuration.

<a id="what-agents-can-do"></a>
<a id="choosing-playwright-or-collaborative-preview"></a>
<a id="automatic-continuation-after-client-disconnection"></a>
<a id="file-import-download-and-reopen"></a>
<a id="real-time-canvas-testing"></a>
<a id="held-input-without-intermediate-tool-delays"></a>
<a id="capture-diagnostics"></a>
- [Choose and use a browser session](browser-workflows.md) Tool selection, data movement, screenshots, and real-time canvas checks.

<a id="t3-browser-host-preparation"></a>
<a id="collaborative-preview-and-private-networks"></a>
- [T3 preview and private routing](browser-t3-preview.md) Server preparation, connected-client routes, and certificate trust.

<a id="offline-cache-freshness"></a>
<a id="verification"></a>
<a id="engine-and-context-coverage"></a>
<a id="opt-in-managed-firefox-tools"></a>
<a id="managed-chromium-integration"></a>
<a id="security-and-operating-limits"></a>
- [Browser readiness and operating limits](browser-readiness.md) Engine coverage, cache freshness, verification, and security boundaries.
