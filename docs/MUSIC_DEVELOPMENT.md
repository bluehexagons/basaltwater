# Music and audio input development

MuseScore and audio inspection clients are optional development tools. Use them
for licensed score inspection, game music review and deterministic input tests.
They are external applications, not a runtime dependency or a source of assets
that projects may automatically redistribute.

## Select tools when rerunning setup

Retain the complete command used for your existing VM and append these options:

```bash
# Options for a Debian workstation/server setup, including agent_code_vm
# --musescore --audio-tools --pdf-tools --av-tools --audacity

# Example plan only: replace the host/account and retain your existing options
basaltw setup agent_code_vm 10.0.0.25 agent \
  --musescore --audio-tools --pdf-tools --av-tools --audacity --dry-run
```

Save work before an actual setup rerun: Debian desktop setup can log out the
shared session. Updating the Basaltwater source alone does not install packages
or refresh managed skills. Use the upgraded setup/launcher and rerun the desired
selection; do not start setup just to inspect readiness.

| Selection | Debian | Existing CachyOS desktop |
| --- | --- | --- |
| `--musescore` / `--no-musescore` | Native `musescore3`; version supplied by the configured Debian release | Native `musescore`; executable `mscore` |
| `--audio-tools` | Expands to saved `--apt-install` selections: `sox`, `libsox-fmt-all`, `alsa-utils`, `pulseaudio-utils` | Unsupported; select native packages with the desktop's normal package manager |
| `--pdf-tools` | Expands to saved `--apt-install poppler-utils`: `pdfinfo`, `pdftoppm`, `pdftotext` | Unsupported; select native packages with the desktop's normal package manager |
| `--av-tools` | FFmpeg/ffprobe, ImageMagick, ExifTool | Native equivalents |
| `--audacity` | Native audio editor | Native audio editor |

The audio and PDF bundles deduplicate their packages, preserve explicit APT
selections and round-trip through setup/remote arguments and saved state.
They have no `--no-audio-tools` or `--no-pdf-tools` switch: packages are ordinary
APT selections after expansion. Removing a selection does not uninstall packages.
The audio bundle installs
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
command -v pdfinfo pdftoppm pdftotext
```

The manifest includes audio utilities and a canonical `musescore` tool/desktop
entry resolving versioned aliases, plus the PDF inspection tools.
Project `required_tools` and recipe
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

With `--pdf-tools` installed, inspect and render a bounded page copy:

```bash
timeout 15 pdfinfo /absolute/task/review.pdf
timeout 30 pdftoppm -f 1 -l 1 -scale-to 1600 -singlefile -png \
  /absolute/task/review.pdf /absolute/task/page
```

View `page.png` and compare notation against source events. Limit page ranges,
dimensions and runtime for large files; keep the PDF and generated images in
the task's artifact directory. Metadata and extracted text alone cannot qualify
score layout.

For GUI work use the manifest launch vector and an absolute task-copy path.
Follow [desktop controls](DESKTOP_AUTOMATION.md) and the managed desktop skill;
inspect the actual document, save MSCZ, reopen and verify separate exports.
Offscreen conversion does not validate GUI responsiveness, perceived audio,
musical correctness or the product's score layout.

### Observed Debian desktop caveats

LibreTabs exercised Debian's `musescore3` 3.2.3+dfsg2-19 on the managed XFCE
desktop on 2026-10-07. Offscreen PDF and MusicXML conversion worked for its
30 bundled MIDI files. GUI startup with `--no-midi --no-synthesizer` exited
with signal 11 after the splash. The same task copy opened successfully with
`--no-midi` alone. Keep `--no-synthesizer` confined to the tested converter
workflow for this version; omit it from interactive launches. Initializing the
synthesizer does not establish audible playback, and this workaround does not
require changing host routing or enabling RDP audio. Other versions need their
own check before adopting it.

`desktop exec --wait-window MuseScore` initially matched the startup splash.
Add `--exclude-title Startup --stable-seconds 1` to skip that known title and
wait for stable matching windows. Use its launch token with `desktop launch-status`,
then inspect the document window before treating the editor as ready.
Cancelling the first-run wizard
exited the application and stored `firstStart=false` in the isolated profile;
observe process exit before relaunching. Qt score controls exposed only an
AT-SPI application root in this check, while the GTK save dialog exposed its
filename text and Save button. Prefer those references when available and use
fresh screenshots for the unexposed score controls.

PDF page inspection tools were absent on this VM; `--pdf-tools` now offers them
as an optional setup selection. After the owner's subsequent setup rerun on
2026-10-07, Poppler 25.03.0 was discovered through the agent manifest. `pdfinfo`,
`pdftotext` and bounded `pdftoppm` rendering passed for all 30 LibreTabs review
PDFs, each with one page. First-page PNGs were 1131×1600 and original PDF/MIDI
bytes remained unchanged. A malformed PDF with a valid-looking header failed
parsing, as expected. This qualifies those command workflows on the tested
Debian VM; executable discovery alone remains insufficient evidence elsewhere.

A reproducible, test-owned virtual capture route
for native Godot remains a useful follow-up and is not provided by `--audio-tools`.
MuseScore's own PNG export can provide score images when a PDF rasterizer is
unavailable. Keep hardware capture, device latency and human listening checks
separate from these software-only workflows.

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
