# Music and audio input development

MuseScore and audio inspection clients are optional development tools. Use them
for licensed score inspection, game music review and deterministic input tests.
They are external applications, not a runtime dependency or a source of assets
that projects may automatically redistribute.

## Select tools when rerunning setup

Retain the complete command used for your existing VM and append these options:

```bash
# Options for a Debian workstation/server setup, including agent_code_vm
# --musescore --audio-tools --av-tools --audacity

# Example plan only: replace the host/account and retain your existing options
basaltw setup agent_code_vm 10.0.0.25 agent \
  --musescore --audio-tools --av-tools --audacity --dry-run
```

Save work before an actual setup rerun: Debian desktop setup can log out the
shared session. Updating the Basaltwater source alone does not install packages
or refresh managed skills. Use the upgraded setup/launcher and rerun the desired
selection; do not start setup just to inspect readiness.

| Selection | Debian | Existing CachyOS desktop |
| --- | --- | --- |
| `--musescore` / `--no-musescore` | Native `musescore3`; version supplied by the configured Debian release | Native `musescore`; executable `mscore` |
| `--audio-tools` | Expands to saved `--apt-install` selections: `sox`, `libsox-fmt-all`, `alsa-utils`, `pulseaudio-utils` | Unsupported; select native packages with the desktop's normal package manager |
| `--av-tools` | FFmpeg/ffprobe, ImageMagick, ExifTool | Native equivalents |
| `--audacity` | Native audio editor | Native audio editor |

The audio bundle deduplicates its own packages, preserves explicit APT
selections and round-trips through setup/remote arguments and saved state.
It has no `--no-audio-tools` switch: packages are ordinary APT selections after
expansion. Removing a selection does not uninstall packages. It installs
clients and processing tools, not an audio daemon, virtual device, default
routing policy, MIDI permission change or SoundFont bundle.

MuseScore follows the native package policy even with `--flatpak`; failures stop
the requested setup step. `--no-musescore` disables the saved install selection
without uninstalling it. Headless profiles remain headless. Debian ships
[MuseScore 3](https://packages.debian.org/trixie/musescore3), whose
[executables](https://packages.debian.org/trixie/amd64/musescore3/filelist) are
`musescore3` and `mscore3`; Arch/CachyOS's
[native package](https://archlinux.org/packages/extra/x86_64/musescore/files/)
uses `mscore`. Versions and scripting capabilities can differ.

## Discover without starting applications

```bash
basaltw agent manifest --json
basaltw desktop status
basaltw desktop doctor
command -v sox soxi arecord aplay amidi aconnect pactl paplay parecord
```

The manifest includes audio utilities and a canonical `musescore` tool/desktop
entry resolving versioned aliases. Project `required_tools` and recipe
`requires` may use `musescore` regardless of the distro executable name.
Use `desktop_applications.musescore.executable` or `launch_argv` when launching:
the manifest's canonical key does not create a shell command. Versioned 4 and 3
aliases take precedence over generic names. Discovery never executes editors,
checks login files or claims devices/application readiness.

## Inspect score task copies

Copy legally usable source MIDI to an ignored task directory. Keep immutable
source bytes, parsed events and their checksums separate from imported notation.
On Debian, an isolated converter query can use:

```bash
QT_QPA_PLATFORM=offscreen musescore3 --version
QT_QPA_PLATFORM=offscreen musescore3 --help
# First verify the installed help supports these MuseScore 3 flags:
QT_QPA_PLATFORM=offscreen musescore3 -c /absolute/task/profile \
  --no-midi --no-synthesizer -o /absolute/task/review.pdf \
  /absolute/task/input.mid
```

See the [Debian CLI reference](https://manpages.debian.org/trixie/musescore3/mscore3.1.en.html).
Do not use factory-settings flags that delete the user's preferences. Set a
timeout for converter jobs, inspect logs and verify a nonempty PDF before
viewing it. A PDF's notation is a derived interpretation: compare pitch,
tempo, meter, voices, quantization, ties and rests against structured source
data and the consuming Godot project.

For GUI work use the manifest launch vector and an absolute task-copy path.
Follow [desktop controls](DESKTOP_AUTOMATION.md) and the managed desktop skill;
inspect the actual document, save MSCZ, reopen and verify separate exports.
Offscreen conversion does not validate GUI responsiveness, perceived audio,
musical correctness or the product's score layout.

## Test input without physical devices

Generate a short project-authored mono PCM WAV with documented sample rate,
amplitude, expected pitches and silences. Include quiet sustained tones,
harmonic-rich decays, noise-only segments and competing playback. Keep generated
WAV files and reports in ignored artifact directories; tests should use an
injected signal/clock or an explicitly selected test capture source.

Use `soxi` or `ffprobe` for structure, decoded measurements for levels and
clipping, and `pactl list short sources` / `arecord -l` for device discovery.
A suspended null-sink monitor is not a microphone; installed ALSA/MIDI clients
do not imply a physical device exists. Do not alter default sources, load
loopback modules or connect MIDI devices as a discovery step.

In sessions with T3 preview tools, try `preview_status` then `preview_open`
first. Managed Chromium file-backed fake microphone input is an explicit
isolated test option only when the preferred browser is unavailable or that
separate browser task is requested. Follow [browser policy](BROWSER_AUTOMATION.md).
For native Godot capture tests, use an explicit temporary test route and restore
only the resources owned by that test. Avoid changing the shared user's audio.

Synthetic fixtures test repeatable signal processing, thresholds, silence
handling and source isolation. Speaker-to-microphone capture, room acoustics,
operating-system gain, phone/browser processing, acoustic instruments and
latency still require real hardware. Record engine/browser versions, fixture
hash, selected input profile, sensitivity, mute state and observed pitch events.
