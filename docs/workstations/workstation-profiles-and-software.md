# Workstation profiles and software

[Topic index](workstations.md)

## Profiles

| Profile | Adds by default |
| --- | --- |
| `workstation_desktop` | Desktop, Firefox ESR, and CLI tools |
| `pc_dev` | Desktop, Firefox ESR, LibreOffice, SMB client packages, Remmina, and CLI tools |
| `workstation_dev` | Desktop, Firefox ESR, and CLI tools including Vim |
| `agent_workstation` | Developer workstation defaults plus GitHub CLI; explicit provider required |
| `agent_code_vm` | Agent workstation plus T3 Code web, RDP, pairing, and read-write Git/auth defaults |

The default desktop is XFCE. All workstation profiles use Debian's Firefox ESR
by default. A browser selected with `--browser` becomes the default for the
setup user; repeat the flag to install more than one browser. Graphical editors
are optional on Debian coding and workstation profiles. Select Geany or Visual Studio Code with
`--editor geany` or `--editor vscode`.

## Terminal Vim

The Debian CLI and control-plane bundles install `vim` with a small configuration
inspired by [vim-sensible](https://github.com/tpope/vim-sensible) and the
[Basic edition of amix/vimrc](https://github.com/amix/vimrc#how-to-install-the-basic-version).
It uses Vim's built-in syntax highlighting, filetype indentation, line numbers,
and Tab completion. You can switch buffers with unsaved edits and use
`Ctrl-^` to return to the alternate buffer. Long lines wrap at word boundaries
and keep their indentation on screen. Use `:set list` to mark the start of
each tab with `>`, trailing spaces with `~`, and nonbreaking spaces with `+`.
Use `:set nolist` to hide the markers.

Use `/name` to find any capitalization or `/Name` to match case. The default
`Ctrl-L` mapping clears the highlights and redraws. For project-wide results, run
`:grep name .` from the project directory, then `:copen` to see the matches.
Use `:cnext` and `:cprevious` to jump between them. Vim runs `rg --no-config`
so quickfix can read the locations. Project ignore files still apply. To set
a different search command for Vim, change `grepprg` in your vimrc.

Put personal overrides in `~/.vimrc`, for example `set nonumber`. On a fresh
setup, Debian's `/etc/vim/vimrc.local` loads the managed
`/etc/vim/basaltwater.vim`. Put site-wide overrides below its `source` line.
They also apply to accounts without a personal vimrc. To see where a setting
came from, use `:verbose set scrolloff?`, substituting the option you want to inspect.
To adopt the defaults in an existing site vimrc, add
`source /etc/vim/basaltwater.vim` before your site settings.
Setup reruns leave previously installed Neovim and personal editor files in place.

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

Use `--browser` to select one or more browsers. Setup uses the first
selection as the primary browser.

- LibreWolf uses its extrepo repository or the Flathub build with `--flatpak`.
- Firefox uses `firefox-esr` through apt, or the Flathub build with Flatpak.
- Chromium uses Debian's `chromium` package, including when `--flatpak` is selected.
- Lynx installs the Debian terminal browser and does not become a graphical
  default.

For a new Firefox or LibreWolf Flatpak install, setup prepares Flatpak and
the system Flathub remote first. If the app is already installed system-wide,
setup can repair its browser configuration without contacting Flathub.

### Content blocking

| Browser | What setup installs | How to check |
| --- | --- | --- |
| Native Firefox ESR | Debian's `webext-ublock-origin-firefox` package includes full uBlock Origin. APT supplies updates. | Open `about:addons` and look for enabled uBlock Origin. |
| Firefox Flatpak | A system-wide Firefox policy installs full uBlock Origin from Mozilla Add-ons on the next online launch. Users can disable it. | Check `about:policies` for `ExtensionSettings` and `about:addons` for uBlock Origin. |
| Debian Chromium | Chromium fetches uBlock Origin Lite from the Chrome Web Store on the next online launch. Lite is the publisher's Manifest V3 edition. Users can disable or remove it. | Open `chrome://extensions` and look for enabled uBlock Origin Lite. |
| LibreWolf | LibreWolf includes uBlock Origin and its own filter-list defaults. | Open `about:addons`. |
| Lynx | This text browser has no WebExtension support. | N/A |

Allow the filter lists to initialize on first launch before testing blocking.
Run `basaltw desktop check-blocking --browser firefox --json` as the desktop
user. Choose `chromium` or `librewolf` to check those browsers, and add
`--flatpak` for system Flathub Firefox or LibreWolf. A disposable profile
loads a benign control script and a matching ad script from loopback. The
check allows 90 seconds for first-launch initialization and reports the
observed result. Online extension installation still needs store access.
Setup also registers missing blockers when rerun on an existing browser. If
an administrator already supplies the Firefox Flatpak `policies.json` or a
different Chromium registration for the same extension ID, setup reports the
path and preserves the file. To include uBlock Origin in an existing Firefox
policy, add `uBlock0@raymondhill.net` under `policies.ExtensionSettings` with
`installation_mode` set to `normal_installed` and `install_url` set to
`https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi`.
[Firefox extension policy](https://firefox-admin-docs.mozilla.org/reference/policies/extensionsettings/) ·
[uBlock Origin Lite](https://github.com/uBlockOrigin/uBOL-home)

### Saved selections and defaults

Saved setups drop Brave, Browsh and Helium and keep the remaining browsers
in order. If every selection was retired, setup uses the profile default
(Firefox on Debian workstations). Rerunning setup preserves previously
installed packages and personal browser data.

A requested browser must finish its package command successfully and pass the
installed-package or application check. A failure stops setup before it changes
the default or installs later selections. Repair the reported package or
repository problem, then rerun the same desired setup.

For an extrepo-based browser install, setup first refreshes APT normally. If
that refresh fails, it makes one bounded attempt to refresh existing
extrepo-managed definitions for LibreWolf and legacy VS Code installs,
then retries APT. Each extrepo lookup has a 30-second timeout. If enabling
LibreWolf times out and Debian's `extrepo-offline-data` package is installed,
setup retries with that package's repository definition. Setup requires a
successful refresh of the selected repository before installing LibreWolf.
An already installed browser skips repository setup on rerun.

The first browser in a repeated list is written to the setup user's
`~/.config/mimeapps.list` as the HTTP/HTTPS default. Text-only browsers do not
write a desktop default. XFCE's per-user WebBrowser helper is configured at
the same time, so its launcher does not fall back to the optional
`www-browser` command. A per-user `X-XFCE-Helper` desktop entry is also
created, so XFCE's Preferred Applications dialog and `exo-open --launch
WebBrowser` resolve the selected browser rather than falling back to an
unregistered command. The helper launches the selected native executable or
system Flatpak. Changing browsers preserves other MIME associations, XFCE
helper selections and comments in both preference files.

On VM and hardware targets, AppArmor is enabled and its distro service reloads
the installed policy without blanket-enforcing package profiles. This preserves
each profile's declared `enforce`, `complain`, or `unconfined` mode. The native
LibreWolf package supplies `/etc/apparmor.d/librewolf` and handles its
installation. Browser setup preserves the package profile and administrator
edits. For older installations with a malformed profile, review the installed
conffile against the current LibreWolf package.

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
