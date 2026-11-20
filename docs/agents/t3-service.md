# Service operation and updates

[Documentation index](t3-code.md)

## Service and update model

Basaltwater uses T3 Code's supported per-user background service. Upstream owns
the launcher, immutable version directories, service state, updates, and
rollback. Basaltwater adds a systemd drop-in for the configured workspace,
host, port, PATH, and GitHub CLI environment. The drop-in runs T3's current
upstream `ExecStart` through a small output filter, resolving that command from
the upstream unit on each start so launcher updates do not require a pinned
Basaltwater command. The filter decodes systemd's literal `%%` and `$$` markers
before executing that command, preserving percent signs and dollar signs in
paths and arguments. It removes terminal color codes and hyperlink metadata,
then redacts credential fields, complete authorization/cookie values, and
terminal QR rows from both output streams. It forwards the remaining
diagnostics to T3's normal service log. A pre-start step also sanitizes an
existing `boot-service.log` and its numbered rotations after the previous
service process has stopped.

The service unit is:

```text
~/.config/systemd/user/t3code.service
```

Its Basaltwater settings are:

```text
~/.config/systemd/user/t3code.service.d/basaltwater.conf
```

User lingering is enabled so the service starts at boot without an interactive
login. Inspect it as the target user:

```bash
systemctl --user status t3code.service
journalctl --user -u t3code.service -n 100 --no-pager
tail -n 100 ~/.t3/userdata/logs/boot-service.log
```

The upstream launcher writes application startup failures, including native
module load errors, to `~/.t3/userdata/logs/boot-service.log`. systemd's journal
primarily records the launcher lifecycle. The managed filter removes startup
tokens, pairing URLs, and their QR image before they reach persistent logs.
One-time pairing commands intentionally return a fresh URL to the authenticated
caller; do not copy that command output into shared logs or diagnostics.

Stable `0.1.0` splits administrative access into granular permissions and
reserves `review:write` for internal use. Basaltwater's pairing helper validates
the temporary CLI session's complete administrative grant and delegates the
matching permission set, while retaining the older grant for pre-V2 servers.
Incomplete grants are rejected and their temporary session is revoked. This
preserves provider management, settings, previews, filesystem access, and server
updates for newly paired clients. If an existing client reports legacy
permissions after upgrading, enroll it again through the protected pairing
entrypoint. See the
[upstream permission contract](https://github.com/pingdotgg/t3code/blob/v0.1.11/packages/contracts/src/auth.ts).
In 0.1.11 the standard client grant covers existing Connect preferences and
unlinking. On managed Debian, use the host-side workflow for initial linking
and relay configuration. [Connect management](../services/device-pairing.md#t3-connect-management)

The helper sends its temporary administrative bearer only to an explicit
loopback address or `localhost`, with proxies and redirects disabled. Remote
and wildcard server addresses are rejected before a session is issued. The
client-facing base URL remains independently configurable, including IPv6;
it does not determine where the helper sends that bearer.

T3 also records non-fatal provider diagnostics. It can health-check optional
agent CLIs even when setup deliberately omitted them, so a missing-Claude or
similar warning is not a failed configured capability; compare it with the
doctor inventory's `required` fields. T3 polls pull-request status for open Git
repositories as well. An intentional local-only repository with no remote can
therefore log `SourceControlProviderError` with `provider: unknown` while local
Git remains healthy. Do not add a remote or rename a branch solely to silence
that PR-only warning. Repair the remote only when the repository is actually
intended to use a supported hosting provider.

T3's trace and provider-event logs are high-volume. Managed user-cache
maintenance preserves current files while pruning numbered rotations older
than 14 days or beyond a combined 256 MiB. The host doctor inventories the
whole T3 log tree and warns beyond 512 MiB. Use that warning and the maintenance
result to identify pressure; do not manually remove current files from a running
service. A preview wait that intentionally times out can also appear as an
application error in the log and is not, by itself, a readiness failure.

Service readiness does not exercise the connected desktop client's preview
presentation. With tools that explicitly support desktop automation, one
recognizable stale state returns successful background navigation or DOM results
while `preview_open` produces no visible sidebar or
floating surface, status remains `visible: false`, and snapshots fail. Do not
open replacement tabs or diagnose the application and network from that state.
Restarting only the desktop client may retain the VM-side tab registry. If the
user accepts interruption of all active T3 sessions, the bounded recovery is:

```bash
systemctl --user restart t3code.service
```

Reconnect the client and retry one status/open cycle. Prefer managed Playwright
when session policy permits fallback, or continue non-browser checks. Restart the service
before considering a whole-VM reboot so VM-side T3 state is isolated.
Setup queues a final service reload so refreshed provider configuration and
skills take effect, including when runtime and unit versions are unchanged.
It first waits for any upstream update's startup/migration to become ready;
the completion reload follows the remaining setup work.
Internal non-refresh service checks keep a healthy current service. Desktop and
standalone provider lifecycle behavior differs by profile; see the corresponding
setup guide.

Basaltwater targets stable T3 Code releases. When T3 Code is selected, every
setup run checks npm's `t3@latest` stable channel for a newer release.
A healthy service receives a final reload after successful setup, even when
runtime and managed configuration are unchanged; internal non-refresh service
checks reuse a healthy session. The T3 client can also offer an explicit
**Update server** action for this background service; prefer that action after
active agent work and terminal commands finish. Keep the client open while the
launcher downloads, installs, restarts, and reconnects. For a host-side update,
use the native updater as the target account. Replace `CLIENT_VERSION` with
the exact stable version required by the client; omit the version only when
both sides should use the latest stable release. Check `t3 --version` first;
if another installation owns that command, use
`~/.local/bin/basaltwater-t3code-pairing-provider` instead. Stable `0.1.0`
introduces orchestration V2 and requires a compatible client; a `0.0.45`
client cannot continue against that server. Read
[the migration procedure](#orchestration-v2-thread-migration) before the first
V2 upgrade and arrange the interruption and private recovery copy.

```bash
t3 update --channel stable CLIENT_VERSION
basaltw agent doctor --capability t3code --wait-for-t3-startup
```

The native updater asks before restarting in an interactive terminal. Arrange
the interruption before accepting; without interactive input it stages the
update unless `--yes` is supplied. If restart was deferred, run
`t3 service restart` at the arranged time, then check readiness. A staged
version is not necessarily running, and an older launcher can remain down
after a crash or reboot until that restart. In 0.1.11 the local updater checks
the candidate against the database before switching. Preserve update-policy
and downgrade refusals. See the tagged
[native update guide](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/updating.md#update-a-connected-server).

The native command and Basaltwater setup operate on the same upstream-managed
user service. To update through setup, rerun the saved command with its
existing options; `--refresh-packages` is only needed when APT packages should
also be refreshed.

During an automatic setup update, an upstream updater failure does not take
down a previously working installation. If the managed service file and active
runtime remain valid, Basaltwater health-checks that runtime, reports that it
was retained instead of updated, and continues setup. A first install,
a damaged runtime, or a failed readiness check remains fatal. Updater failures
include bounded diagnostics from both the beginning and end of npm's output so
an earlier npm error is not hidden by a later successful native-build message.
Even an updater that reports failure may already have started a new service
invocation; setup preserves that migration while validating the retained runtime.
Pending managed configuration changes survive interrupted reconciliation and
are applied on a later setup, without restarting an invocation that already
loaded them.
Setup records this checkpoint in the private
`~/.config/systemd/user/.basaltwater-t3-reconcile.json` file and removes it after
verified application. Its adjacent `.lock` file remains to serialize setup;
rerunning setup completes pending reconciliation automatically.

For older npm-backed runtimes, keep the npm settings scoped to the trusted T3
updater. npm 12 blocks native dependency scripts by default, but inherited `allow-scripts` or
`dangerously-allow-all-scripts` settings cannot be used by T3's nested
project-scoped install: npm rejects that combination with `EALLOWSCRIPTS`.
This also applies when npm reads `allow-scripts` from a user or global
`.npmrc`: the outer `npx` re-exports the setting before T3 starts. Basaltwater
removes only those policy variables inside the `npx` command boundary and
places a managed npm passthrough first in the T3 service PATH. The passthrough
recognizes only an exact versioned `t3` install targeting T3's immutable
`.staging-*` directory. For that call it creates a short-lived, project-scoped
npm policy allowing only `node-pty` and `msgpackr-extract`, then removes the
policy before T3 publishes the runtime. All other npm commands pass through
unchanged, and the target user's normal npm configuration remains unchanged.

If the client reports that its update needs a newer T3 Code service launcher,
rerun the same Basaltwater setup on that VM or follow the focused
[launcher recovery procedure](../../common/agent_skills/basaltwater-t3code/references/updates.md#launcher-recovery).
The doctor then validates the selected runtime. If npm 12 already
produced an incomplete candidate and T3 rolled back, setup identifies the retained
`failed` or `rolled-back` npm-backed candidate from reviewed service state
and rebuilds its two trusted native dependencies without stopping the active
working version. Then retry **Update server** in the client. A refresh setup
performs the same repair before invoking the upstream updater.
If that retained candidate cannot be probed or repaired, including a command
timeout, setup warns and continues with the upstream updater and active-runtime
checks. An obsolete failed candidate does not block installing the current
release or retaining a healthy service;
failure to validate the selected active runtime still stops setup.
Setup bounds each native probe to 30 seconds and each native rebuild to
10 minutes.

Older npm-backed T3 runtimes need a local `node-pty` build, so Basaltwater also
selects the `gcc` and `g++` provided by `build-essential` for setup-time and
service-initiated updates. This prevents a stale inherited `CC` or `CXX` value
from selecting a missing versioned compiler. Basaltwater validates the native
module, rebuilds an incomplete npm-backed active runtime only after successfully
stopping the service, and waits for several consecutive healthy service and
HTTP 200 checks from the same systemd service invocation before setup succeeds.
A restart resets those checks. Readiness
allows up to 30 minutes, matching the upstream launcher's preparation window:
copying an existing database, migrating it, and optimizing SQLite can take
minutes. Setup prints progress every 30 seconds and fails promptly if systemd
reports a failed service. It does not restart a newly updated runtime a second
time while that runtime may be migrating. This also covers an updater that
restarts a repaired launcher without changing the version. Managed configuration
changes still restart the service when the updater leaves it running unchanged.
A readiness timeout alone does not prove a crash; inspect the boot log before
retrying or interrupting migration. The same active-runtime repair is available
after setup:

```bash
basaltw agent doctor --capability t3code --fix
```

Doctor results establish service readiness only; they do not establish
connected-client presentation, provider sign-in, or remote browser reachability.
Use `--wait-for-t3-startup` after an update to allow the same 30-minute startup
window; it checks for stable readiness without restarting an already active
migration. Ordinary doctor checks remain quick. The web panel hands updates
to T3’s native environment settings. A failed or
timed-out stop skips native repair and leaves readiness unhealthy with the stop
diagnostic.
After updates and reboots, record readiness as the target account with
`basaltw agent doctor --capability t3code --capability host --record`. No
automatic post-boot readiness audit runs.

T3's service-state protocol identifies the launcher and runtime contract, and
upstream has changed that contract as its executable layout evolved. Basaltwater
accepts protocol 2 and later for active runtime selection only when
`activeVersion` is valid SemVer and the installed executable matches a known
layout. This lets a protocol-only launcher update work without assuming that an
unknown runtime layout is executable. Setup, the doctor, and the stable `t3`
wrapper share the version and layout checks. Retained failed-update repair and
cache cleanup can alter or delete runtime data, so they remain limited to
explicitly reviewed protocols 2 through 4 and fail closed for newer protocols.
When upstream changes the active state fields or executable layout, update the
shared state/version/layout definitions and compatibility tests, then review
whether the new protocol is safe for repair and cleanup. Both the standalone
executable layout (`versions/<version>/t3`) and the older npm layout remain
supported. The published runtime archive includes its executable and native
packages. Basaltwater checks its `node-pty` with the executable's embedded Node
using a short-lived preload that exits before T3 opens application state. Host Node.js is not needed
for this check. npm-backed runtimes use the Node configured in the service PATH
and retain the `node-pty`/`msgpackr-extract` repair allowlist. Doctor places that
Node's directory first in the repair PATH so npm and its build scripts use the
same runtime as the service. Host npm must not rebuild a standalone archive
against a different Node runtime. A failed archive probe is reported without
stopping an active service; restore the damaged
version from its matching upstream release archive and rerun setup. Basaltwater
applies the supported 50 MiB request-body limit to T3's managed HTTPS route while leaving
the pairing route at its deliberately small limit. See the upstream
[background-service documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md),
[update documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md),
and [release process](https://github.com/pingdotgg/t3code/blob/main/docs/operations/release.md).

Basaltwater currently targets stable T3 Code releases. Existing installations
use the normal upstream update path; application data stays under
`~/.t3/userdata`, and Basaltwater does not edit T3's database or schema versions.

### Orchestration V2 thread migration

Stable T3 `0.1.0` changes the client/server orchestration protocol from 1 to 2.
Update the desktop/mobile client as well as the server; an old client can
disconnect even while the server is healthy. This is separate from the
service-state protocol, which was 3 for that release; 0.1.10 uses launcher
protocol 4. The matching
desktop release is `0.1.0`; the mobile release is `2.0`. If the mobile update
has not reached your app store, use the server's web app in a mobile browser.
See the [release's client compatibility notes](https://github.com/pingdotgg/t3code/releases/tag/v0.1.0).

Before the first upgrade, stop the service after active work finishes and copy
the complete private `~/.t3/userdata` directory, including database sidecars and
attachments. The first V2 launch copies `state.sqlite` to `statev2.sqlite` and
migrates the copy. Leave both databases intact. Existing threads import
automatically; continuing one starts a fresh provider session with a budgeted
history handoff. The V2 desktop app also uses a separate browser profile, so
websites may need a fresh sign-in.

A healthy Basaltwater doctor result does not prove client protocol compatibility
or complete conversation migration. Follow the
[stable migration procedure](../../common/agent_skills/basaltwater-t3code/references/thread-migration.md)
and verify representative threads in the updated client. Do not delete state,
reset pairing, edit migration bookkeeping, or downgrade to resolve a mismatch.
The [upstream migration contract](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/thread-migration.md)
describes the retained history and its limitations.

Older basaltwater installations used a root-owned
`basaltwater-t3code.service` and a separate npm runtime. A subsequent setup
stops that service, starts and validates the upstream user service, and only
then disables and removes the old unit and clears its retained failed state.
A failed legacy stop aborts before updating or activating the replacement.
The old service is restarted if a later migration step fails.

Retiring the old AppImage installer did not delete an AppImage that an
older setup placed in a user's home. After confirming the files were not
replaced with user-managed content, that retired installation can be removed
manually:

```bash
rm -- "$HOME/.local/share/t3code/t3code.AppImage"
rm -- "$HOME/.local/bin/t3code"
rm -- "$HOME/.local/share/applications/t3code.desktop"
rmdir --ignore-fail-on-non-empty "$HOME/.local/share/t3code"
```
