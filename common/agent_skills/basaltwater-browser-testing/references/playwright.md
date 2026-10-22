# Use managed Playwright

For a small engine/context inventory, use `basaltw agent manifest REPOSITORY
--capability browser --json`. The managed capability doctor below qualifies
Chromium and its MCP integration. Project Playwright Firefox and interactive
desktop Firefox have separate readiness; discovery leaves rendering unverified.
When the task explicitly needs Firefox, use `basaltw agent browser doctor
--engine firefox --context project --project REPOSITORY --json` with the project's
existing dependencies. An authorized `browser install` with the same arguments
downloads only the matching engine, preserving packages and lockfiles.
Use `--context managed` without `--project` for the stable test package, or
`--context managed-mcp` for MCP's separate pinned alpha dependency and payload.
Neither command changes MCP's Chromium selection or the session browser policy.
See the [engine/context guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/browser-automation.md#engine-and-context-coverage)
for dependency and coverage limits.

Firefox automation can be explicitly registered as the separate
`basaltwater-playwright-firefox` MCP server with `basaltw agent browser register
--engine firefox --agent-tool codex --json` (or `opencode`), after its `managed-mcp` payload
doctor succeeds and the saved setup has refreshed the managed launcher. This
does not replace Chromium or expose tools to an already running session.
Before using that server, require a healthy `managed-mcp` Firefox engine doctor and
verify that its own tools are exposed. Registration, runtime launch and
application coverage are separate states. Native ESR and Flatpak Firefox remain
desktop contexts with their own profiles and trust.

Before the first VM-local browser action, run:

```bash
basaltw agent doctor --capability browser --json
```

Use Playwright only when `healthy` is true. Follow the stable `issues` and
`remediation` fields when it is not; do not install another browser stack or
mutate the managed launcher. `stale_processes` requires restarting the affected
agent session.

A healthy installation does not establish that its MCP tools are exposed in this
agent session. Unattended panel prompts cannot use a connected T3 thread's tools.
Use only tools actually available to the run. If MCP tools are missing, inspect
configured project Playwright tests or the installed desktop workflow before
declaring browser coverage blocked. Project tests use their own existing
dependencies and documented command; do not bypass the managed launcher, load
its private packages directly, install another stack, or restart services to
make tools appear. If no usable alternative remains, continue non-browser checks
and report the exact missing coverage.

Keep development servers on loopback. Playwright originates on the VM, so it
can reach VM loopback and uses the VM's DNS, routes, source IP, and trust store.
Generated evidence belongs in the private, bounded
`~/.local/state/basaltwater/playwright-mcp` directory by default. Omit
`filename` unless the user requested a workspace deliverable.
