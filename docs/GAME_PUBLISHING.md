# Publishing games from a VM

Open **Publishing** in the existing HTTPS web panel to manage VM-local Butler
and SteamCMD sessions, prepare and upload builds, schedule unattended uploads,
and review release writing. Use `basaltw publish` on that VM for automation and
recovery. Steam default/public releases and rollbacks always happen manually
on Steamworks. Steam announcements and itch.io devlogs use reviewed text exports
and tracked human editor handoffs.

The implementation has mocked process/API and panel tests. Live provider login,
upload, beta API response schemas, and provider text rendering still need
qualification on operator-selected test destinations. No production game was
uploaded as part of development.

Coding agents can discover installed providers and workflow guidance with
`basaltw agent manifest --json`. This [read-only inventory](AGENT_ENVIRONMENT.md)
does not inspect credentials or establish provider readiness.

## Install and authenticate

On the controller, add publishing tools independently of any game engine:

```bash
basaltw patch VM_HOST agent --publishing-tool butler --publishing-tool steamcmd
```

The repeatable flag accepts `butler` and `steamcmd` on Debian profiles with a
non-root setup user. SteamCMD requires x86_64; ARM64 setup reports it skipped.
The existing `--godot-bundle publishing` remains available. Both selections
share `auto-update-godot.timer` and provider locks; a busy account defers its
tool update. A shared Butler update locks every registered publishing owner.
CachyOS keeps its existing package management; this management workflow has not
been qualified there.

Enable the [HTTPS web panel](WEB_PANEL.md#install-and-sign-in), then select
**Publishing → Accounts → Sign in**. Refresh saved status to see the current
native prompt. Butler shows an itch.io authorization link and accepts its final
localhost callback URL in a private input. SteamCMD accepts the account name,
then password/Steam Guard challenges. Responses go directly to the native
process's input and are not saved. Unknown prompts require a VM terminal;
sessions expire after 15 minutes. HTTP and root-profile panels show information
without authentication or mutation forms.

SSH fallback, as the same non-root VM account:

```bash
basaltw publish auth login butler
basaltw publish auth login steamcmd --username BUILD_ACCOUNT
basaltw publish status --json
```

Authentication commands require an interactive terminal. There are no password
or token command-line arguments. An existing credential file is shown as
present-unverified. A zero exit from native login must also leave the expected
local session file, and is still labelled present-unverified; it is not proof
that the provider accepted the login or a fresh account check.
Actual uploads revalidate the session and never wait for interactive input.
Use a provider-side build account with the needed application permissions.

**Remove local session** pauses that provider's upload jobs and erases known
local native credentials. Steam removal also removes the optional beta API key.
It refuses a busy provider; cancel or finish its work first. This does not revoke
remote tokens, website sessions, or credentials in other native installations.
Revoke access on [itch.io](https://itch.io/user/settings/api-keys) or
[Steamworks](https://partner.steamgames.com/) when retiring a VM.

## Configure a project and completed builds

Language declarations live in the project's `basaltwater.json`:

```json
{
  "version": 1,
  "components": [],
  "publishing": {"languages": {"source": "en", "supported": ["en", "es"]}}
}
```

Absent language metadata defaults to English only. Other valid tags are accepted;
English and Spanish have structural tests initially. The declaration does not
claim that the game itself is localized. Older Basaltwater installations must be
upgraded before reading this additive version-1 extension. Create a minimal
game manifest with `basaltw manifest init --kind publishing`. Edit language
settings in the repository; the panel reads them without rewriting the file.

Configure an explicit destination in **Projects**, or run these on the VM:

```bash
basaltw publish project game-linux /home/agent/repos/game butler owner/game:linux
basaltw publish project game-steam /home/agent/repos/game steamcmd APP_ID \
  --depot DEPOT_ID --username BUILD_ACCOUNT
```

Steam's first recipe maps one completed artifact to one depot. Additional depot
destinations create separate app builds; they do **not** compose a multi-depot
release. Projects needing an atomic multi-depot build should retain their native
SteamPipe workflow until a coordinated recipe is supported. No repository VDF,
shell hook, public version label, or public description is accepted.

After the project's build and validation finish successfully, declare its
completed export. Keep the record outside the exported directory:

```bash
basaltw publish complete /home/agent/repos/game exports/linux internal-build-42
basaltw publish prepare game-linux
# Select the returned artifact ID:
basaltw publish upload ARTIFACT_ID --wait
```

The default record is `.basaltwater/publishing-complete.json`, configurable with
`project --record`. Its exact version-1 schema is:

```json
{"version": 1, "path": "exports/linux", "build_id": "internal-build-42", "digest": "SHA256_OF_FILE_INVENTORY"}
```

Use `complete` to calculate the digest; it is not the hash of a ZIP or directory
name. `build_id` is an internal operational identifier, not a public version.
Build producers must finish the directory before writing the record. Changed
files invalidate it. Preparation does not run repository code or contact a
provider, and uploads use a retained private snapshot rather than live output.

Each build completion record pins a directory, internal build identifier and
content digest. Snapshots reject links, special files, hard links and credential
paths. Native upload commands use fixed arguments and generated Steam VDFs
without `SetLive`; Steam default release/rollback always happen on Steamworks.
An interrupted dispatch is held for reconciliation rather than repeated.

The same content at the same provider/channel/app/depot returns the existing
run across retries and restarts. Cancellation after dispatch can leave remote
effects and is recorded as unknown. Inspect the provider, then use the panel's
reconciliation control. **Not submitted** permits retry only after a human
establishes that result. A zero exit status without an identified receipt is
uploaded-unverified, not proof that players can download the build.
Steam upload receipts must identify the requested AppID and a nonzero BuildID;
a success line for another app does not verify this upload.

## Unattended uploads and promotion

```bash
basaltw publish schedule game-linux --interval 60
basaltw publish jobs --json
basaltw publish job JOB_ID pause
basaltw publish job JOB_ID resume
basaltw publish job JOB_ID edit --interval 30
basaltw publish job JOB_ID remove
basaltw publish runs --json
basaltw publish cancel RUN_ID
```

The running panel polls deterministically every 15 seconds, independently of
long agent prompts. Job intervals are 5–43,200 minutes. Each overdue job checks
the latest completed artifact once; missed intervals are not replayed. Three
consecutive preparation failures pause a job. Authentication failures pause
provider jobs, and ambiguous outcomes block automatic retries. Resume is
explicit and accepts the current project configuration, clears the failure count,
and schedules the next poll one full interval from resuming. Preparations rejected
before queueing are removed automatically, including builds with unreviewed
notes; failed polls do not consume the retained-build quota. Active, ambiguous
and release-linked artifacts remain protected. Pause stops future
dispatch and cancels that schedule's queued uploads; cancel running work separately.
Detached uploads survive a panel restart, but scheduling requires the panel
service to be running. External build runners can invoke `prepare`/`upload`;
`publish worker` processes the queue and does not become another schedule owner.

Each project can create one schedule at a time, including a paused schedule.
Repeated creation is rejected; use the existing job's controls instead. The panel
shows its identifier and links directly from the project to its schedule.
**Save interval** / `job edit --interval` accepts 5–43,200 minutes, keeps the
current enabled/paused state, destination authority and failures, and moves the
next poll to one full interval from saving. It cancels queued uploads for the
previous schedule revision. It does not resume a paused job or accept changed
project settings; use **Resume** deliberately for those actions.

**Remove schedule** / `job remove` stops polling and cancels only its queued
uploads. It keeps a removed job record, prior run history, provider receipts,
retained snapshots and successful/uncertain duplicate-detection records. Only
never-started queued dispatch reservations are released to permit safe retry.
Running or uncertain uploads remain tracked and need their cancellation/reconciliation
controls. Removed jobs cannot be edited or resumed; create a new schedule from
the project if needed. Pause, edits and removal invalidate a poll still preparing
a snapshot, so it cannot queue an upload after those controls return. Authentication
failure and local logout also cancel queued scheduled work without resurrecting
removed jobs. Existing duplicate schedules from an older version are preserved;
pause/remove extras explicitly.

For itch.io promotion, configure another channel of the same game and use
**Promote retained artifact to channel**, or `publish promote-itch ARTIFACT_ID
DESTINATION_PROJECT`. This re-uploads the exact retained bytes. Channel names
do not prove restricted visibility. Artifacts containing declared public writing
need destination-bound preparation/review instead of this shortcut.

For Steam, an uploaded numeric BuildID can create a **Steamworks release
handoff**, including rollback to a previously uploaded build. A human releases
that exact BuildID on the selected branch, then records it in the panel. Posts
linked to the release remain held until its completion is recorded.

Optional non-default beta promotion uses a separate publisher API key entered
through Accounts. It stays in a private VM file and is not needed for uploads.
**Observe and prepare beta promotion** checks app build history and current
branches, records the previous BuildID, and expires after five minutes.
**Promote** rechecks those observations, then validates current project settings,
writing reviews, timing and cancellation immediately before dispatch. It reads
the branch back afterward. Repeated submission of the same prepared release
returns its saved operation rather than dispatching again.
`public`, `default`, implicit targets, unknown schemas and default aliases fail
closed. CLI equivalents are `beta-prepare RUN_ID BRANCH` and `beta-promote
RELEASE_ID`. API schemas need live qualification; use the Steamworks handoff
if unsupported. The API has no conditional branch-update guarantee, so avoid
concurrent external promotions during that operation. Audience stays unknown.

## Write, translate, review and publish posts

Writing and supplied translations start as drafts. Each final destination and
language revision requires a human review in the authenticated panel. Editing
the source invalidates dependent translations. Scheduled posts use explicit UTC
instants and a reviewed lateness window. Steam announcements and itch.io devlogs
initially export reviewed text for a human editor handoff; they do not advertise
automatic remote publication. A recorded human receipt is labelled
operator-confirmed, not provider-verified.

1. Select **Prepare agent writing task** to draft source text or translate a
   selected source revision. This opens the existing Agents form for review;
   it does not start an agent automatically. Agents return unreviewed plain text.
2. Import the final title/body for one project and language. A translation names
   its current source revision; old destination/language configurations and
   translation chains are rejected. Select a release gate when the post claims
   an update is live. Optional timing must include `Z` or an explicit UTC offset.
3. A human reviews the exact destination and each language, side by side with
   the source. The decision defaults to changes requested. Approval binds the
   revision hash, project/language configuration, timing and release dependency.
4. Export reviewed text or begin the editor handoff. At a scheduled time the
   panel makes the handoff ready only inside the reviewed window (default 60
   minutes; configurable 1–1,440). Late handoffs are held for a new review.
5. Paste the text into the Steam announcement or itch.io project devlog editor.
   Check final formatting and localization there before publishing. Record the
   published URL and confirm it matches the reviewed revision. A changed text
   needs a new local revision; exports and editor links never mark it published.

CLI draft input is UTF-8 JSON with `language`, `title`, `body` and optional
`source`, `replaces`, `release`, `publish_at` (Unix UTC seconds), `late_minutes`:

```bash
basaltw publish draft game-linux draft.json
basaltw publish export DRAFT_ID
# Ready the reviewed handoff, respecting its timing and release gate:
basaltw publish export DRAFT_ID --handoff
```

Imported approval fields are rejected. There is no CLI review/approval command.
Agents must not read credentials, alter the approval database, or use browser
tools to click human review/confirmation controls. Source replacement invalidates
dependent translations. English and Spanish are tested structurally in both
directions; other valid tags remain configurable without linguistic or provider
rendering claims. There is no automatic post submission, remote scheduling,
external edit detection, rich-asset support or Steam CSV conversion yet.

Files named `changelog*`, `release-notes*`, or `patch-notes*` in an artifact require
an approved body matching their bytes exactly. Preparation can reuse an existing
matching review for the same project/destination; schedules cannot approve new
text. Public itch.io uploads and automatic Steam beta promotions also respect
that review's timing window and release gate. Steam uploads may stage approved
notes before their public release; beta promotion rechecks approval immediately
before dispatch, and the final default release review stays manual on Steamworks.
Use **Attach reviewed release notes**, or `publish artifact-text ARTIFACT_ID
PATH DRAFT_ID`, to declare another public text file and attach its review.
Projects still own human review of other player-facing game content; filename
detection cannot prove a binary or image contains no writing.

## State, limits and recovery

| Item | Location or limit |
| --- | --- |
| Operational records, approval authority and dispatch ledger | `~/.local/share/basaltwater/publishing/state.sqlite3`; private owner-only directory/files |
| Prepared snapshots | `~/.local/share/basaltwater/publishing/artifacts/`; at most 100 retained artifacts |
| Native itch.io credentials | `~/.config/itch/butler_creds` |
| Managed SteamCMD installation and native state | `~/.local/share/basaltwater/steamcmd/` |
| Optional Steam beta publisher key | `~/.local/share/basaltwater/publishing/steam-api.key` |
| Per-artifact limits | 10,000 regular files, 30 GiB, relative paths up to 1,024 UTF-8 bytes |
| Native upload limits | Six hours, 16 MiB total stream, 64 KiB parser tail; raw output is discarded |
| Writing | Title 200 characters, body 64 KiB UTF-8; panel agent prompts 4,000 bytes |
| Publishing form transport | 256 KiB to accommodate percent-encoded UTF-8 drafts; text limits remain unchanged |
| Panel/CLI status | Up to 200 selected records per kind, prioritizing enabled/paused jobs and actionable runs/drafts/releases; includes referenced projects and translation sources; panel displays 20 artifacts, 40 runs and 40 drafts |

Remove unused snapshots through the panel or `publish remove-artifact ARTIFACT_ID`.
Active, ambiguous and release-linked artifacts cannot be removed. History,
reviews and the dispatch ledger remain durable; automatic history/receipt
pruning is not implemented. Provision space for retained copies and native
Steam build caches. No upload is dispatched when preparation runs out of space.
The manager never purges active builds to meet a quota.

Linked, foreign-owned or exposed state fails closed; repair only the identified
VM files as their owner after inspecting them. Do not delete the dispatch ledger
to clear a failed run. Reconcile unknown results through the panel first.
Maintenance, login and uploads share the same provider lock. Direct native CLI
invocations outside `publish` require operator coordination.

Database corruption, lock and write failures report unavailable state without
recreating the ledger or exposing database error text. Stop publishing work
before recovery and preserve the failing database and retained snapshots. Restore
a consistent private backup if available, then reconcile any operations whose
provider effects occurred after that backup before resuming schedules. Replacing
the database with empty state would lose the authority and duplicate-detection
records needed for safe recovery.

Accounts and records stay on the panel user's VM, outside the controller and
repository. Same-user processes and root can access native sessions and control
state: human review is a managed workflow boundary, not isolation from
unrestricted code running as that account. Separate accounts/VMs and provider
permissions define a stronger trust boundary. VM backups also contain credentials;
rebuilt VMs authenticate again rather than cloning them through this feature.

See the [publishing plan](plans/GAME_PUBLISHING.md) and
[release communications plan](plans/GAME_RELEASE_COMMUNICATIONS.md) for follow-on
provider qualification, rich assets, Steam locale CSV imports, website/blog,
social adapters, and reviewed campaigns.
