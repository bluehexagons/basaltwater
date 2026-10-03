# Managed T3 service updates

When T3 Code is selected, a Basaltwater setup rerun checks the upstream
service and updates it when a newer release is available. A healthy service is
restarted only when the runtime or managed configuration changes. Prefer the
connected client's explicit **Update server** action when an update should be
performed outside setup. For a host-side update, set `T3_RELEASE` to the exact
version required by the client. Use `latest` only for a matching stable client;
it does not select the newest nightly or preview. Before the first upgrade to
orchestration V2, read [the thread migration procedure](thread-migration.md)
and arrange a private recovery copy and the required interruption. Then run:

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
basaltw agent doctor --capability t3code --fix
```

If the client says the update requires a newer T3 Code service launcher,
reconcile it with the matching release's command above. For a stable client,
rerunning the saved Basaltwater setup command on that VM also reconciles the
launcher and runtime. A stable setup rerun cannot advance a nightly
client/server pair; use the exact matching release above for that forward
upgrade. The doctor validates the selected runtime. Do not pass
`--allow-downgrade` or replace an existing V2 database with its V1 source.
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
stopping the working active version; then retry **Update server**. The doctor
repairs and verifies npm-backed active runtimes. Standalone archives are checked
with their embedded Node, without requiring host Node for the probe. If that
check fails, restore the matching upstream release archive and rerun setup;
host npm must not rebuild the archive against a different Node runtime.
`basaltw agent update` remains an agent-only updater; setup reruns update T3
and selected Codex, Claude Code, and OpenCode installations as well.
Do not start a second foreground T3 server on the managed port.
