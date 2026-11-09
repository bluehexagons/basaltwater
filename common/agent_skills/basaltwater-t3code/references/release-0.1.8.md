# T3 Code 0.1.8 integration

Reviewed **2026-11-09** against [stable 0.1.8](https://github.com/pingdotgg/t3code/releases/tag/v0.1.8),
commit `bf469bb19669671d0df2efda74ed73867a7137c0`.
This replaces the earlier nightly preparation review.

## Compatibility and update route

The release retains service-state protocol **3**, `activeVersion`, the standalone
archive layout and `__service-launcher`. Existing Basaltwater runtime selection,
native-PTY probe, log wrapper and administrative pairing remain compatible;
no adapter change is required. The launcher loads fewer dependencies; that is
not evidence of a whole-VM memory reduction. Sources:
[protocol](https://github.com/pingdotgg/t3code/blob/v0.1.8/apps/server/src/cloud/serviceProtocol.ts)
and [launcher](https://github.com/pingdotgg/t3code/blob/v0.1.8/apps/server/src/cloud/bootService.ts).

Follow [updates](updates.md) for an authorized deployment. Basaltwater setup
already resolves the latest stable release; it needs no new release pin. Check
desktop package availability separately and preserve database downgrade blocks.
The recommended provider versions include Codex **0.159+** and Claude Code
**2.1.301+**; Claude **2.1.280+** remains supported. Refresh provider discovery
before selecting a model. A new catalog entry, including Fable 5.2, does not
change the Sol/Opus cost guidance or authorize fast service/Ultracode. Source:
[provider manifest](https://github.com/pingdotgg/t3code/blob/v0.1.8/apps/server/src/provider/model-manifest.json).

## Workflow routes

- [Scheduling](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
  covers `freshSession`, binding changes, migration and self-contained prompts.
  The MCP input differs from the persisted `threadMode` contract.
- [Client workflows](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-project-development/references/t3-workflows.md#shared-018-client-workflows)
  cover `/clear`, inherited side-chat permissions, private secrets, optional
  API text generators and settlement. `/clear` discards earlier provider context;
  Restart agent session remains the route for reloading guidance.
- [Operations](operations.md) covers outside-agent grants and provider update
  ownership. [Maintenance](updates.md#018-service-environment-and-maintenance)
  covers `service.env` precedence and offline database reclamation.

## Qualification evidence and limits

On Debian 13 x64, the GitHub-digest-verified 0.1.8 archive passed version and
embedded native-PTY probes, disposable fresh-home HTTP startup through the
managed output filter, administrative pairing with all 16 scopes, bootstrap
session revocation, signal forwarding/shutdown, and SQLite integrity.
A disposable 0.1.7-to-0.1.8 upgrade passed the same checks and retained a project
registered through the host CLI. SQL migrations advanced from **79 to 80**.
No live service or user database was changed.

These component checks do not qualify systemd installation/update/reboot,
authenticated provider turns or terminals, native clients, Connect, or the new
UI/scheduling/secret workflows. Complete the
[live qualification cases](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/t3-code.md#stable-feature-qualification)
on disposable systems before claiming those paths. Preserve a private recovery
copy and use synthetic secrets. Optional API generators, secret accounts,
linked environments, and cloud sandboxes remain explicit choices.
