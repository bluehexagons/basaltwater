# Git, providers, and readiness

[Documentation index](t3-code.md)

## Git and provider behavior

### Current provider and thread workflows

Managed Debian headless setup exposes `~/.local/bin/t3`, linked to the stable
wrapper that follows the service's selected runtime after updates. Setup
preserves an existing command at that path; the managed wrapper is also
available as `~/.local/bin/basaltwater-t3code-pairing-provider`. Use
`t3 --version` to check which runtime the shell invokes.

Stable 0.1.2 adds `t3 thread` commands for scripts on the environment host.
`t3 thread list --json` and `t3 thread show THREAD_ID` inspect existing work;
`start` creates an ordinary thread and `send` queues a message without
interrupting a running turn. `--wait` exits 2 when an answer is needed in the
app. Use native `delegate_task` for child work inside a T3 session and keep
top-level thread creation within the user's requested scope. See the
[upstream host CLI guide](https://github.com/pingdotgg/t3code/blob/v0.1.2/docs/user/outside-agents.md#scripts-on-the-host).

Managed setup refreshes reload selected provider configuration and skills. On
CachyOS desktop, a detached user worker gracefully sends SIGTERM to a running
same-user `t3code-bin` app, waits for exit, then relaunches the packaged app
with its original environment and persistent arguments, excluding consumed
`t3code:` startup links so refresh does not repeat pairing or create a thread.
It does not force-kill; if the app
was stopped, it starts when a graphical session is available; otherwise KDE
login autostart remains available. Check
the worker's printed `journalctl --user` unit if it fails; queueing is not proof
of readiness. Verify an authenticated provider turn and terminal.

Saved T3 threads can continue on their next message. Automatic continuation of
an interrupted active turn depends on the existing **Continue threads after
server update** setting. Persistent queued turns still need user Resume, and
approvals may not survive. Standalone provider CLIs cannot be safely restarted
by setup: finish and exit them, then resume manually. For a single thread or
context-only override, use native **Restart agent session** or a launch override
without restarting the shared T3 service.

Use the **Update all** control at the bottom of T3's sidebar for a requested
bulk update across connected environments. It updates outdated providers,
servers, and the desktop app on their selected release tracks. Stable 0.1.5's
desktop **Settings → About → Update track** separately switches connected
installations between Stable and Nightly, with per-machine skips and database
compatibility checks. A requested track can remain recorded after an incomplete
switch; inspect the running version too. Preserve compatibility blocks rather
than forcing a downgrade or changing migration records. Basaltwater setup
continues to target stable. See the
[track-switch guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/updating.md#switch-between-stable-and-nightly).
For one provider on one VM, use that provider's
individual control in **Settings → Providers** or
`basaltw agent update HOST USER --tool TOOL`. After a T3 provider update, run
Basaltwater's doctor and record readiness; its updater records and rollback
backups are created only by its own update flow. See the upstream
[provider update guide](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/updating.md#update-providers)
and [release notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0)
for the global update action.

**No project** threads use individual folders under `~/.t3/scratch` on the
managed Debian service. T3's enabled native retention can remove projectless
folders; without a Git repository their files may have no recovery. The host doctor
includes their size as `agent_storage.size_bytes.t3_scratch`; automatic cache
maintenance preserves them. CachyOS web installations use the `scratch` folder
under their isolated T3 data directory. Stable 0.1.5 adds **Turn into project**
to a projectless thread's menu. Finish or stop all threads using that folder
first. Promotion registers the existing folder in place and moves its threads
and their scheduled tasks into the project; it does not move files or initialize
Git. Use **Initialize Git** before branch/worktree controls. A promoted folder
remains user project data even though it is still under `scratch`. T3 hides
projectless threads when its data directory is inside a Git checkout. See the
[projectless thread guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/thread-sidebar.md#start-without-a-project).

Follow the active T3 session's PR-linking and watch instructions, including for
PRs created through `gh` and each layer of a stack.

### Native mobile device workflows

T3 also offers a Device panel and `device_*` tools for native mobile testing.
Basaltwater does not provision their SDKs, emulators, or simulator hosts and
does not enable agent device access during setup. Existing `--device-pairing`
enrolls T3 clients; it does not grant simulator control. See the
[mobile support plan](../plans/mobile-agent-support.md) and the
[upstream device workflow](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/user/devices.md)
for ownership and prerequisites. The new Simlock pool integration is an opt-in
alpha; Basaltwater does not install its gateway/workers, create leases, or
provision agent tokens. Returning a lease wipes its device. Responsive browser
presets remain browser coverage rather than native-device verification.

### Server-side Git checks

T3 Code runs as the target user. Git identity, GitHub CLI credentials, provider
credentials, repositories, and the workspace therefore stay in that user's
home and configured workspace.

Useful checks:

```bash
basaltw agent doctor --capability t3code --capability host
gh auth status
git config --global --get user.name
git config --global --get user.email
git config --global --get init.defaultBranch
basaltw agent support-bundle
```

Basaltwater configures `main` as the default for newly initialized repositories
unless the target user already selected another global default. This matters
for T3's branch controls: an unborn repository has only a symbolic branch name
and no branch ref until its first commit. To correct an existing unborn
repository initialized as `master`, run `git branch -m main`. Using
`git branch main` invokes branch creation instead and fails because there is no
commit to reference. Additional branches can be created or selected normally
after the initial commit.

The doctor's `healthy` result covers T3 service readiness, not successful
provider requests. Missing Git author identity, GitHub authentication, or a
Git credential helper is reported separately as an integration warning; an
installed but unselected `gh` does not make T3 unhealthy. JSON retains these
observations in `checks` and identifies the mandatory service checks in
`required_checks`. Use `agent doctor --tool gh` to check GitHub credentials
directly. The doctor does not send a model prompt or establish that a previous
provider timeout was resolved by running diagnostics.

### Provider status timeouts

`Timed out while checking Codex app-server provider status.` means the T3
provider probe did not complete; it does not establish that credentials are
invalid. After setup finishes and the host is idle, refresh provider status in
**Settings → Providers** and retry in T3. A successful Codex CLI request does
not validate T3's separate app-server probe. If it continues to time out,
record T3 and Codex versions, collect the doctor result, and inspect a redacted
service-log excerpt. Host contention is one possible trigger, not a diagnosis.
Do not replace credentials, delete T3 state, or restart active sessions solely
because a provider status probe timed out.

### Doctor checks and repairs

The doctor validates the upstream service-state protocol and selected immutable
runtime, required native terminal module, active and boot-enabled user service,
HTTP 200 endpoint, pairing helper, Git identity, and managed agent skill. Its
local endpoint checks support IPv4 and IPv6 loopback binds; wildcard listeners
are checked through the corresponding loopback address. HTTP errors do not count
as successful startup checks. Add `--fix` to rebuild an incomplete npm-backed
native runtime, repair GitHub's credential helper, enable the service for future
boots, or restart an inactive user service. The separate
host capability reports memory, swap, filesystem and agent-storage headroom,
T3 cgroup usage, recurring maintenance state, and pending reboots. Capacity
warnings do not make an otherwise healthy service fail; critical disk pressure
and recorded maintenance failures do.

Swap occupancy alone is not a pressure warning: idle swapped pages can remain
while plenty of RAM is available. When swap is configured, the host doctor
samples Linux swap-in/out counters for one second and includes the page deltas
and sample duration in `memory.swap_activity`. It warns about observed swap
I/O, unavailable activity measurements when swap is in use, and available RAM
below 512 MiB (including zero). An idle sample is only a point-in-time result;
repeat the check under normal workload before deciding to increase VM memory.
The doctor does not clear swap or change kernel tuning.

## Related documentation

- [Device pairing](../services/device-pairing.md)
- [Agent browser automation](browser-automation.md)
- [Client CA trust](../services/client-ca-trust.md)
- [Managed agent workflow skills](agent-skills.md)
- [Agentic coding security](agent-security.md)
- [Command-line reference](../reference/command-line.md)
- [Workstation and agent profiles](../workstations/workstations.md)
