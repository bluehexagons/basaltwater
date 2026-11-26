# Diagnose Sunshine and Moonlight

[Topic index](cachyos-maintenance.md)

Use fish from the KDE session as the account that owns the installation.

## Sunshine service and encoder checks

Sunshine supports Intel, AMD, and NVIDIA GPUs. On Linux, Intel/AMD hardware
encoding can use VA-API; a missing CUDA library during automatic probing does
not establish a streaming failure on those GPUs. See the upstream
[encoder settings](https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md#encoder).

`--sunshine` installs the native package, `libva-utils` and `libkscreen`, enables Sunshine at
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

### Sleeping monitors and Moonlight

Display sleep is separate from agent permission and Sunshine service health.
The host can be awake with Sunshine active while its powered-down output is
unavailable for capture. In KDE, inspect and wake display power with:

```fish
kscreen-doctor --dpms show
kscreen-doctor --dpms on
```

Wait briefly, then retry Moonlight. Enabled agents can instead use
`basaltw desktop --native wake`; it honors the persistent permission state and
works without starting the native portal. Inspect a fresh capture after waking.
Wake requests preserve locking and idle policy; they do not power on an
unplugged monitor, enable a disabled output, or resume a suspended computer.

The owner can also request display wake through an existing same-account SSH
login, using KDE's user-manager environment rather than guessing a display or bus:

```fish
systemd-run --user --collect --wait --pipe -- /usr/bin/kscreen-doctor --dpms on
```

KDE must already be logged in and its user manager must have the graphical
environment. A failure remains a session/display issue; do not create another
desktop or change login policy to make this command work.

Automatic wake before a Sunshine connection is not configured. As checked on
2026-10-24, Sunshine's current launch path probes capture/encoders before its
normal application/global preparation commands. If the sleeping output makes
that probe fail, a wake command in `global_prep_cmd` never runs. See upstream
[issue #4971](https://github.com/LizardByte/Sunshine/issues/4971) and the open
[pre-display preparation proposal #5808](https://github.com/SunshineStream/Sunshine/pull/5808).
The tested CachyOS `2026.1123.40502-1` binary still lacks the proposed
`pre_display_prep_cmd` option. Once supported, that earlier hook could run
`/usr/bin/kscreen-doctor --dpms on` before probing; verify release support and
sleep-to-stream behavior before adopting it. Setup preserves existing Sunshine
commands and KDE idle settings.

### Sunshine reports busy after a failed launch

An active service and a successful encoder probe can coexist with a failed
Moonlight launch. On the default port, the local GameStream endpoint reports
Sunshine's application state. Read the state and inspect the recent service log:

```fish
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:47989/serverinfo | string match -r 'SUNSHINE_SERVER_(?:FREE|BUSY)'
journalctl --user -u app-dev.lizardbyte.app.Sunshine.service --since '10 minutes ago' --no-pager
```

Use the configured GameStream HTTP port if it differs. `SUNSHINE_SERVER_BUSY`
can appear while a client is streaming or after an application launch that never
produced a stream. Compare `Executing [Desktop]` with subsequent
`CLIENT CONNECTED` and `CLIENT DISCONNECTED` events. Use the following manual
recovery only when the display is awake and the owner confirms nobody is
streaming. Quit the running application from Moonlight if offered and retry.
If it stays busy, restart the user service once from a separate terminal or SSH
connection, then reconnect in Moonlight:

```fish
systemctl --user restart app-dev.lizardbyte.app.Sunshine.service
```

This recovered a `Desktop` launch that reported busy without an established
stream. Confirm the retry reaches `CLIENT CONNECTED` and shows the desktop in
Moonlight. If the retry fails, inspect the new launch log before making changes.
