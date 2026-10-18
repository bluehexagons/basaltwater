# Unattended panel tasks

The web panel's **Agents** screen and `basaltw agent tasks` share saved Codex
prompts, goals, checkpoints and managed workspaces independently of T3.
Use `basaltw agent tasks capabilities --json` to inspect executor and worker
availability. CLI controls and optional stdio MCP are available to standalone
Codex and agents inside T3. These Basaltwater tasks run independently of T3;
native T3 schedules can instead return to their owning conversation. Use the
installed `basaltwater-subagents` skill's T3 scheduling reference to choose one
scheduler and inspect its actual target, limits, and provider settings.
Use the `basaltwater-agent-tasks` skill to schedule authorized follow-ups,
hand off current findings, or pursue bounded goals with persistent context.
Read [the unattended task guidance](unattended-tasks.md) when preparing
or running recurring prompts. It covers execution permissions, isolated working
directories, blocked checks, and review between editing runs. Inspection tasks
must not use the update, repair, login, hold, or privilege-request examples below.

For quick project discovery, run `basaltw agent manifest --json` from its
checkout. It reports available tools, workspace/artifact conventions, and
explicit branch-to-deployment mappings from optional `basaltwater-agent.json`.
Projects can also declare required executables and named validation recipes;
the summary reports missing prerequisites and displays commands without running
them. Use relevant project recipes when choosing checks for the user's task.
Undeclared `dev` and `staging` destinations remain unknown; tool presence does
not prove health or authentication. See the
[environment manifest guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/agent-environment.md)
or `docs/agents/agent-environment.md` in a Basaltwater checkout.

Blender is optional on Debian agent hosts: setup `--blender` selects the
configured Debian release's APT package, including on headless `agent_vm`.
Normal host package maintenance owns its updates. Check `blender --version`
and use the manifest's background-render recipe for repeatable scene output;
installation does not establish GPU or interactive desktop readiness. Native
editing uses the shared desktop skill when that capability is installed.
See [desktop software support and flags](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/workstations.md#desktop-software-support-and-flags)
and [Blender workflows](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-development.md#blender).

For image evidence, `basaltw agent visuals compare BEFORE.png AFTER.png --json`
creates a private standalone viewer with synchronized zoom/scroll, overlays,
pixel differences, and optional per-image settings. `agent visuals capture`
replays a project capture command and settings in two managed Git worktrees;
it retains worktrees and evidence rather than swapping primary assets. Read the
[visual comparison guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/visual-comparisons.md)
or `docs/workstations/visual-comparisons.md` before running revision captures.
