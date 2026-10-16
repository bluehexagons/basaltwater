# CachyOS maintenance and recovery

For a first installation, follow the [CachyOS setup guide](cachyos.md).
This reference covers an established Basaltwater installation. Run commands
in fish from the KDE session as the same desktop account, without `sudo`
unless a specific command requires elevation.

## Privileged checks from an agent

On an existing KDE desktop with a registered polkit authentication agent, an
agent may request an authorized privileged command through the normal desktop
password dialog when its execution policy permits. A failed `sudo -n` check
alone does not establish that desktop authentication is unavailable. Announce
the specific check and run the executable directly, for example:

```fish
/usr/bin/pkexec --disable-internal-agent /usr/bin/ufw status verbose
```

The owner enters the password only in KDE's dialog; the agent receives the
command's output. `--disable-internal-agent` prevents a fallback password prompt
in the agent's terminal. If no graphical agent is available or authentication
is rejected, leave the check pending and use the owner's KDE terminal. Do not
collect passwords in chat, tool stdin, scripts, or environment variables.

For the mirror recovery described below, the scoped elevated command is:

```fish
/usr/bin/pkexec --disable-internal-agent /usr/bin/systemctl start cachyos-rate-mirrors.service
```

For effective firewall inspection, use the same invocation with
`/usr/bin/ufw show raw` and inspect the live IPv4/IPv6 input chains and rule order.
Authentication applies to the requested command; it does not authorize other
system changes. This is an interactive audit/recovery procedure, separate from
the unprivileged, read-only doctor.

When copying commands into a terminal, copy only the command text. `fish` is
the code block's language label, not a command prefix. Run each command
separately; joining several lines with spaces passes later commands as arguments
to the first command.

## Installer data and older installations

The installer no longer migrates `infra_tools` user data. Any remaining old
installation must first use the [intermediate version](../migration-guide.md).
An existing `cachyos-t3` data directory at the default Basaltwater install path
is retained during reinstall.
Installer updates also carry forward managed `state`, `deployments`, and
`worktrees` directories.
If that path also contains other unmanaged files, move or resolve them before
rerunning the installer.

## Mirror refresh failures

If `health.mirrors` reports failure, inspect the distro service and the hosts
named in its journal before changing package or network settings:

```fish
systemctl status cachyos-rate-mirrors.service --no-pager
journalctl -b -u cachyos-rate-mirrors.service -n 60 --no-pager
systemctl list-timers cachyos-rate-mirrors.timer --no-pager
getent hosts HOST_FROM_ERROR
```

A successful lookup does not establish that DNS or HTTPS worked during
the failed run. Check the loaded local unit to see its current retry policy;
the service policy belongs to CachyOS and may change with normal updates.
Basaltwater observes it without installing retries, changing distro units,
or refreshing package databases.

Once connectivity is restored, retry the existing distro service from KDE:

```fish
sudo systemctl start cachyos-rate-mirrors.service
systemctl show cachyos-rate-mirrors.service -p ActiveState -p Result
basaltw local cachyos-doctor
```

A successful oneshot normally returns to `ActiveState=inactive` with
`Result=success`; clearing failed state alone does not rerun mirror refresh.
Resolve any new journal error, then use the normal full CachyOS update
workflow if needed. Do not use `pacman -Sy` or replace DNS servers to hide a
transient failure. A service with no recorded failure does not prove that its
mirror list or package sync metadata is current.

## Surface resume and touchpad warnings

Resume warnings and touchpad-jump messages are separate symptoms. A warning
after resume can indicate an ordering or device-state problem, but its timing
alone does not identify a fixed kernel or driver issue. Check whether battery,
wake, pointer, or gesture behavior is affected before changing kernel,
firmware, or device quirks.

Libinput discards touch coordinates it considers implausible. If the only
symptom is a log warning, that filter may be working as intended; visible
pointer jumps, freezes, or broken gestures need focused device investigation.
Use the current boot and user-session journals, identify the actual event
node, and follow the distribution's hardware-support procedure. Do not add
device-specific overrides based only on a warning.

## Direct T3 access across LAN subnets

`--lan-access` allows the default-route private subnet discovered on the
workstation; it does not allow every private address range. Explicit
`--access-source` values replace inferred LAN discovery, even when
`--lan-access` remains selected. Include every intended network in the saved
setup and preview changes with `basaltw refresh --dry-run` before applying.

Access sources apply to all selected managed services, including Sunshine;
they are not a T3-only allowance. The router must route the client to the
workstation and permit traffic between networks. A host firewall allow cannot
create that route or bypass router isolation/NAT.

T3 Connect and direct LAN pairing are separate connection modes. Use the T3
desktop's connection settings for Connect, or test a new connection from the
intended client after applying direct-source rules. See the upstream
[remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)

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

## T3 desktop login startup

Desktop mode enables T3 Code at each KDE login. Existing desktop installations
acquire this behavior on their next setup or refresh:

```fish
basaltw refresh --dry-run
basaltw refresh
basaltw local cachyos-doctor
```

The entry is `~/.config/autostart/basaltwater-cachyos-t3.desktop`, or
`$XDG_CONFIG_HOME/autostart/basaltwater-cachyos-t3.desktop` for an absolute XDG
override. It uses the package-owned `/usr/bin/t3code` and KDE's normal login
environment. No user service or restart loop is added for the desktop app;
quitting it leaves it stopped for that session. Setup retains a running app.
The entry's `TryExec` prevents login launch attempts after package removal.

Use **System Settings → Autostart** to disable T3 startup. The doctor reports a
disabled entry as advisory; the next desktop setup or refresh re-enables it.
Missing or modified entries and a missing native executable are reported as
failures. Setup refuses symlinks or unmanaged content at the managed filename;
inspect that file locally before retrying. Other personal startup entries are
retained. Remove duplicate personal T3 entries in KDE if you previously added one.

Verify startup with a later logout/login when convenient, then test a provider
thread and terminal in T3. The doctor observes startup configuration without
launching the app and cannot prove that a login launch succeeded. No automatic
login or pre-login desktop is configured.

## AUR download failures

A generic Shelly source-download error may omit the underlying Git failure.
Inspect the installed version, configured source, and cache ownership as the
desktop user before retrying:

```fish
pacman -Q shelly
shelly config get AurUrl
ls -ld -- "$HOME/.cache" "$HOME/.cache/Shelly"
```

If the Shelly cache directory is root-owned, repair only that directory after
confirming it is the normal user cache and not a symlink or shared location.
Do not recursively change ownership across the home directory or delete
checkouts that may contain edits.

To inspect source connectivity without building or installing, clone into a
new temporary directory with the same configured AUR base URL:

```fish
set -l aur_probe (mktemp -d)
and git clone -- https://aur.archlinux.org/t3code-bin.git "$aur_probe/t3code-bin"
```

This downloads packaging files only; do not execute them for diagnosis.
A successful clone does not rule out a cache, privilege, dependency, or later
application-download failure. Inspect the Shelly log path reported by the
installation error, then rerun the same full setup command.

## Sunshine service and encoder checks

Sunshine supports Intel, AMD, and NVIDIA GPUs. On Linux, Intel/AMD hardware
encoding can use VA-API; a missing CUDA library during automatic probing does
not establish a streaming failure on those GPUs. See the upstream
[encoder settings](https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md#encoder).

`--sunshine` installs the native package and `libva-utils`, enables Sunshine at
KDE login, and starts its user service. Setup verifies that the same process
and service invocation remain active across consecutive checks; repeated
restarts cannot count as stable startup. The package's `Restart=on-failure` policy allows a
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
inactive service from failures without restarting it. `startup.sunshine`
separately checks persistent login enablement; disabled, masked, and runtime-only
enablement are reported as failures for the saved Sunshine selection. An enabled
service quit for the session remains advisory. Rerun the saved setup when you
want login startup restored. It also checks H.264 High VA-API encoding profiles;
older setups may need the diagnostic
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
Sunshine is stopped. This prefers VA-API before Vulkan probing, which can fail
on some Intel/Mesa combinations. Sunshine can still probe other encoders if
VA-API initialization fails. Other settings, explicit encoder selections, and
active services are retained. Unknown/mixed GPU layouts retain automatic
selection; setup does not install or modify graphics drivers.

If logs stop at `Trying encoder [vulkan]` or `Creating encoder [h264_vulkan]`
and the crash stack points into the Intel Mesa Vulkan driver, try the documented
`encoder = vaapi` setting in your existing Sunshine configuration, after
confirming VA-API support. Preserve other settings. Check that VA-API actually
initializes: a failed preferred encoder can still fall back to Vulkan.
Keep capture selection separate: KDE Wayland is not a wlroots compositor,
so a missing `wlr-export-dmabuf` interface is not an NVIDIA requirement.

If system `vainfo` works but Sunshine logs `Trying to open` drivers under
`/home/runner/work/build-deps/.../libva/lib/dri` followed by
`Couldn't initialize va display`, the Sunshine build carries an incorrect
VA-API driver search path. This was observed with CachyOS
`sunshine 2026.1015.174736-1`, followed by a Vulkan fallback crash even with
`encoder = vaapi`. Setup recognizes this journal pattern for the current start
attempt and reports a package repair instead of suggesting encoder selection
alone. Its bounded journal probe reports fixed guidance, keeping raw logs out
of setup errors and notifications; unavailable logs retain generic guidance
and the canonical journal command above.

The native package needs replacement or a rebuild with libva's driver path
set to `/usr/lib/dri`, or linked to the distribution's correctly configured
system libva. Keep the native package manager's ownership and use the normal
CachyOS update/rebuild workflow. Reinstalling an unchanged affected build or
repeating setup cannot repair it. A system `vainfo` result describes its own
libva, which can differ from Sunshine's bundled copy. An environment override
is also insufficient for capability-enabled builds using
[libva's secure environment lookup](https://github.com/intel/libva/blob/master/va/va.c):
`LIBVA_DRIVERS_PATH` can be ignored. Basaltwater does not remove executable
capabilities, patch native binaries, or create build-machine paths to work
around this packaging error. To pause repeated crashes while arranging the
package repair:

```fish
systemctl --user stop app-dev.lizardbyte.app.Sunshine.service
pacman -Q sunshine
```

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
after successful activation it removes the managed desktop autostart entry.
A failed web activation keeps desktop login startup. The desktop package stays
installed. Omitting both flags leaves the current mode alone. Setup never kills
the desktop app or takes over an upstream service.

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
failure warns without failing an otherwise successful setup.

The separate `0600` `last-notification.json` records aggregate delivery status,
target count, timestamp, and a digest of the target selection and policy. It
contains no endpoint URLs, tokens, or exception text. Run
`basaltw local cachyos-doctor` to inspect this evidence without contacting
receivers. Delivered means every configured target accepted the setup event;
failed can include partial delivery, and suppressed means the level required
no delivery. The doctor defers records from different selections or older
completed setups. Setups made before this record was introduced have no
historical delivery evidence. The setup report marks its own notification
pending because delivery happens after the report is saved.

Verify the event in the receiver after applying the flags; an accepted request
does not prove downstream processing. See the
[notification guide](../services/notifications.md) for payload, TLS, token, and retry details.

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
[refresh on Debian](../reference/command-line.md#refresh-this-machine).
