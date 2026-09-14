# Managed agent workflow skills

infra-tools installs concise operational skills for Codex and OpenCode under
the shared `~/.agents/skills` directory. The skills describe VM-specific
commands and boundaries that a general coding agent cannot infer reliably from
the project alone.

## Installed skills

The limited [`agent_cachyos` profile](CACHYOS.md) has a separate catalog:
`infra-tools-cachyos-workstation`, `infra-tools-cachyos-workspace`, and optional
`infra-tools-cachyos-t3code`. It does not receive the VM or browser skills below.
Its installer reconciles known managed VM skills while preserving personal
skills. See the [CachyOS guide](CACHYOS.md#skills-diagnostics-and-boundaries) for
scope and reruns.

A normal agent-enabled setup that selects Codex or OpenCode receives these
base skills:

| Skill | Use it for |
| --- | --- |
| `infra-tools-agent-operations` | Readiness checks, deliberate terminal-agent updates, maintenance holds, and controller-side credential rotation |
| `infra-tools-agent-workspace` | Isolated branches and worktrees for concurrent tasks |
| `infra-tools-deploy-smoke` | Preflight and layered smoke checks for test deployments |
| `infra-tools-shared-assets` | SMB/SSHFS asset boundaries and Git LFS workflows |
| `infra-tools-vm-triage` | Redacted host diagnostics and support snapshots |

Browser guidance is selected from the resolved setup instead of being included
in the base catalog:

| Skill | Installed browser capabilities |
| --- | --- |
| `infra-tools-playwright-testing` | Managed Playwright only |
| `infra-tools-t3-preview-testing` | T3 Code collaborative preview only |
| `infra-tools-browser-testing` | Both managed Playwright and T3 Code preview |

The combined skill selects Playwright for repeatable VM-origin work when live
collaboration is unnecessary, and selects T3 preview for shared interaction or
client-origin checks. It routes immediately to Playwright when the T3 app is
closed. T3-only guidance treats a closed app or untrusted client certificate as
a browser coverage gap, not a prerequisite that blocks non-browser work.

Other provisioned capabilities add focused skills:

| Skill | Installed with |
| --- | --- |
| `infra-tools-t3code` | T3 Code web service |
| `infra-tools-web-gateway` | T3 Code setup or the Godot web bundle; the skill publishes and verifies managed static snapshots or live forwards |
| `infra-tools-godot-web` | Godot web bundle |
| `infra-tools-desktop` | Shared desktop capability, including an explicit `--desktop` or `--rdp` on an agent VM |

Desktop guidance describes the one shared XRDP session, native application
launch, screenshot/input commands, and human takeover. Browser tests almost
always use T3 Code or Playwright when available; a running desktop is not a
reason to switch to pixel automation. Desktop-specific integration and a
justified fallback remain available. See [XRDP](XRDP.md).

A skill does not install the capability it describes. A setup with neither T3
Code nor managed Playwright receives no browser skill, avoiding instructions
for tools that cannot exist on that VM.

Claude Code does not consume the shared Codex/OpenCode skill location, so a
Claude-only setup receives the agent management command but not this skill set.

## Reconciliation and ownership

Setup copies repository-owned `SKILL.md` files into the target account. A rerun
refreshes files containing `managed-by: infra_tools` and leaves identical files
alone. It removes obsolete infra-tools-managed desktop/browser skills when the
selected capability combination changes, while preserving unrelated skills and
user configuration. It refuses symlinked paths, directories owned by another
user, and a same-name skill without the managed marker.

The Playwright doctor includes the selected browser workflow skill in
capability health, and the T3 doctor requires exactly one T3-capable browser
variant. Running both checks on a combined VM therefore verifies the combined
skill rather than accepting independent Playwright-only and T3-only guidance.

An older VM receives the current base set when its saved setup is rerun from an
updated infra-tools control plane. The same setup rerun also updates selected
Codex, Claude Code, and OpenCode executables through the verified user-scoped
updater; `infra-tools agent update` remains available for an agent-only update.
Neither command refreshes infra-tools or these skills.

For an explicit `--steps` setup, `install_agent_workflow_skills` installs the
base and selected browser set. The Playwright, T3 Code, and Godot capability
steps reconcile the relevant catalog as well, so capability-only and
custom-step runs still produce appropriate guidance.

## Maintaining the catalog

Skill sources live in `common/agent_skills`. Keep each entrypoint short and
self-contained, with a precise discovery description and only non-obvious VM
behavior. Add a base skill to `BASE_AGENT_SKILL_NAMES` in
`common/agent_steps.py`. Browser variants belong in
`BROWSER_AGENT_SKILL_NAMES` and the capability selector; other capability skill
tuples should extend the base constant so standalone capability setup remains
complete.

The installer currently copies only `SKILL.md`; sibling `references/`,
`scripts/`, and `agents/` files are not deployed. Keep essential commands and
fallbacks in the entrypoint. Link optional detailed procedures to the maintained
operator documentation with an absolute repository URL, and mention the local
checkout path as an alternative. Do not assume the agent's application checkout
contains infra-tools documentation. Supporting skill files require installer
and reconciliation support before skills can depend on them.

During an audit, check command examples against their parsers and implementation,
check readiness claims against doctor results, and review all three browser
variants together. Select diagnostics for the task instead of treating examples
as a mandatory checklist. Keep deployment checks scoped to the repositories
being changed and make mutation effects explicit. Platform-specific certificate
enrollment lives in [Client CA trust](CLIENT_CA_TRUST.md), while browser skills
retain the trust-verification boundary and fallback behavior.

Validate changes with the repository tests and the Codex skill validator when
it is available:

```bash
python3 -m unittest tests.test_agent_skills tests.test_t3_agent_skills
python3 -m unittest tests.test_godot_web_host
```

Also run `git diff --check` and verify that every skill retains the
`managed-by: infra_tools` marker before committing.
