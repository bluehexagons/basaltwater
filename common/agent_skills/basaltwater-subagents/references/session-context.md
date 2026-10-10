# Skills and tools in worker sessions

Choose guidance for the account, platform, and tools that actually run the task.
Basaltwater installs complete user bundles under `~/.agents/skills` for
Codex/OpenCode and `~/.claude/skills` for Claude Code. A different account,
container, remote host, or WSL distribution needs its own readable catalog.
`CODEX_HOME` selects Codex configuration; the shared user skill catalog remains
under the account's home. Do not copy credentials or change provider configuration
to make a skill available.

## Confirm access

`basaltw agent manifest /absolute/project --json` reports known local Basaltwater
entrypoints and whether they match this installation's source. This inventories
files; it cannot establish that the active harness enabled, advertised, or read
them. Check the session's skill list (`/skills` in interactive Codex), disabled
skill settings, and any missing-file errors. A crowded catalog can omit skills
from initial context; name the relevant skill and its absolute path when needed.
If guidance is stale or incomplete, the owner reruns the platform's setup from
updated Basaltwater source. Reopen the agent session if refreshed skills are
absent from its discovery list. Do not refresh a host as an incidental worker task.

Repository skills follow the worker's working directory. A managed worktree
contains tracked `.agents/skills` at its committed base, but does not inherit
uncommitted or ignored skill files from the primary checkout. Pass explicit
readable paths when needed; do not copy the parent's entire configuration or
assume that reading the parent's `AGENTS.md` applies the child's nested rules.

## Select the session's guidance

| Session | Guidance and tools |
| --- | --- |
| Debian terminal agent | Use installed VM/workspace skills and only configured terminal MCP tools. A running T3 service does not expose its preview to this session. |
| CachyOS terminal agent | Use the workstation, workspace, and native desktop skills. Desktop control requires the owner's actual session and grants; Debian VM commands do not apply. |
| T3 Code | Use the provider host/account's catalog. Use collaborative preview tools when exposed, following status/open and the preview-first fallback policy. Remote T3 Connect guidance lives on the remote provider host. |
| Ubuntu WSL, including Windows T3 connections | Use `basaltwater-wsl-workspace` and this shared skill inside the selected distribution. A native Windows provider cannot discover Ubuntu's user catalog. |
| Basaltwater panel task | Use the operations skill's unattended-task reference. The fresh `codex exec` process has the panel account's home/configuration and no interactive reply. Thread-scoped T3 tools are unavailable unless explicitly provided to that process. |
| Read-only worker | Read the instructions for the assigned checkout and use inspection operations through every tool. Checks that write files require an authorized isolated workspace. |

Availability of a skill does not create its tools or grant actions. Keep native
desktop, browser, publication, and privileged workflows within the assigned
task and session permissions. When a worker lacks a required tool, have it return
the blocked check and evidence so the parent can complete that part with its own
authorized capabilities.

## Pass the needed context explicitly

Codex custom agents inherit `skills.config` and MCP settings when their custom
configuration omits overrides. This does not prove a worker received the parent's
conversation, loaded skill bodies, browser attachment, or identical tools.
Check the actual spawn interface; use fresh context for bounded cheap tasks when
supported. Supply the relevant project instructions and skill paths in the worker
assignment, especially when changing models without a history fork:

```text
Task: Review only docs/guides for incorrect commands; report findings without edits.
Working directory: /absolute/assigned/checkout
Project instructions: /absolute/assigned/checkout/AGENTS.md; follow applicable nested instructions.
Relevant skill: basaltwater-subagents at /absolute/account/.agents/skills/basaltwater-subagents/SKILL.md.
Read its session-context reference; use the platform's installed workspace skill if needed.
Context: unattended inspection; no operator reply; finish within the parent's remaining runtime.
Tools: use only your exposed inspection tools; report any required browser check you cannot perform.
Return: findings, inspected paths, validation, unresolved checks, and actual model/effort/service settings.
```

Replace these paths with the actual account/catalog and include only skills the
task needs. A worker doing file categorization usually needs project rules and
the categories, not host deployment or desktop instructions. A deployment worker
needs the locally installed deployment skill and explicit task authorization.
Honor disabled skills rather than using a direct path to bypass their selection.

For unattended delegation, pass the task's sandbox, read-only/network/temp-write
limits, stopping conditions, and remaining wall time to every worker. The parent
must join workers before returning; cancellation of one CLI process does not
guarantee cancellation of externally owned agents or MCP actions. Keep the task
with one agent when its limits cannot be enforced across workers.

The [official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills)
describes local discovery; [custom subagent settings](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describe inherited configuration. Check current provider behavior when upgrading;
Basaltwater does not supply a separate subagent harness.
