# Managed T3 service updates

Basaltwater targets stable T3 releases. When T3 Code is selected, a setup
rerun checks npm's `t3@latest` stable channel and updates the upstream service
when a newer stable release is available. An explicit setup refresh restarts
the healthy service even when runtime and managed configuration are unchanged;
if the updater already started a fresh service, setup does not restart it again.
A healthy non-refresh setup reuses the running service. Prefer the
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
