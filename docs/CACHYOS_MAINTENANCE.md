# CachyOS maintenance and recovery

For a first installation, follow the [CachyOS setup guide](CACHYOS.md).
This reference covers an established Basaltwater installation. Run commands
in fish from the KDE session as the same desktop account, without `sudo`
unless a specific command requires elevation.

## Installer data and older installations

The installer no longer migrates `infra_tools` user data. Any remaining old
installation must first use the [intermediate version](BASALTWATER_MIGRATION.md).
An existing `cachyos-t3` data directory at the default Basaltwater install path
is retained during reinstall.
Installer updates also carry forward managed `state`, `deployments`, and
`worktrees` directories.
If that path also contains other unmanaged files, move or resolve them before
rerunning the installer.

## Codex updates

For deliberate standalone updates outside setup:

```fish
basaltw agent update --tool codex --dry-run
basaltw agent update --tool codex --tools-only-readiness
```

The update checks successful version/help commands and keeps a complete package
snapshot for rollback, including adjacent sandbox and other runtime resources.
It retains the latest snapshot plus any selected or running snapshots; older
validated snapshots are pruned. Update records and backups are private under
`~/.local/state/basaltwater`. Concurrent Basaltwater updates are refused until
the current update finishes. `--tools-only-readiness` avoids gating CLI updates
on the generic VM host/T3 checks, which do not qualify this desktop profile.
Restart Codex/T3 provider sessions when convenient to use the new executable.
Verify a real Codex task in T3 after setup; local login status does not test API
access or the GUI's provider environment. If needed, set T3's Codex **Binary
path** to the absolute launcher path printed by setup, typically
`/home/USER/.local/bin/codex`; keep that stable path across updates.

## T3 desktop updates

The desktop's embedded server uses the installed `t3code-bin` version. A newer
client can report a server update even when Basaltwater setup succeeded:
`setup` and `refresh` retain an installed desktop package. `pacman -Qu` checks
repository sync metadata and does not check AUR releases.

Update through the same AUR helper that installed the package. On a workstation
using CachyOS's default Shelly, run as your desktop user and complete its normal
package-review and sudo prompts:

```fish
pacman -Q t3code-bin
shelly update aur t3code-bin
pacman -Q t3code-bin
```

Finish active work and quit/reopen T3 Code afterward to load the updated embedded
server. Installing a package does not update an already running process. If
the warning remains, check which environment the other PC is connected to;
desktop and managed web runtimes have separate update workflows. If the AUR
recipe lags upstream, keep the package manager's ownership and review policy.

Desktop setup creates or protects the default `~/.t3` and `~/.t3/userdata`
directories with mode `0700`, and an existing `clerk-tokens.json` with `0600`.
It retains their contents and refuses symlinks, foreign ownership, and unexpected
file types. Private parent directories protect credentials and session databases
even if the app later creates a file with permissive settings. The read-only
`basaltw local cachyos-doctor` checks these permissions when desktop mode is saved;
it never opens tokens or databases. A permissive token file inside private parents
is advisory; exposed directories or unsafe ownership/types are failures.
Custom T3 state locations require separate inspection.

## AUR download failures

Shelly's generic source-download failure can hide the underlying Git error:
its [3.1.6 download implementation](https://github.com/Seafoam-Labs/Shelly-ALPM/blob/v3.1.6/Shelly.PackageManager/src/aur/manager.zig)
discards failed clone/pull output. This message alone does not establish a
network problem. Basaltwater now includes the helper's exit status, retry
command, and diagnostic pointers when installation fails.

First inspect the version, configured source, and cache ownership as your normal
desktop user:

```fish
pacman -Q shelly
shelly config get AurUrl
ls -ld -- "$HOME/.cache" "$HOME/.cache/Shelly"
# If you configured an absolute XDG_CACHE_HOME, inspect its Shelly directory too.
```

If the reported **Shelly directory itself** is root-owned, and it is your normal
cache directory rather than a symlink or shared location, repair just that
directory and retry setup:

```fish
sudo chown -- (id -u):(id -g) "$HOME/.cache/Shelly"
```

Use the actual path reported by preflight for a custom cache. Do not recursively
chown your home or delete the AUR cache; existing checkouts may contain edits.
If another parent directory or checkout has incorrect permissions, inspect it
separately. Setup does not assume every download failure is an ownership issue.

To expose Git's own network/TLS/proxy error without building or installing,
clone into a new temporary directory as your normal user. The URL below is for
the default Arch AUR: replace its base with your configured `AurUrl` if different,
so the check tests the same service as Shelly.

```fish
set -l aur_probe (mktemp -d)
and git clone -- https://aur.archlinux.org/t3code-bin.git "$aur_probe/t3code-bin"
```

This only downloads packaging files; do not execute them for diagnosis. A
successful clone tests access as your user, but does not rule out a Shelly cache,
elevation, dependency, or later application-download failure. Inspect Shelly's
session log at `/var/log/shelly.log` (may require sudo) or its unprivileged
fallback `$XDG_STATE_HOME/shelly/shelly.log`, or
`~/.local/state/shelly/shelly.log` when that variable is unset. The log may still
omit Git's discarded error. Resolve the reported cause, then rerun the
same full setup command. The existing managed web service stays running if
desktop installation fails.

## Sunshine service and encoder checks

Sunshine supports Intel, AMD, and NVIDIA GPUs. On Linux, Intel/AMD hardware
encoding can use VA-API; a missing CUDA library during automatic probing does
not establish a streaming failure on those GPUs. See the upstream
[encoder settings](https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md#encoder).

`--sunshine` installs the native package and `libva-utils`, enables Sunshine at
KDE login, and starts its user service. Setup verifies that it remains active
across consecutive checks. The package's `Restart=on-failure` policy allows a
normal tray-menu quit or `systemctl --user stop` to leave it stopped for that
session. It starts again at the next KDE login; `disable --now` stops it
persistently until setup selects Sunshine again. An already active service is
retained without restart.

This requires an active graphical desktop. Setup does not configure automatic
login or create a display before login; remote-first machines still need KDE
running to stream it. Setup refuses masked/custom units or drop-ins instead of
overwriting them. Sunshine pre-login streaming needs a separate greeter-to-user
handoff. LizardByte's [pre-login guide](https://app.lizardbyte.dev/2024-10-16-autostart-sunshine-on-boot-without-auto-login/)
was tested on Debian with SDDM's X11 greeter; it does not qualify current
CachyOS's Plasma Login Manager/Wayland greeter. Enabling user lingering alone
does not create that display/session integration. Basaltwater does not configure
pre-login streaming in this profile. Check the existing session from KDE:

```fish
basaltw local cachyos-doctor
systemctl --user status app-dev.lizardbyte.app.Sunshine.service --no-pager
journalctl --user -u app-dev.lizardbyte.app.Sunshine.service -n 100 --no-pager
```

With Sunshine selected in the saved setup, the doctor distinguishes an
inactive service from failures without restarting it. It also
checks H.264 High VA-API encoding profiles; older setups may need the diagnostic
tool installed with `sudo pacman -S --needed libva-utils`. Select an actual render
node from `/dev/dri` and inspect it without starting screen capture:

```fish
ls /dev/dri/renderD*
vainfo --display drm --device /dev/dri/renderD128
```

Use your machine's node, which may differ from the example. An encoding profile
uses `VAEntrypointEncSlice` or `VAEntrypointEncSliceLP`; `VAEntrypointVLD` is
decoding. Profiles alone do not prove live encoding, capture, input, or Moonlight
streaming. VA-API results do not qualify NVIDIA's separate NVENC path.

For an Intel-only render-device layout with verified H.264 VA-API encoding,
setup supplies `encoder = vaapi` when no explicit encoder setting exists and
Sunshine is stopped. This avoids the Vulkan encoder probe that crashed on the
audited Intel workstation. Other settings, explicit encoder selections, and
active services are retained. Unknown/mixed GPU layouts retain automatic
selection; setup does not install or modify graphics drivers.

If logs stop at `Trying encoder [vulkan]` or `Creating encoder [h264_vulkan]`
and the crash stack points into the Intel Mesa Vulkan driver, try the documented
`encoder = vaapi` setting in your existing Sunshine configuration, after
confirming VA-API support. Preserve other settings. This avoids the Vulkan
encoder probe; it does not repair the driver or prove the VA-API path works.
Keep capture selection separate: KDE Wayland is not a wlroots compositor,
so a missing `wlr-export-dmabuf` interface is not an NVIDIA requirement.

After addressing the reported error, the desktop owner can reset the failed
state and retry the service:

```fish
systemctl --user reset-failed app-dev.lizardbyte.app.Sunshine.service
systemctl --user start app-dev.lizardbyte.app.Sunshine.service
```

Keep Sunshine administration local at `https://localhost:47990`, pair a trusted
Moonlight client, and verify video, audio, and input. Include `--lan-access` or
explicit `--access-source` values in the full setup selection before remote
streaming. The read-only doctor does not start capture or change encoder settings.

## Switch modes on a later setup

`--t3code-desktop` and `--web-interface t3code` cannot be selected together.
Finish active work before switching:

```fish
# Web → desktop: install/verify the package, then disable the managed web service
basaltw setup agent_cachyos localhost --t3code-desktop

# Desktop → web: quit the desktop app first to free its listening port
basaltw setup agent_cachyos localhost --web-interface t3code
```

Desktop mode disables only the Basaltwater-owned user service and retains its
unit, runtime, and data. Web mode stages and starts the managed service again;
the desktop package stays installed. Omitting both flags leaves the current
mode alone. Setup never kills the desktop app or takes over an upstream service.

The web service uses `~/.local/share/basaltwater/cachyos-t3/data` explicitly,
separate from the desktop's default `~/.t3`. Switching modes does not copy,
delete, or merge their databases, projects, pairing identities, or Connect
credentials. Repositories can be opened in either environment.
Older Basaltwater web units used `~/.t3`: their next web setup starts a separate
environment in the new directory and reports this change. The old data remains
untouched in `~/.t3`; back it up and plan any history migration separately.
This separation also prevents database contention if the desktop is later opened
while the web service is running; see the
[upstream duplicate-backend report](https://github.com/pingdotgg/t3code/issues/6097).
A port conflict still requires quitting the desktop or choosing another web port.

## Web service updates and recovery

If a previous checkout produced `has a bad unit file setting`, update
Basaltwater and rerun setup. Validate the generated unit with:

```fish
systemd-analyze verify "$HOME/.config/systemd/user/basaltwater-cachyos-t3.service"
```

The service follows the user session; lingering is not enabled. An existing
upstream `t3code.service` is refused without being stopped or adopted. Manage
that unit with its original installer before selecting either T3 mode. Preflight
checks effective systemd units in all user-unit search locations, as well as
local files, and refuses masks or unmanaged drop-ins on the Basaltwater unit.
Resolve those overrides before switching or updating. To stop this managed
service persistently:

```fish
systemctl --user disable --now basaltwater-cachyos-t3.service
```

Use `journalctl --user -u basaltwater-cachyos-t3.service` for startup errors.
The generic VM T3 pairing and update commands do not manage this unit. For a
deliberate runtime update, finish active work and rerun setup as yourself:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code
```

Keep your selected host, port, and workspace options when rerunning. Each T3
setup stages `t3@latest` in a separate release directory, checks its CLI and
native terminal dependency, then validates the unit before stopping the old
service. Activation switches the CLI link and unit and starts the new runtime.
Even an unchanged T3 version is rebuilt and restarted so a Node upgrade does
not leave an incompatible native addon. Do not use `npm install --prefix` on
the managed root; it bypasses staging and can replace the stable CLI link.

Installation/validation failures leave the old service untouched. Activation
failures restore the previous runtime, unit permissions, and enabled/running
state. An interrupted activation leaves private recovery snapshots in
`~/.local/share/basaltwater/cachyos-t3/.activation`; the next setup retries
recovery before installing. If recovery is incomplete, retain that directory
and both runtimes while resolving the reported service error. Changes or
removals made to the unit or CLI link outside setup stop recovery and preserve
the snapshots for manual inspection.

Rollback covers runtime and service configuration, **not T3 database migrations**.
Back up application data before updates that may change its schema. Successful
updates retain the current and previous managed release, prune older marked
releases, and leave legacy npm files and unmarked directories alone. New files
created by the service use `UMask=0077`; existing personal data permissions are
not changed. Missing optional-provider warnings (for example, Claude on a
Codex-only installation) do not by themselves mean the selected provider failed.

See the upstream [T3 installation guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
and [remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
for current provider, client, and T3 Connect requirements.

## Webhook notifications

To add setup-result webhooks while keeping your saved software and access
selection, first upgrade the installed CLI so it recognizes these new flags:

```fish
basaltw upgrade
basaltw refresh --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https --dry-run
basaltw refresh --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

Replace the example URL with your receiver's full link. Webhooks send setup
success/failure results; no background notification jobs are installed.
`mailbox` is unsupported. The preview sends nothing and hides webhook URLs in
the displayed command. The actual refresh retains the full URL for delivery
and saves it privately only after setup succeeds. A failed run can notify the
targets provided for that attempt, while retaining the previous saved selection.

Repeatable `--notify` adds targets without exact duplicates. Omitted options
preserve saved targets, level, and HTTPS policy. Use
`basaltw refresh --notification-level off` to pause delivery; restore `normal`
to receive completion/failure results again. `warning` or `error` keeps only
failures. `--no-notification-strict-https` restores self-signed compatibility.
To replace or remove targets, run a full explicit `setup` with the desired
software/access selection and new targets, or omit `--notify` to remove them.

Targets, including any credentials in the URL, are retained in the user-owned
`0600` `last-setup.json` and `last-report.json`; keep both private. A receiver
failure warns without failing an otherwise successful setup. Verify the event
in the receiver after applying the flags. See the
[notification guide](NOTIFICATIONS.md) for payload, TLS, token, and retry details.

## Reruns, updates, and repositories

### Upgrade and repeat your last setup

After a successful `agent_cachyos` setup, use this from your KDE terminal as
the same user, without `sudo`:

```fish
basaltw refresh --dry-run
basaltw refresh
```

`refresh` upgrades Basaltwater to the latest source on the selected channel
(`dev` follows `main`), then starts the updated code with the last successful
setup selection. Version/commit channels remain pinned, just as with
`basaltw upgrade`. Setup runs even if Basaltwater was already up to date.
The dry run displays the saved command and setup plan without fetching source,
upgrading, installing, or changing the saved selection.

The private record is `~/.local/state/basaltwater/cachyos/last-setup.json`.
The adjacent private `last-report.json` records completion time, source commit
and channel (when available), selected tool/package versions, and diagnostic
observations including warnings. It is informational, not replay input. A
receipt failure warns without discarding a successful saved selection.
It saves supported setup options, including provider selections/exclusions,
T3 mode, optional tools, repositories, workspace, web bind/port, and webhook
targets/policy. It does not copy authentication files or save provider
credentials; webhook URLs may themselves contain receiver credentials. The record is
written only after all setup steps, including cleanup, succeed. A failed or
interrupted run and a dry run leave the previous successful selection intact.
Saved commands explicitly include or exclude every currently supported agent,
so changes to profile defaults do not select an unwanted agent during refresh.
Records written by older versions may omit optional-agent exclusions; repeat
your full setup command with the updated code to save those exclusions.

**First use after upgrading from an older checkout:** run `basaltw upgrade`,
then run your usual full setup command once. Older CachyOS runs did not save
their options, so refresh cannot recover them from installed packages. For
example, to establish the desktop selection:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs
```

To add software without repeating your saved flags, use:

```fish
basaltw refresh --material-maker --etcher --butler --steamcmd --dry-run
basaltw refresh --material-maker --etcher --butler --steamcmd
```

Refresh accepts the supported CachyOS setup flags (`refresh --help` lists them).
Unspecified options are preserved. Boolean and single-value options override
saved values; repeatable repositories, access sources, and webhook targets are added without
duplicates. Agent selections/exclusions override their saved opposite for that
provider. `--no-access-source` clears saved explicit sources; combine it with
`--no-lan-access` to close managed remote access. Selecting a T3 mode replaces
the saved mode; switching to desktop clears the old web bind/port.
Conflicting or invalid new options stop before upgrade. A successful setup
saves the combined selection; after a failed attempt, repeat the added flags
when retrying. Omitting an option does not remove it; software `--no-*` flags
stop managing a selection, without uninstalling it.

An older installed CLI cannot recognize newly added flags. Run `basaltw upgrade`
once first to acquire this refresh interface and any new options.
Alternatively, rerun `setup` with all desired options. That successful run
replaces the record rather than merging with earlier selections.
Refresh follows the same update behavior as setup: managed agent CLIs update,
web mode updates/restarts its service, and an installed T3 desktop package is
retained for your normal AUR update workflow. Finish active work first.

Missing or invalid saved options stop refresh before the source upgrade.
An upgrade failure prevents setup. If setup fails after a successful upgrade,
Basaltwater stays upgraded and returns setup's failure status; resolve the
error and repeat `refresh`, or run an explicit setup to change the selection.
On CachyOS this repeats only the local `agent_cachyos` profile. It does not
upgrade CachyOS itself or redeploy saved remote hosts. Debian also supports
refresh, using its existing target-side record and root setup runner; see
[refresh on Debian](COMMAND_LINE.md#refresh-this-machine).
