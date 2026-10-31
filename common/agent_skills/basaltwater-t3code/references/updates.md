# Managed T3 service updates

This procedure manages Debian's upstream `t3code.service`. CachyOS web uses
`basaltwater-cachyos-t3.service` and staged setup activation; use its installed
`basaltwater-cachyos-t3code` skill. CachyOS desktop and native Windows use their
AUR and WinGet packages, respectively. On CachyOS, `t3 service install` creates
a separate service and can select different data. Upstream background services
are unsupported on native Windows; use its desktop installation workflow.

Basaltwater targets stable T3 releases. When T3 Code is selected, a setup
rerun checks npm's `t3@latest` stable channel and updates the upstream service
when a newer stable release is available. Successful setup queues a final
service start/reload after providers, skills, saved state and notifications,
even when runtime and managed configuration are unchanged. The independent
completion worker waits for the setup process to exit and checks stable HTTP
readiness afterward. Internal non-refresh service checks reuse a healthy running
service. Prefer the
connected client's explicit **Update server** action when an update should be
performed outside setup. For a host-side update, set `T3_RELEASE` to the exact
stable version required by the stable client, or `latest` when both should
use the latest stable release. Nightly and preview builds are used only for
isolated forward-compatibility testing. Stable `0.1.0` introduces orchestration
V2: before the first upgrade follow the
[thread migration procedure](thread-migration.md), arrange the interruption and
private recovery copy, and update the client as well. Then run:

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
basaltw agent doctor --capability t3code --fix --wait-for-t3-startup
```

If the client says the update requires a newer T3 Code service launcher,
reconcile it with the matching stable release's command above. Alternatively,
rerunning the saved Basaltwater setup command on that VM also reconciles the
launcher and runtime. The doctor validates the selected runtime. Do not pass
`--allow-downgrade` or restore older application data over a newer database
without release-specific upstream recovery instructions.
Basaltwater recognizes service-state
protocols 2 and 3, including the standalone executable runtime layout used by
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
without restarting an active migration. The web panel uses it after updating;
normal doctor checks remain quick.
If an older Basaltwater setup timed out, inspect the boot log and service state
first; do not assume the migration crashed or delete either database. Update
Basaltwater and rerun the saved setup command to apply the readiness fix.

## Release tracks and archive layout

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

## Refresh from a T3 terminal

On Debian with systemd service management, `sudo basaltw refresh` queues a root-owned system service before
upgrading source or applying setup. It survives closure of a local T3 terminal
or an SSH connection from another T3 environment. Exit 0 confirms dispatch,
not successful refresh. The command prints its private log under
`/var/log/basaltwater-refresh/` and a `systemctl show` command for the final
`Result` and `ExecMainStatus`. The final T3 reload is a separate completion
unit whose identity appears in that log; inspect its result too, then run
`basaltw agent doctor --capability t3code`. Dry runs and OCI refresh without
service management stay in the foreground; OCI does not host managed T3.

CachyOS retains interactive package/sudo prompts and defers disruptive T3
activation until all steps, saved state and notifications finish. Its queued
worker starts the selected web service, or gracefully reloads/starts desktop
when a graphical session is available. Check the printed user journal unit;
otherwise desktop startup waits until KDE login. Native Windows queues a
same-session Task Scheduler worker after successful setup receipts, uses only
graceful window closure, and writes a constant completion status under
`%LOCALAPPDATA%\Basaltwater\t3-completion`. If the app cannot close safely or
the login session differs, reopen it manually. Neither platform automatically
restarts the OS; completion notices report pending reboot signals separately.
