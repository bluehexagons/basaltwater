# Installed skill catalog

[Documentation index](agent-skills.md)

## Installed skills

The limited [`agent_cachyos` profile](../workstations/cachyos.md) has a separate catalog:
`basaltwater-cachyos-workstation`, `basaltwater-cachyos-workspace`,
`basaltwater-cachyos-desktop`, the shared `basaltwater-project-development`,
`basaltwater-project-adoption`, `basaltwater-frontend-design`, and
`basaltwater-subagents`, plus optional
`basaltwater-cachyos-t3code` and `basaltwater-t3-preview-testing` for selected
or existing managed T3 environments. The browser skill describes session preview
tools and existing alternatives, without claiming managed Playwright is installed.
The remaining VM/browser skills below are excluded.
Its installer reconciles known managed VM skills while preserving personal
skills. See the [CachyOS guide](../workstations/cachyos.md#skills-diagnostics-and-boundaries) for
scope and reruns.

| CachyOS skill | Use it for |
| --- | --- |
| `basaltwater-cachyos-workstation` | Native package bundles, diagnostics, and desktop/session boundaries |
| `basaltwater-cachyos-workspace` | User-owned repository workspaces and safe reruns |
| `basaltwater-project-development` | Repo-agnostic workflow, local instructions, scoped changes, and useful independent review or delegation |
| `basaltwater-project-adoption` | Incrementally convert an existing project while preserving useful conventions and native tooling |
| `basaltwater-frontend-design` | New frontend screens, substantial UI redesigns, responsive behavior, and interaction design |
| `basaltwater-subagents` | Bounded delegation, complete local worktree lifecycle, and cost-aware project model learning on either platform |
| `basaltwater-cachyos-desktop` | Task-scoped KDE portal/AT-SPI control, native Blender and media workflows, reusable grants, and physical-key recovery |
| `basaltwater-cachyos-t3code` | T3 desktop or managed web mode, switching, pairing, and T3 Connect |
| `basaltwater-t3-preview-testing` | T3 preview checks and available browser alternatives when a T3 environment is selected or retained |

The desktop skill is always included in this catalog; installing its guidance
does not start control or establish application readiness. Its shipped Blender,
media, and native-control references explain prerequisite selection, owner
consent, saved-grant restoration, pause/revocation, and workflow verification.
The CachyOS T3 entrypoint routes desktop and web-service procedures to separate
references, so routine desktop work need not load web activation and pairing steps.

A Debian agent-enabled setup that selects Codex, OpenCode, or Claude Code
receives these base skills:

| Skill | Use it for |
| --- | --- |
| `basaltwater-project-development` | Repo-agnostic workflow, local instructions, scoped changes, and useful independent review or delegation |
| `basaltwater-project-adoption` | Incrementally convert an existing project while preserving useful conventions and native tooling |
| `basaltwater-frontend-design` | New frontend screens, substantial UI redesigns, responsive behavior, and interaction design |
| `basaltwater-agent-operations` | Readiness checks, deliberate terminal-agent updates, maintenance holds, controller-side credential rotation, and unattended panel task guidance |
| `basaltwater-agent-tasks` | Scheduling authorized follow-ups, recurring/event checks, bounded goals, and portable context handoffs from T3 or terminal agents |
| `basaltwater-agent-workspace` | Managed VM worktrees, integration/cleanup, and selected durable project notes or todos |
| `basaltwater-subagents` | Bounded delegation, complete local worktree lifecycle, and cost-aware project model learning on either platform |
| `basaltwater-deploy-smoke` | Preflight and layered smoke checks for test deployments |
| `basaltwater-shared-assets` | SMB/SSHFS asset boundaries and Git LFS workflows |
| `basaltwater-vm-triage` | Redacted host diagnostics and support snapshots |

The shared subagent skill ships the same complete worktree lifecycle and
cost-aware OpenAI model guidance on Debian and CachyOS. Both workspace skills
direct agents to this local bundle; its references and starter policy have one
repository source. The parent owns file assignments, standard worker handoffs,
integration, validation, outcome recording, and cleanup. Read-only reviewers
can inspect the primary checkout; editors need isolation. Project policies and
reviewed results support new model IDs without a Basaltwater upgrade. See
[Subagent workflows and model choices](agent-subagents.md) for commands,
adaptation, and the standard-service/no-max defaults.

Browser guidance is selected from the resolved setup instead of being included
in the base catalog:

| Skill | Installed browser capabilities |
| --- | --- |
| `basaltwater-playwright-testing` | Managed Playwright only |
| `basaltwater-t3-preview-testing` | T3 Code collaborative preview only |
| `basaltwater-browser-testing` | Both managed Playwright and T3 Code preview |

Both T3-capable variants link to the same local
[preview reference](../../common/agent_skills/basaltwater-project-development/references/collaborative-preview.md)
in the co-installed project-development bundle. It covers runtime and network
origin, gateway URLs, Chrome preparation, certificate trust, and evidence
after fallback. The active session decides when to use T3 preview and when
fallback is permitted. See [Browser automation](browser-automation.md#automatic-continuation-after-client-disconnection).

Deployment, Godot, and gateway skills defer browser selection to this same
session policy. Unattended panel prompts cannot borrow a connected T3 thread's
tools; a healthy managed browser installation still needs its MCP tools exposed
to the task session. Missing tools do not preclude ready project tests or a
configured desktop browser. Local build, export validation, and browser smoke checks do
not imply publication or live forwarding.

The operations skill ships `references/unattended-tasks.md` for the panel's Codex
runner. It distinguishes execution settings from task authorization, describes
review between recurring editing runs, and explains that blocked findings can
still accompany a successful CLI exit. Maintenance holds are shared account
state; a runtime cap does not create one, and one task must not shorten or
release a hold needed by another. Stopping a local task does not cancel an
external broker request or reverse its effects.

The task skill ships scheduling recipes and describes the shared CLI/MCP
controls, worker readiness, initial handoffs, goal limits, and current T3
boundaries. Standard managed Codex defaults expose writable task MCP controls;
user overrides and hardened restrictions still apply. See
[Agent tasks and goals](agent-tasks.md) for activation and persistence details.

Other provisioned capabilities add focused skills:

| Skill | Installed with |
| --- | --- |
| `basaltwater-t3code` | T3 Code web service |
| `basaltwater-web-gateway` | T3 Code setup or the Godot web bundle; the skill publishes and verifies managed static snapshots or live forwards |
| `basaltwater-godot-web` | Godot web bundle |
| `basaltwater-desktop` | Shared desktop capability, including an explicit `--desktop` or `--rdp` on an agent VM |

Desktop guidance describes the one shared XRDP session, native application
launch, screenshot/input commands, and human takeover. Agents choose a browser
that fits the task under the session policy: T3 for collaboration, Playwright
for repeatable DOM/network checks, or the desktop browser for native integration,
visual interaction, and fallback. A running desktop alone does not establish
browser readiness or DOM/network coverage. See [XRDP](../workstations/xrdp.md).

A skill does not install the capability it describes. A setup with neither T3
Code nor managed Playwright receives no browser skill, avoiding instructions
for tools that cannot exist on that VM.

Claude-only setups receive the same capability-selected catalog, without creating
the Codex/OpenCode shared directory. Mixed setups install independent copies in
both locations. Personal Claude skills follow the
[upstream skill location contract](https://code.claude.com/docs/en/skills#choose-where-skills-load).
This adds workflow guidance; it does not add Claude Playwright MCP registration.
