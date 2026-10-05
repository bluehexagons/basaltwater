# CachyOS creative and publishing software

The options in [issue #106](https://github.com/bluehexagons/basaltwater/issues/106)
use the existing desktop account on the latest fully updated CachyOS KDE
workstation. Package mappings and dependencies were reviewed against the enabled
CachyOS/Arch repositories and AUR recipes on 2026-09-27. Versions below are not
pinned: full distro updates remain the owner's responsibility.

Choose your software before the first setup. Add these flags after
`--local-setup agent_cachyos` in the [fish installer](CACHYOS.md#quick-start),
or preview the complete selection if you installed only the launcher:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs \
  --godot --material-maker --blender --gimp --inkscape --krita --remmina \
  --sunshine --moonlight --etcher --butler --steamcmd --dry-run
```

Remove `--dry-run` to apply the selection. Package and AUR review prompts run
in the attached KDE terminal as your desktop account.

| Application | Flag | Installation and retained ownership |
| --- | --- | --- |
| Godot | `--godot` | Repository `godot` if the command is missing; retain existing PATH installation |
| Material Maker | `--material-maker` | AUR `material-maker-bin`; also retain an installed source-built `material-maker` |
| Blender | `--blender` | Repository `blender` plus `libdecor` for native Wayland support |
| GIMP | `--gimp` | Repository `gimp` (current GIMP 3, not a GIMP 2 compatibility setup) |
| Inkscape | `--inkscape` | Repository `inkscape` |
| Krita | `--krita` | Repository `krita`; no forced Qt version or Python-plugin extras |
| Audio editors | `--audacity`, `--ardour`, `--lmms` | Corresponding native repository packages |
| Video editors | `--shotcut`, `--kdenlive` | Corresponding native repository packages |
| Scribus | `--scribus` | Repository `scribus` |
| Engineering editors | `--freecad`, `--kicad` | Corresponding native repository packages |
| OBS | `--obs` | Repository `obs-studio`; no automatic capture or recording |
| Remmina | `--remmina` | Repository `remmina`, `freerdp`, `libvncserver`, `spice-gtk`, `gtk-vnc`, `libsecret` |
| Sunshine | `--sunshine` | CachyOS repository `sunshine`; does not start or enable it |
| Moonlight | `--moonlight` | Repository `moonlight-qt`; command is `moonlight` |
| Balena Etcher | `--etcher` | CachyOS repository `etcher-bin`; command is `etcher` |
| itch.io butler | `--butler` | AUR `butler`; independent of `--godot` |
| SteamCMD | `--steamcmd` | AUR `steamcmd`, with repository `lib32-glibc` and `lib32-gcc-libs`; independent of `--gaming` |

Native dependencies follow the enabled repositories' priority, including CachyOS
optimized builds. Setup installs missing packages only, never runs `pacman -Sy`,
and does not silently upgrade the OS or installed AUR packages. Missing-package
errors require resolving the mirror/repository problem or completing a normal
full-system update, then rerunning setup. Omitted or `--no-*` software flags stop
managing that selection; they do not uninstall packages or remove user data.
Successful selections are saved for `basaltw refresh`, and the doctor/receipt
records selected software metadata without launching a GUI or publisher.
For later software additions, see [saved setup maintenance](CACHYOS_MAINTENANCE.md#upgrade-and-repeat-your-last-setup).

Desktop application selections also install Python GObject, AT-SPI,
GStreamer/base/PipeWire and GTK3 prerequisites for task-scoped automation.
Setup does not start input/capture. The [native desktop guide](CACHYOS_DESKTOP.md)
and managed `basaltwater-cachyos-desktop` skill cover autonomous application
editing, scripting, editable sources, exports and reopen/consumer checks.
Use `basaltw agent manifest --json` for installed applications and actual
launch arguments. Native UI work uses `basaltw desktop --native start`
with the owner's KDE portal consent, preserving the existing desktop.

## AUR review and existing installations

The three new AUR options share T3 desktop's helper and cache handling. Missing
packages use `shelly install aur PACKAGE` as the human user, with normal review,
build, and sudo prompts. Paru or yay is used only when Shelly is absent; setup
does not switch helpers after a failure, change AUR sources, suppress checksums,
or approve a package build for you. Review the PKGBUILD and dependencies. Installed
packages are retained without requiring an AUR helper. If an unmanaged executable
already exists but no supported package owns the installation, setup stops rather
than replacing it: omit the flag to retain its original manager, or migrate it
deliberately. [AUR troubleshooting](CACHYOS_MAINTENANCE.md#aur-download-failures) also applies.
Already installed packages must also have a working executable path owned by
that package; missing commands and personal PATH shadows stop preflight.

The reviewed recipes are [Material Maker binary](https://github.com/archlinux/aur/blob/material-maker-bin/PKGBUILD),
[butler](https://github.com/archlinux/aur/blob/butler/PKGBUILD), and
[SteamCMD](https://github.com/archlinux/aur/blob/steamcmd/PKGBUILD).
Etcher is available directly from the [CachyOS repository](https://packages.cachyos.org/package/cachyos/x86_64/etcher-bin);
it does not need an AUR build or an unmanaged AppImage download.

## Application-specific checks

- Godot: install export templates matching the exact editor version using its
  Export Template Manager before exporting. The native editor flag does not
  install templates, .NET, Android SDKs, or the Debian-only `--godot-bundle`
  workflow. Test a small export before publishing. See the
  [Godot export guide](https://docs.godotengine.org/en/stable/tutorials/export/exporting_projects.html).
- Material Maker: the binary package carries its own engine/runtime; it does not
  require replacing the workstation's Godot version. Open a sample material and
  test rendering/export on the actual GPU.
- Blender, GIMP, Inkscape, Krita: preserve settings, plugins, projects, and existing
  GPU drivers. Blender's CUDA/HIP/OneAPI choices are hardware-dependent and are
  not automatically installed. Launch from KDE and test your normal file/render
  workflow; package presence alone is not functional qualification.
- Remmina: test the protocols you use and your desktop Secret Service integration.
  `libsecret` supplies the client plugin, not a newly configured credential store.
  Setup does not start a remote-desktop server or replace wallet settings. The
  included plugins follow the [Arch package dependencies](https://archlinux.org/packages/extra/x86_64/remmina/).
- Sunshine/Moonlight: review [LAN firewall policy](CACHYOS.md#optional-workstation-firewall)
  separately. When ready, the current packaged Sunshine unit can be enabled with
  `systemctl --user enable --now app-dev.lizardbyte.app.Sunshine.service`; this
  creates its `sunshine.service` alias. Set credentials locally, pair Moonlight,
  and test video, audio, and input. Package-provided udev access rules are retained;
  Basaltwater does not add the user to `input`, apply capabilities, select GPU
  drivers, or grant capture permissions. See the
  [upstream setup guide](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html).
- Etcher: launch as the normal user and use its normal elevation prompt for a
  deliberately selected device. Setup never flashes a disk, grants blanket disk
  access, or installs an Electron `--no-sandbox` workaround.
- butler: authenticate locally with `butler login` and keep credentials private.
  Uploads remain deliberate project actions; setup does not run `butler push` or
  self-update a package-managed binary. See the [butler manual](https://itch.io/docs/butler/).
- SteamCMD: readiness checks package/executable ownership without running it,
  including `+quit`. The package wrapper's first run initializes user Steam state
  and downloads Valve updates. Run it as the desktop user, complete Steam Guard
  locally, and use the publisher account's own app/depot configuration. No login,
  EULA acceptance, credentials, upload, or Steam library modification is performed
  by Basaltwater. Its 32-bit runtime libraries are checked in enabled repositories;
  current Arch/CachyOS provides them in core, so setup does not enable multilib
  merely by assumption.

New installation behavior is covered by mocked tests. Live AUR build/review,
desktop rendering, removable-media flashing, and authenticated publishing need
their own explicit user tests; previous acceptance of the running workstation
does not establish those new workflows.
