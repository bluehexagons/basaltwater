# Managed T3 service updates

This procedure manages Debian's upstream `t3code.service`. CachyOS web uses
`basaltwater-cachyos-t3.service` and staged setup activation; use its installed
`basaltwater-cachyos-t3code` skill. CachyOS desktop and native Windows use their
AUR and WinGet packages, respectively. On CachyOS, `t3 service install` creates
a separate service and can select different data. Upstream 0.1.10 also supports
Windows background services; Basaltwater's Windows profile still manages the
desktop. Do not install a service as an incidental desktop repair.

Basaltwater targets stable T3 releases. When T3 Code is selected, a setup
rerun checks npm's `t3@latest` stable channel and updates the upstream service
when a newer stable release is available. Successful setup queues a final
service start/reload after providers, skills, saved state and notifications,
even when runtime and managed configuration are unchanged. The independent
completion worker waits for the setup process to exit and checks stable HTTP
readiness afterward. Internal non-refresh service checks reuse a healthy running
service. Prefer the
connected client's explicit **Update server** action when an update should be
performed outside setup. For an existing managed Debian service, use T3's
native host updater as the target account:

```bash
t3 update --channel stable CLIENT_VERSION
basaltw agent doctor --capability t3code --wait-for-t3-startup
```

Replace `CLIENT_VERSION` with the exact stable version required by the client.
Omit it only when both sides should use the latest stable release. Check
`t3 --version` first: setup preserves an existing `t3` command. If that command
belongs to another installation, invoke the managed CLI as
`~/.local/bin/basaltwater-t3code-pairing-provider` instead. Keep the same T3 home;
do not change `--base-dir` as part of an update.

In an interactive terminal, the native updater asks before restarting the
background service. Arrange interruption of active turns and terminals before
accepting. Without interactive input it stages the update without restarting
unless `--yes` is supplied; staging does not establish that the new version is
running. If activation was deferred, run `t3 service restart` at the arranged
time, then check readiness. Older launchers can remain down after a crash or
reboot until that restart. In 0.1.11, the local `t3 update` command checks the
target build against the database before switching. Keep the active version
when preflight reports an incompatible or unreadable database. See the tagged
[native updater](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/updating.md#update-a-connected-server).

## Launcher recovery

If the managed CLI is unavailable or the client requires a newer service
launcher, rerun the saved Basaltwater setup command. To reconcile the launcher
directly, set `T3_RELEASE` below to the exact stable version required by the
client. This installation path can restart the service; arrange the
interruption first. Nightly and preview builds are used only for isolated
forward-compatibility testing. Stable `0.1.0` introduces orchestration V2:
before the first upgrade follow the
[thread migration procedure](thread-migration.md), arrange the interruption and
private recovery copy, and update the client as well. For launcher recovery:

```bash
T3_RELEASE=CLIENT_VERSION
T3_NPM_SHIM="$HOME/.local/share/basaltwater/t3-npm/bin"
env -u npm_config_dangerously_allow_all_scripts \
  -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
  -u npm_config_allow_scripts \
  -u NPM_CONFIG_ALLOW_SCRIPTS \
  PATH="$T3_NPM_SHIM:$PATH" \
  CC=gcc \
  CXX=g++ \
  npm_config_strict_allow_scripts=false \
  npm_config_foreground_scripts=true \
  npx --yes --package="t3@$T3_RELEASE" -c \
  'env -u npm_config_allow_scripts \
    -u NPM_CONFIG_ALLOW_SCRIPTS \
    -u npm_config_dangerously_allow_all_scripts \
    -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
    t3 service install'
basaltw agent doctor --capability t3code --wait-for-t3-startup
```

The doctor validates the selected runtime; use its separate `--fix` only for
an authorized repair. Do not pass
`--allow-downgrade` or restore older application data over a newer database
without release-specific upstream recovery instructions.
Basaltwater recognizes service-state
protocols 2 through 4, including the standalone executable runtime layout used by
current T3 releases.

For older npm-backed runtimes, keep those npm settings scoped to this trusted
T3 update command. npm 12 rejects inherited `allow-scripts` and
`dangerously-allow-all-scripts` settings in T3's nested runtime. Basaltwater
installs the referenced npm passthrough; it
recognizes only a versioned T3 install into an immutable `.staging-*` runtime,
creates a short-lived project policy allowing only `node-pty` and
`msgpackr-extract`, and removes it before publication. Other npm commands pass
through unchanged.

If an older npm-backed update rolled back with a native-module load error,
rerun the VM's Basaltwater setup. It repairs the retained candidate without
stopping the working active version; then retry **Update server**.
If a retained candidate cannot be probed or repaired, including command timeouts,
setup warns and continues with the upstream updater and active-runtime checks
instead of letting an obsolete candidate block the current release. Validation of the active runtime and
service remains required. The doctor repairs and verifies npm-backed active
runtimes only after a successful service stop; a failed or timed-out stop skips
repair and reports unhealthy readiness with its diagnostic. Standalone archives
are checked with their embedded Node, without requiring host Node for the probe.
If that check fails, restore the matching upstream release archive and rerun setup;
host npm must not rebuild the archive against a different Node runtime.
`basaltw agent update` remains an agent-only updater; setup reruns update T3
and selected Codex, Claude Code, and OpenCode installations as well.
Do not start a second foreground T3 server on the managed port.

The first V2 startup can spend minutes copying and migrating the database before
it serves HTTP. Basaltwater waits up to 30 minutes with progress every 30 seconds
and stops waiting early if systemd reports a failed service. It leaves a newly
updated service running rather than restarting its migration a second time,
including same-version launcher repairs. Readiness requires HTTP 200 and three
checks from the same systemd service invocation; a restart resets the count.
An updater may already have restarted the service even when it reports failure;
setup checks the invocation before deciding another restart is needed. Pending
configuration reconciliation survives interrupted setup and is completed on retry.
The opt-in doctor flag `--wait-for-t3-startup` allows the same startup window
without restarting an active migration. Use it for host-side verification
after native updates; normal doctor checks remain quick. The web panel links
to T3's native environment settings for updates.
If an older Basaltwater setup timed out, inspect the boot log and service state
first; do not assume the migration crashed or delete either database. Update
Basaltwater and rerun the saved setup command to apply the readiness fix.

## Release tracks and archive layout

Stable 0.1.12 retains launcher protocol 4 and accepts protocol 3 launchers for
remote updates. Database migrations advance from 102 to 117. Before an arranged
restart, review active turns, terminals and any enabled workflow runs. Preserve
the matching private recovery copy and allow database preflight to block an
older build. See the
[0.1.12 operator record](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/t3-releases/0.1.12.md).

Stable 0.1.10 advances the launcher to protocol 4, retaining protocol 3 launcher
compatibility and the durable state/layout contracts. Basaltwater recognizes
protocol 4 for retained failed-update inspection and runtime retention; future
unknown protocols remain blocked for maintenance. Migrations advance to 96.
See the [0.1.10 operator record](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/t3-releases/0.1.10.md)
for qualification evidence.

Stable 0.1.9 retains protocol 3, the standalone archive layout and the
administrative pairing contract. Its database adds migration 81; retain
upstream downgrade/preflight checks. Failed downloads now identify the step,
HTTP status and download host. Diagnose that failure before retrying instead
of treating it as a native-module failure.

Stable 0.1.8 retains the same protocol/layout and administrative pairing
contract. Provider discovery updates do not authorize changing the owner's
provider, model, or release track.

Stable 0.1.7 retains service-state protocol 3 and the standalone archive layout.
An installed version can differ from the running service: upstream records
`runtime/.restart-pending` when activation remains outstanding. Setup checks
systemd invocation identity before deciding an updater already restarted it;
it restarts an unchanged invocation even when the selected executable changed.
An unavailable invocation plus a pending marker also requires activation.
Do not delete the marker to report an update as ready.

0.1.7 also reads machine-wide managed policy on startup. Use `t3 policy check
policy.json` before deployment and `t3 policy show` as the T3 user to compare
installed and loaded policy. Invalid or conflicting sources prevent startup;
client restrictions can block remote pairing/Connect, and update restrictions
can disable updates or select a mirror. Preserve those restrictions. Basaltwater
does not install policy or remove it during refresh. Linux policy lives at
`/etc/t3code/policy.json`; Windows can use Group Policy or a policy file, and WSL
reads its own Linux policy. See the tagged
[policy guide](https://github.com/pingdotgg/t3code/blob/v0.1.7/docs/user/managed-policy.md).

Policy defaults apply where the user has no choice saved; locks enforce values.
A `maxRuntimeMode` cap also controls provider launch configuration. Pin absolute
provider binaries and required environment variables in policy when deploying
that cap; a user-controlled PATH or wrapper does not establish enforcement.
The cap controls T3's provider requests, not a sandbox around independent CLIs.
Policy changes need an arranged T3 restart; reconnecting a client does not reload
them. Validate policy against the installed version before deploying newer keys.

Stable 0.1.6 keeps service-state protocol 3 and the standalone archive layout.
It adds lower-priority agent slices with memory/task bounds and server resource
priorities. Debian uses the upstream unit; CachyOS's managed web unit follows
those server priorities, while the packaged desktop manages its own runtime.
For an explicitly requested Debian pressure-tuning operation, `t3 service protect`
previews privileged settings and asks for confirmation/sudo. It can also protect
SSH/Tailscale. `t3 service status` reports protection and
`t3 service protect --undo` removes it. Memory protection applies immediately;
the server OOM score takes effect after an arranged service restart. It targets
the upstream service, not CachyOS's separate web unit or desktop. Basaltwater
does not run it automatically. See the
[tagged protection guide](https://github.com/pingdotgg/t3code/blob/v0.1.6/docs/user/background-service.md#keep-the-server-responsive-when-agents-use-all-memory).

Stable 0.1.5 adds **Settings → About → Update track** in the desktop app to
switch connected installations between Stable and Nightly. Ordinary **Update
all** uses their selected tracks. Basaltwater setup continues to target stable;
a requested Nightly trial is a separate operation. The switch dialog lists
machines it skips and blocks a target that cannot support the recorded database
migrations, including when compatibility cannot be established. Preserve those
blocks instead of forcing a downgrade or editing migration records. A machine
can retain its requested track after an incomplete switch; inspect its running
version as well as the track setting. See the
[tagged update guide](https://github.com/pingdotgg/t3code/blob/v0.1.5/docs/user/updating.md#switch-between-stable-and-nightly).

Linux 0.1.5 archives include `lib/libatomic.so.1` beside the executable, with
`$ORIGIN/lib` RUNPATH. Preserve the complete archive directory during recovery;
copying only `t3` loses the bundled library. Basaltwater's runtime selection and
CachyOS staging already preserve this layout. No global `LD_LIBRARY_PATH`
override is required. The desktop release also updates Electron for its Brotli
security fix; updating a server does not update a separately installed client.
See the [archive builder](https://github.com/pingdotgg/t3code/blob/v0.1.5/scripts/build-cli-archive.ts)
and [desktop release](https://github.com/pingdotgg/t3code/releases/tag/v0.1.5).

## 0.1.8 service environment and maintenance

The upstream launcher reads `service.env` under its T3 home at startup. Values
override inherited environment values except `PATH`, `T3CODE_HOME`,
`T3_BOOT_SERVICE_UNIT` and `T3_SERVICE_LAUNCHER_CONTEXT`. Inspect its relevant
non-secret keys when managed host/port behavior differs from the systemd
drop-in: `T3CODE_HOST` and `T3CODE_PORT` there can override the inherited
Basaltwater values. Keep the configured bind/source allowlist consistent;
do not copy the file into diagnostics or remove unrelated owner settings.
An explicit `T3CODE_HOST` takes precedence over upstream's LAN setting.
Changes require an arranged service restart, not merely selecting a version.

For Debian's managed service, `basaltw agent doctor --capability t3code` requires
the `service_environment` check. It uses the selected runtime's Node dotenv
parser to compare persisted bind overrides with the managed drop-in and reports
only conflicting key names. Missing files or matching settings pass. Files that
cannot be inspected safely, including symlinks and files larger than 1 MiB,
remain unverified. Reconcile conflicts privately before an arranged restart.
`--fix` preserves the file and skips runtime repair and service restart until
this check passes; `--wait-for-t3-startup` also skips its long wait on failure.

**Settings → Maintenance → Reclaim** is offline database repair: it stops
agents and the server, rebuilds the database and restarts it. Arrange downtime
and a private recovery copy first. Free-space requirements include about twice
the live data across the temporary directory and the database filesystem, plus
WAL growth/headroom. Use native `t3 doctor --dry-run --base-dir PATH` to inspect
another environment. Native `t3 doctor --fix` runs recommended repairs and can
stop a matching installed service; it is different from `basaltw agent doctor
--fix`. A manually started server or desktop must be stopped first. Never add
reclamation or legacy-database deletion to routine cache cleanup. Preserve
custom base directories and migration compatibility blocks. Source:
[service environment and repair](https://github.com/pingdotgg/t3code/blob/v0.1.8/docs/user/background-service.md).

## Refresh from a T3 terminal

On Debian with systemd service management, `sudo basaltw refresh` queues a root-owned system service before
upgrading source or applying setup. It survives closure of a local T3 terminal
or an SSH connection from another T3 environment. Exit 0 confirms dispatch,
not successful refresh. The command prints its private log under
`/var/log/basaltwater-refresh/` and a `systemctl show` command for the final
`Result` and `ExecMainStatus`. The final T3 reload is a separate completion
job. Use the receipt path and journal command printed in that log, then run
`basaltw agent doctor --capability t3code`. Dry runs and OCI refresh without
service management stay in the foreground; OCI does not host managed T3.

The final T3 completion worker records `status` and `issue` in a private
per-request JSON receipt. The timer and successful service unload after the
worker exits. The journal records its outcome, and failed services remain
visible in systemd. A `ready` receipt confirms the worker's readiness checks.
Verify an authenticated provider turn separately. If the worker has exited
and the receipt is still nonterminal, check the journal before starting a
replacement refresh.

CachyOS retains interactive package/sudo prompts and defers disruptive T3
activation until all steps, saved state and notifications finish. Its queued
worker starts the selected web service, or gracefully reloads/starts desktop
when a graphical session is available. Check the printed user journal unit;
otherwise desktop startup waits until KDE login. Native Windows queues a
same-session Task Scheduler worker after successful setup receipts, uses only
graceful window closure, and writes a constant completion status under
`%LOCALAPPDATA%\Basaltwater\t3-completion`. If the app cannot close safely or
the login session differs, reopen it manually. CachyOS reports pending reboot
signals without restarting the OS. Native Windows setup queues a required
Windows reboot with a visible countdown and a **Later** option; T3 startup then
uses the next account login. If the reboot is deferred, reopen T3 manually.
Use `-NoReboot` or `basaltw refresh --no-reboot` to defer the reboot for one run
while retaining T3 completion. The WSL profile requires a manual Windows reboot.
See the [Windows restart workflow](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows.md)
for requirement checks, interruption recovery and status inspection.
