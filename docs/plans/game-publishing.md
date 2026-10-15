# Game publishing status

Status: VM-local publishing controls and upload workflows are available through
the panel and CLI. The [operator guide](../workstations/game-publishing.md)
defines the current contract and limits. Provider account behavior, real uploads,
and destination rendering remain unqualified on live test destinations.

## Current boundary

| Area | Current status | Qualification needed |
| --- | --- | --- |
| Tools and authentication | Butler and SteamCMD are managed per owner; login stays VM-local and secrets do not pass through the controller. | Verify supported CLI versions, remote login prompts, session reuse, and local logout on disposable accounts. |
| Builds and jobs | Completed artifacts can be reviewed and uploaded; scheduled jobs retain durable dispatch and reconciliation state. | Exercise receipts, interruption, duplicate prevention, cancellation, and recovery against private test destinations. |
| Promotion | itch.io channel uploads and non-default Steam beta promotion have explicit controls; Steam default release remains manual. | Verify provider readback, stale-target refusal, and manual Steamworks handoff with an authorized test app. |
| Public writing | Drafts and translations require exact human review; editor handoffs are supported. | Verify provider rendering and language round trips; automatic post adapters remain unavailable until separately qualified. |

## Safety boundaries

- Build uploads do not authorize public writing or default-branch release.
- Publishing jobs use an existing built artifact and saved destination; they do
  not run repository build hooks or arbitrary shell commands.
- A beta channel is not assumed private. An itch.io channel upload can affect
  players immediately.
- Unknown provider outcomes require inspection or explicit reconciliation;
  they are not retried as if nothing happened.
- Credentials and provider sessions remain on the VM. A same-user agent still
  has the authority of that account and is not sandboxed by this workflow.

Use [release communications](game-release-communications.md) for the current
review, translation, promotion, and human-publication boundaries. Keep live
qualification on operator-selected private projects and test apps.
