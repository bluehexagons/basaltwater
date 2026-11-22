# Workstation profiles and software

[Topic index](workstations.md)

## Profiles

| Profile | Adds by default |
| --- | --- |
| `workstation_desktop` | Desktop, Firefox ESR, and CLI tools |
| `pc_dev` | Desktop, Firefox ESR, LibreOffice, SMB client packages, Remmina, and CLI tools |
| `workstation_dev` | Desktop, Firefox ESR, and CLI tools including Neovim |
| `agent_workstation` | Developer workstation defaults plus GitHub CLI; explicit provider required |
| `agent_code_vm` | Agent workstation plus Geany, T3 Code web, RDP, pairing, and read-write Git/auth defaults |

The default desktop is XFCE. All workstation profiles use Debian's Firefox ESR
by default. A browser selected with `--browser` becomes the default for the
setup user; repeat the flag to install more than one browser. Graphical editors
are explicit on the general profiles: select Geany or Visual Studio Code with
`--editor geany` or `--editor vscode`. The opinionated `agent_code_vm` profile
defaults to Geany; `--editor vscode` replaces that editor default.

## Desktop software support and flags

Application installation and desktop access are separate choices. Debian's
standard workstation and server profiles accept `--blender`, `--inkscape`,
`--gimp`, `--krita`, `--audacity`, `--musescore` and `--shotcut`, including the headless
`agent_vm`. These applications are opt-in on every profile. To edit interactively,
use a workstation profile or select `--desktop` on a standard server profile;
`--rdp` enables remote access to the shared desktop. Background rendering needs
neither flag. Dedicated Proxmox host and Ubuntu WSL profiles have their own
setup plans; the application choices here target Debian workstations/servers
and the existing CachyOS desktop.

| Software or capability | Debian setup | CachyOS `agent_cachyos` setup |
| --- | --- | --- |
| Blender | `--blender`: Debian APT `blender` package | `--blender`: native `blender` plus `libdecor` |
| Inkscape: SVG icons, logos and vector UI assets | `--inkscape`: APT `inkscape` | `--inkscape`: native package |
| GIMP: raster crops, alpha cleanup and image touch-ups | `--gimp`: APT `gimp` | `--gimp`: native package |
| Krita: painting, sprite and texture touch-ups | `--krita`: APT `krita` | `--krita`: native package |
| Audacity: sound effect trims, fades and level adjustments | `--audacity`: APT `audacity` | `--audacity`: native package |
| MuseScore: score/MIDI inspection and review exports | `--musescore`: APT `musescore3` | `--musescore`: native `musescore` (command `mscore`) |
| Shotcut: short clips, trailers and cutscene edits | `--shotcut`: APT `shotcut` | `--shotcut`: native package |
| Godot | `--godot`: verified upstream stable release; optional `--godot-bundle web` or `--godot-bundle publishing` | `--godot`: native package or existing executable |
| Graphical editor | `--editor geany` (Debian package) or `--editor vscode` (Microsoft APT source) | Use the existing desktop's editor |
| Desktop browser | Repeat `--browser NAME`; first becomes the default | Use the existing desktop's browser |
| LibreOffice | `--office`; `pc_dev` selects it by default | Use the existing desktop's package manager |
| Remmina | Included with `pc_dev`; otherwise `--apt-install remmina` with desired plugins | `--remmina` includes common desktop plugins |
| Media conversion and metadata | `--av-tools`: ImageMagick, FFmpeg/ffprobe, ExifTool | `--av-tools`: native equivalents |
| Audio/MIDI inspection and signal processing | `--audio-tools`: SoX, ALSA utilities, PulseAudio clients; saved APT selections | `--audio-tools`: native SoX, ALSA/MIDI and PulseAudio-compatible clients; verifies existing KDE PipeWire/WirePlumber clients |
| PDF metadata, text extraction and page rendering | `--pdf-tools`: Poppler utilities; saved APT selections | Use existing desktop package management |
| OpenGL diagnostics and tracing | `--gl-tools`: Mesa utilities, apitrace | `--gl-tools`: native equivalents |
| Other native creative apps | Repeat `--apt-install PACKAGE` for packages available in the configured Debian repositories | `--scribus`, `--kdenlive`, `--ardour`, `--lmms`, `--freecad`, `--kicad`, `--obs` |
| Gaming, streaming and publishing | Godot publishing bundle where applicable; other packages are explicit choices | `--gaming`, `--sunshine`, `--moonlight`, `--material-maker`, `--etcher`, `--butler`, `--steamcmd` |
| Additional packages | Repeat `--apt-install PACKAGE` or `--flatpak-install APP_ID` | Use native package management; `--sysadmin-tools` selects the reviewed administration bundle |

Debian's `--blender` uses the version supplied by the configured Debian release.
Setup does not add an upstream Blender download, third-party repository,
backports source, or dedicated Blender update timer. It remains APT-managed;
an already installed Debian package is retained on rerun, with updates following
the host's normal package maintenance. `--flatpak` does not change Blender's
installation source. A failed package installation stops the requested step.

The media editor flags follow the same Debian package-source and rerun
policy. They install without recommended extras to keep optional dependencies
small. Choose `--inkscape --gimp --av-tools` for routine game and website
asset touch-ups; add Krita for painting and sprite work, Audacity for audio
editing, or Shotcut for timeline video edits. Each remains separately opt-in.
These choices do not enable language runtimes or change desktop/RDP settings.

```bash
# Add to your usual graphical VM setup command, retaining its other options
basaltw setup agent_code_vm 10.0.0.25 agent --lan-access --codex \
  --inkscape --gimp --av-tools

# Optional painting, sound and video editors
# --krita --audacity --shotcut
```

Read [media touch-up workflows](desktop-development.md#media-touch-ups-for-games-and-websites)
for editable source formats, repeatable exports and autonomous validation.
Read [music and input development](music-development.md) for MuseScore executable
aliases, isolated score comparisons and deterministic audio tests for Godot.

Add Blender to your usual setup command, retaining its other desired options:

```bash
# Graphical Debian coding VM
basaltw setup agent_code_vm 10.0.0.25 agent --blender --lan-access --codex

# Headless Debian rendering host
basaltw setup agent_vm 10.0.0.24 agent --blender --codex

# Existing CachyOS KDE desktop; run as its desktop user, without sudo
basaltw setup agent_cachyos localhost --blender --codex
```

Preview a command with `--dry-run` before applying it. On Debian, the selection
is saved for subsequent setup/deploy operations; `--no-blender` in a patch
disables the install selection without uninstalling the package. The media
editors likewise support `--no-inkscape`, `--no-gimp`, `--no-krita`,
`--no-audacity`, `--no-musescore` and `--no-shotcut`. CachyOS setup
requires the complete desired selection on each explicit rerun; see
[CachyOS software](cachyos-software.md) for package sources and publishing limits.
Installing a package does not qualify its renderer, add-ons or GPU support.
Use [desktop development workflows](desktop-development.md) for application
discovery, background renders and native editing checks.

## Browser choices

This section covers the browser used by a person at the desktop. It does not
configure an MCP server for coding agents; use `--browser-automation
playwright` for that independent capability.

Supported `--browser` values are `brave`, `firefox`, `librewolf`, `helium`,
`browsh`, and `lynx`:

- Brave and LibreWolf use Debian repositories unless `--flatpak` is selected.
- Firefox uses `firefox-esr` through apt, or the Flathub build with Flatpak.
- Helium installs a pinned upstream Debian package for amd64 or arm64.
- Browsh provides a terminal browser. Setup verifies Firefox is installed and
  selects a pinned upstream Debian package for amd64, arm64, armhf, or i386.
- Lynx installs the Debian terminal browser and does not become a graphical
  default.

A requested browser must finish its package command successfully and pass the
installed-package or application check. Download, verification and missing
browser failures stop setup before default-browser configuration or later browser
selections. Browsh also verifies Firefox before downloading its own package and
repairs that dependency on rerun. Brave retains its Flatpak-to-APT fallback;
Firefox and LibreWolf report a failed Flatpak installation directly. Repair the
reported package or repository problem, then rerun the same desired setup.

For an extrepo-based browser install, setup first refreshes APT normally. If
that refresh fails, it makes one bounded attempt to refresh existing
extrepo-managed definitions for Brave, LibreWolf, and legacy VS Code installs,
then retries APT. The extrepo repair has a 30-second per-source timeout and does not
run on successful reruns. It does not remove or rewrite manually managed files under
`/etc/apt/sources.list` or `/etc/apt/sources.list.d`. If APT still reports an
unrelated third-party source error, repair or disable that source separately
before rerunning setup.

Native Helium and Browsh packages are staged in private, randomly named
temporary directories. Setup compares the download's SHA-256 with the
architecture-specific pin before invoking APT. Reruns keep the installed
package if its version meets or exceeds the pin and upgrade older packages
after verification. A failed download or checksum check leaves an existing
package in place. Refresh the version and all supported architecture digests
in `desktop/browser_steps.py` together when qualifying a new upstream
release. Use the publisher's release metadata or checksum manifest and
confirm the package filename and Debian version for each architecture.
[Helium releases](https://github.com/imputnet/helium-linux/releases) ·
[Browsh releases](https://github.com/browsh-org/browsh/releases)

The first browser in a repeated list is written to the setup user's
`~/.config/mimeapps.list` as the HTTP/HTTPS default. Text-only browsers do not
write a desktop default. XFCE's per-user WebBrowser helper is configured at
the same time, so its launcher does not fall back to the optional
`www-browser` command. A per-user `X-XFCE-Helper` desktop entry is also
created, so XFCE's Preferred Applications dialog and `exo-open --launch
WebBrowser` resolve the selected browser rather than falling back to an
unregistered command. These files are managed defaults: workstation setup
recreates them rather than merging older basaltwater state. The `v2.0.0` setup
is intended for clean workstation builds; rebuild a workstation created by an
older basaltwater release instead of attempting an in-place migration.

On VM and hardware targets, AppArmor is enabled and its distro service reloads
the installed policy without blanket-enforcing package profiles. This preserves
each profile's declared `enforce`, `complain`, or `unconfined` mode. LibreWolf's
own browser sandbox remains responsible for browser confinement. Setup replaces
the package profile with its managed unconfined compatibility profile and
reloads it. The `v2.0.0` setup is intended for clean workstation builds rather
than in-place migration of older basaltwater state.

Debian 13's AppArmor policy also supplies the `unprivileged_userns` transition
used by otherwise-unconfined applications that create a user namespace. Setup
loads and verifies this capability-stripping compatibility profile whenever
the kernel's AppArmor user-namespace restriction is active. This supports
Chromium and Firefox builds downloaded into per-user caches by Playwright and
other browser-automation tools without disabling their sandboxes or granting
an unconfined AppArmor attachment to every executable in a user-writable cache.
If that required profile cannot load, workstation setup stops with a clear
error instead of leaving browser automation silently broken.

## Flatpak and containers

Flatpak requires a desktop-capable host and is unreliable in unprivileged
containers. On a container target, basaltwater warns and falls back to apt for
browsers; other Flatpak bundles may also fall back to apt when Flatpak cannot
be installed. Prefer `--machine vm` when a reproducible Flatpak desktop is
required.
