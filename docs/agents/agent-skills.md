# Managed agent workflow skills

Basaltwater installs shared skills for Codex and OpenCode in
`~/.agents/skills` and Claude Code skills in `~/.claude/skills`. Open the
selected `SKILL.md`, then the reference for the task. Check the active
session for tool and skill exposure.

Use `basaltw agent manifest . --capability skills --json` to inspect the
installed Basaltwater catalog and instruction footprint.

For project commands, long-running checks, and visual replies in T3, use the
[project-development skill](../../common/agent_skills/basaltwater-project-development/SKILL.md)
and its [T3 workflow reference](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md).

## Choose a topic

<a id="access-from-different-sessions"></a>
- [Skill access by session](agent-skill-access.md) Provider discovery, freshness, and instruction footprint.

<a id="installed-skills"></a>
- [Installed skill catalog](agent-skill-catalog.md) Profiles, providers, and browser capability routing.

<a id="reconciliation-and-ownership"></a>
<a id="maintaining-the-catalog"></a>
- [Skill bundle maintenance](agent-skill-maintenance.md) Installation ownership, packaging, and validation.
