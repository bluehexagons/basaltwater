# Managed agent workflow skills

Basaltwater installs concise project workflow and operational skills for Codex
and OpenCode under the shared `~/.agents/skills` directory and for Claude Code
under its personal `~/.claude/skills` directory. The project workflow skill
applies across repositories; operational skills describe profile-specific
commands and boundaries that a general coding agent cannot infer from a project
alone.

## Access from different sessions

Keep discovery separate from procedural context. The catalog advertises skill
names and descriptions; an installed base skill does not require reading its
whole body on every request. Basaltwater entrypoints contain task routing and
essential boundaries. Read only the linked workflow needed for the current
operation, then load more detail when a specific step requires it. Do not
preload every reference or run an operations checklist for ordinary code edits.

Operations, tasks, workspaces, triage, browser, desktop, and gateway skills use
this layout. Browser selection and automatic fallback stay in the entrypoint;
canvas timing, recordings, credential rotation, and other specialized procedures
are loaded on demand. Development-site refresh responsibility also stays visible
without loading all publication and service commands.

The workspace skill also routes long-running/shared projects to
[local project memory](agent-memory.md). Read a bounded index and selected
records when relevant; memory is not automatically injected into prompts.
Secret entries register private file locations without reading their values.

| Launch context | Catalog used |
| --- | --- |
| Codex CLI, including `codex exec` | The provider account's `~/.agents/skills`, plus repository and other Codex-supported scopes |
| T3 Code on Debian or CachyOS | The account running the provider on the selected connection, using the same provider catalog as terminal sessions |
| Basaltwater panel prompt task | The panel account's `~/.agents/skills`; the runner fixes `HOME` and `CODEX_HOME` and starts a new terminal process |
| T3 Code on Windows connected to Ubuntu WSL | The selected distribution's Linux account/catalog |
| Subagent | Its actual account, working directory, and provider configuration; pass relevant skill paths when assigning a fresh worker |

Skill access has three separate checks: the files exist and are readable, the
provider advertises/enables the skill, and the agent reads it for this task.
Basaltwater handles installation. It does not replace the provider's discovery
or guarantee that every skill is loaded on every turn. Codex custom agents
inherit `skills.config` and MCP settings unless overridden; fresh conversation
context and thread-scoped tools are separate. See the
[official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills) and
[subagent configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Run `basaltw agent manifest /absolute/project --json` as the provider account to
list known Basaltwater entrypoints in both user catalogs. `skills` reports
`current`, `different`, `incomplete`, `unsafe`, or `personal` against the running
Basaltwater installation's source. `different` can mean an older bundle or local
edits; setup preserves edited supporting resources rather than overwriting them.
This is an inventory of known Basaltwater names, not personal, plugin, repository,
or system skills. `session_access` stays `unverified`; it does not read provider
configuration or credentials. Use the active skill list (`/skills` in interactive
Codex) to verify discovery, and respect disabled skills. Large Codex catalogs
can omit metadata from initial context; pass the relevant skill name and path.

Choose platform guidance first, then session-specific references. Keep a single
capability-selected catalog per account rather than replacing it whenever an
agent launches. T3 preview, native desktop grants, and unattended permissions
depend on the active session. The shared subagent skill ships
[`references/session-context.md`](../../common/agent_skills/basaltwater-subagents/references/session-context.md)
with a worker assignment example and guidance for each context.

Windows `server_wsl` installs `basaltw`, `basaltwater-wsl-workspace`,
`basaltwater-project-development`, `basaltwater-frontend-design`, and
`basaltwater-subagents` when a supported coding agent is selected. These are
Linux-user skills; native Windows providers have separate homes and are not
provisioned by this profile. Native PowerShell artifact jobs execute committed
scripts rather than Codex prompts. WSL does not receive VM maintenance, panel,
XRDP, or managed browser skills. See
[Windows/WSL](../workstations/windows-wsl.md).

## Installed skills

The limited [`agent_cachyos` profile](../workstations/cachyos.md) has a separate catalog:
`basaltwater-cachyos-workstation`, `basaltwater-cachyos-workspace`,
`basaltwater-cachyos-desktop`, the shared `basaltwater-subagents`, and optional
`basaltwater-cachyos-t3code`.
It does not receive the VM or browser skills below.
Its installer reconciles known managed VM skills while preserving personal
skills. See the [CachyOS guide](../workstations/cachyos.md#skills-diagnostics-and-boundaries) for
scope and reruns.

| CachyOS skill | Use it for |
| --- | --- |
| `basaltwater-cachyos-workstation` | Native package bundles, diagnostics, and desktop/session boundaries |
| `basaltwater-cachyos-workspace` | User-owned repository workspaces and safe reruns |
| `basaltwater-project-development` | Repo-agnostic workflow, local instructions, scoped changes, and useful independent review or delegation |
| `basaltwater-frontend-design` | New frontend screens, substantial UI redesigns, responsive behavior, and interaction design |
| `basaltwater-subagents` | Bounded delegation, complete local worktree lifecycle, and cost-aware project model learning on either platform |
| `basaltwater-cachyos-desktop` | Task-scoped KDE portal/AT-SPI control, native Blender and media workflows, reusable grants, and physical-key recovery |
| `basaltwater-cachyos-t3code` | T3 desktop or managed web mode, switching, pairing, and T3 Connect |

The desktop skill is always included in this catalog; installing its guidance
does not start control or establish application readiness. Its shipped Blender,
media, and native-control references explain prerequisite selection, owner
consent, saved-grant restoration, pause/revocation, and workflow verification.

A Debian agent-enabled setup that selects Codex, OpenCode, or Claude Code
receives these base skills:

| Skill | Use it for |
| --- | --- |
| `basaltwater-project-development` | Repo-agnostic workflow, local instructions, scoped changes, and useful independent review or delegation |
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

In T3 sessions with collaborative preview tools, browser skills follow T3's
preview-first policy: status, then open when no capable tab is attached.
Fallback requires absent tools, an explicit request for another browser, or an
explicit unsupported/unavailable response from open, including a disconnected
or missing client. Once policy permits fallback, agents automatically select
a ready configured alternative without waiting for the user to reopen T3.
Outside a preview-first policy, choose the option that covers the task. Both T3
variants resolve loopback ports through `basaltwater-web preview resolve`,
verify uncertain input acknowledgments, scope diagnostics by navigation and
component, and inspect service-worker freshness when updates appear missing.
Browser guidance also covers existing project Playwright tests and configured
desktop browsers. It checks their readiness before declaring browser coverage
blocked, preserves profiles and human pause, and reports browser/network origin
and the checks actually performed. Browser coverage gaps do not block unrelated
non-browser work. See [Browser automation](browser-automation.md#automatic-continuation-after-client-disconnection).

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

## Reconciliation and ownership

Setup copies repository-owned `SKILL.md` files and complete `references/`,
`scripts/`, `assets/`, and `agents/` trees into each selected personal catalog.
Scripts retain executable permission, other resources use mode 0644, and
individual files are limited to 16 MiB. Installing a bundle never runs its scripts.
A rerun refreshes files containing `managed-by: basaltwater` and leaves identical files
alone. It removes obsolete Basaltwater-managed desktop/browser skills when the
selected capability combination changes, while preserving unrelated skills and
user configuration. It refuses symlinked paths, directories owned by another
user, and a same-name skill without the managed marker.
The refresh runs after provider configuration is copied, so an older managed
catalog included in `--agent-config active` cannot replace the current guidance.

Supporting resources are tracked with SHA-256 digests in the private
`.basaltwater-files.json` inventory. Refreshes preflight each bundle before
replacing files and refuse untracked same-name resources or modified tracked
resources. Move or rename those personal files before retrying; setup will not
overwrite them. Obsolete unchanged resources are removed, while edited resources
and unrelated personal files remain. Retirement removes the managed entrypoint,
unchanged tracked resources, and empty directories only. Symlinked source or
destination entries, special files, and unsafe inventory paths are rejected.
Each file replacement is atomic; the entire catalog is not a single transaction,
so rerun setup after correcting a reported collision. New resource ownership is
recorded before writing files, and previous digests remain until refresh finishes.
An interrupted installation can therefore be retried without treating its new
resources as personal files; missing recorded resources fail readiness checks.
Readiness verifies recorded resources are present, unchanged, user-owned, and
not writable by other users; older entrypoint-only installations remain readable.

The Playwright doctor includes the shared selected browser workflow skill in
capability health, and the T3 doctor requires exactly one T3-capable browser
variant in every installed skill-compatible provider's catalog. Running both
checks on a combined VM therefore verifies the combined skill rather than
accepting independent Playwright-only and T3-only guidance.

An older VM receives the current base set when its saved setup is rerun from an
updated Basaltwater control plane. The same setup rerun also updates selected
Codex, Claude Code, and OpenCode executables through the verified user-scoped
updater; `basaltw agent update` remains available for an agent-only update.
Setup refreshes the selected skills from its source checkout. The agent-only
update refreshes neither Basaltwater nor skills; upgrade Basaltwater separately
before rerunning setup to install newer guidance.

Current skills invoke `basaltw`; their `basaltwater-*` IDs remain stable.
Normal agent setup installs `basaltw` before refreshing skills. When
upgrading an older VM with explicit `--steps`, include
`install_agent_cli_launcher` before `install_agent_workflow_skills` or another
capability step that refreshes the catalog, for example
`--steps 'install_agent_cli_launcher install_agent_workflow_skills'`.
Capability-only runs must not publish new guidance while leaving an old-only
launcher installation. On CachyOS, rerun the current installer to refresh the
user bootstrap before updating skills. Remaining infra-tools installations
require the [intermediate migration version](../migration-guide.md) first.

## Maintaining the catalog

For project discovery, `basaltw agent manifest` reports tools, workspace
conventions and declared deployment mappings. See [Agent environment manifest](agent-environment.md).

Skill sources live in `common/agent_skills`. Keep each entrypoint short and
self-contained, with a precise discovery description and non-obvious platform
behavior. The project-development and frontend-design skills are cross-project
guidance: keep them repository-agnostic and defer platform details and
delegation mechanics to the relevant workspace and subagent skills. Add shared
skills to `BASE_AGENT_SKILL_NAMES` in `common/agent_steps.py` and the parallel
`CACHYOS_SKILLS` catalog in `common/cachyos_steps.py`. Browser variants belong in
`BROWSER_AGENT_SKILL_NAMES` and the capability selector; other capability skill
tuples should extend the base constant so standalone capability setup remains
complete. Platform-neutral skills such as `basaltwater-subagents` also belong
there; keep platform-specific skills in their own catalog. Add shared skills
intended for Ubuntu WSL to `WSL_SKILL_NAMES` in `common/wsl_steps.py`, wired by
`plugins/wsl.py`.

Keep essential discovery and operational boundaries in the entrypoint and link
detailed procedures to shipped relative references. For example,
`basaltwater-t3code` keeps host-side update and migration instructions in
`references/updates.md`. Supporting trees are packaged in wheels as well as
deployed from source. Do not ship credentials, caches, generated bytecode, or
unnecessary assets, and do not assume an application checkout contains
Basaltwater documentation. The agent suite covers bundle refresh, collisions,
ownership, and retirement; the wheel artifact check requires the shared subagent,
T3, unattended task, and CachyOS native-control references.

During an audit, check command examples against their parsers and implementation,
check readiness claims against doctor results, and review all three browser
variants together. Select diagnostics for the task instead of treating examples
as a mandatory checklist. Keep deployment checks scoped to the repositories
being changed and make mutation effects explicit. Platform-specific certificate
enrollment lives in [Client CA trust](../services/client-ca-trust.md), while browser skills
retain the trust-verification boundary and fallback behavior.

Validate changes with the repository tests and the Codex skill validator when
it is available:

```bash
make docs-check
python3 -m unittest tests.test_agent_skills tests.test_t3_agent_skills
python3 -m unittest tests.test_agent_skill_contracts tests.test_web_panel_agents
python3 -m unittest tests.test_godot_web_host
```

The skill contract tests parse fenced bash, sh, and fish Basaltwater command
examples with the current CLI parsers without executing them and verify shipped
Markdown references. `make docs-check` also checks local file and heading links
across operator docs, plans, and skill resources. Template tests check task
validation and command permissions; they do not launch paid prompts or prove an
agent will follow every instruction.

Also run `git diff --check` and verify that every skill retains the
`managed-by: basaltwater` marker before committing.
