# T3 client features by release

[Topic index](t3-workflows.md)

## Shared 0.1.6 client workflows

These workflows apply to compatible web/desktop clients on Debian, CachyOS,
native Windows, and Ubuntu WSL connections. They use the selected environment's
repository, provider, credentials, and installed skills.

- Start a side question with `/side` or `/btw`, or **Side chat** in the right
  panel's **+** menu. It inherits context and shares the thread's folder while
  the main thread continues. In 0.1.8 it inherits the parent's composer model,
  effort and permissions from the first reply; later parent changes do not
  change an existing side chat. Inspect its own composer. Use bound worktrees for
  concurrent edits. **Bring back** includes the discussion with the next
  main-thread message while that thread is idle. It does not create a delegated
  task receipt or merge Git changes.
- Pin projects in pickers or **Settings → Projects**. Archive/unarchive in
  that settings list; archive hides a project from pickers and retains its
  files, settings, and threads. All checkouts must be connected for a project
  spanning machines. Inspect running work and schedules separately.
- Choose **Commits** in the Diff pane source picker for the newest 100 commits
  in the branch comparison. Merge commit diffs use the first parent.
- Builds enabling `T3CODE_FEATURE_SECRETS` at build time expose **Settings →
  Secrets** for 1Password-backed provider variables and source-control
  credentials. The environment running the provider needs a working `op` CLI
  and account access. A Windows installation/login does not establish Ubuntu
  access. Setup does not enroll 1Password; setting a service environment
  variable does not enable the compiled UI. Continue using `request_secret`
  for private agent input with one-use references or file delivery.

Contracts: [side chats](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md#side-chats),
[project organization](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/project-settings.md#pin-and-archive-projects),
[commit review](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/source-control.md),
and [1Password resolution](https://github.com/pingdotgg/t3code/blob/v0.1.6/packages/secret-provider-onepassword/src/server/driver.ts).

## Shared 0.1.7 client workflows

- **Child approvals:** the parent conversation shows the child's name and
  requested action. The owner can approve/reject there; this does not give the
  parent agent additional approval authority. For a linked child, check the
  owning environment. Questions and permission approvals remain separate flows.
- **Thread links:** use **Copy → Thread**, or **Copy thread** on mobile, to
  share a link that opens the conversation. A copied reference can prefer its
  PR. Recipients still need access to the machine; copying a link does not
  publish the thread. Preserve tool-returned `link` values verbatim in agent replies.
- **Usage filters:** `usageModelFilter` in the server's `userdata/settings.json`
  supports `include`/`exclude` lists of `{provider, model}` selectors; omit
  `provider` to match a model across providers. Use the IDs Usage shows before
  **Map to**: filtering precedes that mapping, and each environment has its own
  filter. Invalid filters are ignored and can disappear on the next settings
  save. Change these settings only when requested. Filtered API-equivalent cost
  estimates are not a subscription bill or complete accounting of all work.
- **Model history:** conversation markers show model or reasoning-effort changes.
  Use them to locate a change when reviewing behavior; read the current thread
  configuration before reusing settings for another task or schedule.
- **Tagged-thread hiding:** the server settings `hiddenThreadTags` and
  `hideTaggedThreads` can hide matching threads from lists for recording or
  presentation. Hiding does not stop work, revoke access, or redact conversation
  contents. Inspect the actual recording surface for private material.

One-time follow-ups use T3's new `once` schedule; the subagent skill's
[scheduling reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md)
owns the schema, timezones, and dispatch checks. Machine-wide managed policy can
cap modes or disable providers, remote access, and updates; inspect installed
versus loaded policy with `t3 policy show` on the environment host. Windows
policy does not configure a server inside WSL and disables desktop-managed WSL
connections. Read the [platform boundaries](t3-platforms.md) for that restriction,
desktop CLI installation, and Debian's default systemd-path requirement.
The T3 operations skill's
[update reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-t3code/references/updates.md#release-tracks-and-archive-layout)
owns policy diagnostics. Setup preserves the owner's feature settings.

Contracts: [approvals](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/permission-modes.md),
[thread links](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/prompt-links.md#share-a-thread),
[usage filters](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/usage.md#show-only-some-models),
[model history](https://github.com/pingdotgg/t3code/blob/v0.1.7/apps/web/src/components/chat/V2LifecycleRow.tsx),
and [tag settings](https://github.com/pingdotgg/t3code/blob/v0.1.7/packages/contracts/src/settings.ts).

## Shared 0.1.8 client workflows

- **Context reset:** send `/clear` alone to start a fresh provider session in
  the same thread. Earlier conversation and diffs stay visible, but are not
  supplied to the new session. Save a durable checkpoint before a deliberate
  reset. Compaction retains a summary; **Restart agent session** reloads the
  session's guidance and preserves conversation context. Do not substitute
  `/clear` for a skill reload or assume it erases retained history.
- **Private secrets:** call `request_secret` for the private card. Use its
  one-use `secretRef` with a supported T3 tool, or `delivery: "file"` for a
  command. Read that owner-only file inside the command without printing the
  value; it expires after about 24 hours. A configured secret provider can
  answer the card with a reference. The request card works even when the
  preview **Settings → Secrets** page is absent. Headless 1Password access
  needs service-account or Connect authentication on the server, not an
  unattended desktop approval. Private sudo entry does not expand the task's
  authority or replace Basaltwater's privilege approval route.
- **Optional text generators:** Settings can select a direct OpenAI, Anthropic,
  OpenRouter or compatible API route for titles, branch names, commit/PR text
  and transcript cleanup. It needs separate API credentials/billing and can
  send the staged diff to that endpoint. A coding subscription does not cover
  those requests automatically. Preserve the existing agent route unless the
  owner chooses a generator; PR review guides still use a coding agent.
- **Thread state:** default automatic settlement includes seven days of
  inactivity. Inspect **Settings → Threads**, per-thread **Auto-settle
  behavior**, project settle actions and worktree retention before relying on
  a thread staying active. Pending work/input blocks automatic settlement;
  snoozing an existing question leaves it unanswered. Pinning does not prevent
  settlement, and an open PR protects against inactivity only when configured.

Fresh-session schedules use the [scheduling reference](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-subagents/references/t3-scheduling.md).
Preserve explicit model selections as discovery refreshes; new catalog entries
do not override the shared Sol/Opus cost guidance.

Contracts: [composer](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/composer.md),
[secrets](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/secrets.md),
[text generation](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/text-generation.md),
and [thread state](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/thread-sidebar.md).

## Shared 0.1.9 client workflows

- **Attachment sending:** ordinary messages can be submitted while files upload;
  delivery waits for preparation to finish. A pending send is not a completed
  provider request. Retry/remove failed uploads and reattach interrupted files
  after a reload. Question answers and approval redirects still require ready
  attachments. The tagged composer manual retains older wording here; the
  release and composer implementation describe the changed ordinary-send path.
- **Claude approvals:** an MCP app can be allowed for the current Claude
  session. Inspect the app and requested action before choosing that broader
  grant. A session grant does not change the task's authorization or managed
  policy. Do not assume it survives a fresh provider session.
- **OpenCode goals:** OpenCode **2.2.5+** supports `/goal`, mirrors native goal
  state across clients, and projects native todo updates into checklists.
  `/goal` alone reads status; `clear` removes it and `pause`/`resume` control
  execution. Stopping its goal pauses it. Use only for requested goal work;
  this does not migrate Basaltwater's managed OpenCode 1.x installation or
  schedule an independent task. The general 2.0.18 provider recommendation
  does not establish native-goal support.
- **Review and accessibility:** PR links preserve their comment, diff-line,
  checks or commit destination. Keep those anchors when sharing a specific
  review location. A link does not qualify checks/reviews for the latest head.
  GitLab merged-MR revert is available for an authorized revert operation.
  Background-command details expose status/output and subagent navigation;
  inspect the final result before claiming completion. Screen-reader turn
  announcements and a bindable regenerate-title shortcut are client features;
  preserve the owner's preferences.
- **Connect agent sign-in:** the hosted approval page can use the account
  linked to the environment without a code. Follow the operations skill's
  [outside-agent route](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-t3code/references/operations.md#outside-agents)
  for grants, reachability, local fallback and revocation.

Contracts: [release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.9),
[composer implementation](https://github.com/pingdotgg/t3code/blob/v0.1.9/apps/web/src/components/chat/ChatComposer.tsx),
[goals](https://github.com/pingdotgg/t3code/blob/v0.1.9/docs/user/composer.md#goals),
and [Claude session approvals](https://github.com/pingdotgg/t3code/pull/33707).

## Shared 0.1.10 client workflows

Use upstream's [messages, goals and recovery manual](https://github.com/pingdotgg/t3code/blob/v0.1.10/docs/user/composer.md)
for response replies, native goals and Resume. Codex, Claude and OpenCode can
keep working toward an explicitly requested goal; OpenCode requires 2.2.5+.
Codex/OpenCode support goal pause/resume; stopping Claude ends the turn while
leaving its goal set for the next message. Inspect actual goal state rather
than assuming all providers stop alike. Stopped Claude, Codex, OpenCode and
Cursor threads can use native continuation without adding a new prompt.
Do not wrap a T3-owned goal in a second Basaltwater continuation schedule.
Independent tasks with lifetime limits and receipts remain a separate route.

For Connect device approval, Windows services, cleanup and compatibility,
read the [operator qualification record](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/t3-releases/0.1.10.md).
T3 owns keep-awake behavior during work and client theme/language preferences;
setup preserves these choices. Use upstream manuals for ordinary UI controls
instead of reproducing their release history in project instructions.
