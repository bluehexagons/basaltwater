# Audio and video analysis and production

Status: Debian and CachyOS offline analysis and bounded CPU production
implemented; native qualification and remaining platform boundaries are recorded in the
[Linux media guide](../workstations/media-analysis.md) and
[coverage map](testing-coverage.md). Intel Arc VA-API H.264 encode/decode and
GPU scaling pass the opt-in qualification runner, including decoded-content checks.
Kdenlive CPU and VA-API exports pass on the native integrated Arc target;
interactive Movit is unavailable in the tested editor build. NVIDIA and Windows
milestones remain separate; optional speech recognition is
deferred without a demonstrated need. The [CachyOS handoff](audio-video-cachyos-handoff.md) gives the next agent
the setup, baseline and production acceptance sequence. This plan extends
[multimedia bundles issue #25](https://github.com/bluehexagons/basaltwater/issues/25)
and complements [native QA evidence](../workstations/native-qa-evidence.md)
and [Windows game testing](windows-game-testing.md).

The [CachyOS editor/GPU guide](../workstations/cachyos-media.md) supplies the
qualified Kdenlive linked-media/proxy/title round trip and CPU/VA-API export recipes,
plus a separate native MLT Movit effect check and explicit CPU timeline fallback.
Current Arc evidence is integrated
Meteor Lake graphics; discrete Arc and NVIDIA still need their own target runs.

## Scope and existing capabilities

| Platform | Intended use | Starting point and gap |
| --- | --- | --- |
| CachyOS desktop with discrete GPU | Human and agent collaboration, including demanding video production | Opt-in `--av-tools`, Kdenlive, Shotcut, Audacity, Ardour, LMMS and OBS selections and [desktop media guidance](../../common/agent_skills/basaltwater-cachyos-desktop/references/media.md) exist. Qualify actual editing, exchange and GPU workflows. |
| Debian agent host | File analysis and modest production within measured CPU/software-rendering limits | `--av-tools`, `--audio-tools`, Audacity and Shotcut are opt-in; headless profiles can select applications. The [media workflow](../../common/agent_skills/basaltwater-desktop/references/media.md) covers basic edits. Establish bounded CLI workflows and practical limits. |
| Native Windows | Simple file analysis for debugging | Define discovery and a supported installation route for FFmpeg/ffprobe; no existing cross-platform media analysis contract is assumed. Live process audio is separate [planned work](windows-game-testing.md). |

The existing CachyOS native audio analyzer is a useful reference, not yet a
portable API: it accepts bounded WAV input, uses Linux paths and is tied to the
native desktop evidence workflow. It measures levels, loudness and silence;
capture is limited to qualified native PipeWire stereo streams. Existing
discovery and package selections do not establish codec, GPU, editor or
physical audio readiness. See the [evidence guide](../workstations/native-qa-evidence.md)
and [music tooling](../workstations/music-development.md).

Native Windows has a separate PowerShell CLI; Linux `agent` commands are not
available there and Python is an optional selection. Linux manifest discovery
already includes FFmpeg/ffprobe. A common result contract therefore does not
mean one existing CLI or runtime can be used unchanged on all three platforms.

## Related GitHub issues

- [#25: Multimedia packages](https://github.com/bluehexagons/basaltwater/issues/25)
  is open. Individual application selections cover part of the request; the
  proposed bundles, package mappings and defaults still need a decision. This
  plan favors opt-in analysis and defers broader bundles to a demonstrated need.
- [#106: CachyOS software](https://github.com/bluehexagons/basaltwater/issues/106)
  is open. Its creative-application launches on a target GPU overlap the
  qualification here; coordinate evidence while keeping its other software,
  connections and publishing acceptance separate.
- [#2: Audio not setup properly](https://github.com/bluehexagons/basaltwater/issues/2)
  is closed and records a historical Debian XRDP audio-output failure. It is
  relevant when qualifying interactive remote listening, not a dependency of
  offline file analysis; its closed status alone does not qualify current audio
  playback on a particular host or client.

## Tool choices to validate

- Use FFmpeg/ffprobe as the common candidate for metadata, decoding,
  frame extraction, audio measurements and simple edits. Their [probe](https://ffmpeg.org/ffprobe.html)
  and [filter](https://www.ffmpeg.org/ffmpeg-filters.html) interfaces provide
  machine-readable metadata and signal analysis. Debian Trixie and Arch provide
  [FFmpeg](https://packages.debian.org/trixie/ffmpeg) and
  [FFmpeg packages](https://archlinux.org/packages/extra/x86_64/ffmpeg/),
  respectively; upstream [links to Windows builds](https://www.ffmpeg.org/download.html).
  Configured repositories, versions, builds, codecs and filters can differ;
  inspect actual capabilities rather than assuming parity.
  On Windows, evaluate the exact-source WinGet `Gyan.FFmpeg` package as an
  opt-in candidate: its [package manifest](https://github.com/microsoft/winget-pkgs/blob/master/manifests/g/Gyan/FFmpeg/8.0.1/Gyan.FFmpeg.installer.yaml)
  declares both executables. This is a third-party build, not an FFmpeg project
  binary; qualify the current manifest, architecture, license, PATH discovery,
  installation and update behavior before adopting it. A preinstalled tool
  should also be usable without triggering setup.
- Keep Kdenlive as the primary CachyOS timeline candidate, with Shotcut as an
  existing alternative and Audacity/Ardour for audio work. Arch packages
  [Kdenlive](https://archlinux.org/packages/extra/x86_64/kdenlive/),
  [Shotcut](https://archlinux.org/packages/extra/x86_64/shotcut/),
  [Audacity](https://archlinux.org/packages/extra/x86_64/audacity/) and
  [OBS](https://archlinux.org/packages/extra/x86_64/obs-studio/); verify the
  configured CachyOS repositories. Avoid committing to more editors before
  testing the current selections. OBS is an opt-in recording tool, not a
  prerequisite for file analysis. Kdenlive's [rendering documentation](https://docs.kdenlive.org/en/exporting/render.html)
  describes hardware presets as experimental; hardware encoding, decoding and
  timeline effects are separate capabilities and each needs its own evidence.
- Defer adding alternative packages until a workflow needs them. For example,
  [MediaInfo](https://archlinux.org/packages/extra/x86_64/mediainfo/) adds
  another metadata view, [HandBrake](https://handbrake.fr/docs/en/latest/technical/official-presets.html)
  offers graphical/CLI conversion presets, and [LosslessCut](https://github.com/mifi/lossless-cut)
  offers a GUI for fast cuts. They overlap the first FFmpeg/editor workflows;
  evaluate package source and maintenance if a gap emerges. LosslessCut's
  upstream Linux downloads are separate from distro packages. A player such as
  MPV can assist human review but does not establish agent analysis capability.
- Evaluate local speech-to-text as an optional later capability:
  [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) offer CPU paths;
  compare installation, model licensing, memory, speed, timestamps and accuracy
  before choosing. Model downloads and any remote service must be explicit;
  generated transcripts and captions need review. This is speech recognition,
  not text-to-speech; text-to-speech is not currently planned and may not be
  added. No transcription engine or GPU backend is selected by this plan.

## Initial implementation decisions

- CachyOS now accepts opt-in `--audio-tools` for native SoX, ALSA/MIDI and
  PulseAudio-compatible clients, with presence checks for the default KDE
  PipeWire/WirePlumber tools. Preserve the owner's audio services and routing;
  package selection and mocked tests do not establish native audio readiness.
  See [music tooling](../workstations/music-development.md).
- Future Windows tooling should start with opt-in FFmpeg/ffprobe installation
  and discovery (including preinstalled tools), followed by a bounded native
  file-analysis adapter using the versioned result contract. Qualify executable
  provenance, updates, private storage and process cancellation on Windows;
  the Debian analyzer's Linux locking/resource limits cannot be reused unchanged.
  Add device/application-audio diagnostics only for a demonstrated debugging
  workflow. No Windows implementation or additional Python dependency is added
  by the CachyOS audio-tools selection.

- Start with debugging clips and game audio as the foundation, while allowing
  other media within the same contract. Build editing and delivery workflows on
  that foundation. Prioritize local files,
  stream metadata, bounded timestamped frame samples and audio measurements.
  Provide individual images plus a labelled contact sheet for an agent or human
  to inspect; include timestamps and source identity for each image. These are
  observations, not automatic semantic understanding of a clip.
- Keep file analysis independent of a running desktop, capture session or GPU.
  Build on existing discovery and evidence concepts rather than transplanting
  Linux-only `/usr/bin`, `/tmp`, UID and boot-clock assumptions. Define native
  Windows discovery and CLI integration separately; decide whether an optional
  Python runtime or a native adapter is the smallest maintainable implementation
  after a prototype. Do not silently add Python as a Windows prerequisite.
- Begin with common self-contained audio/video files and explicit selected
  streams; report other inputs as unsupported until qualified. Record missing
  audio/video separately from failures, and audio/video stream start times,
  time bases and actual decoded frame timestamps when available. Requested seek
  time is not necessarily observed frame time: [ffprobe documents](https://ffmpeg.org/ffprobe.html)
  inaccurate interval seeking. Cover variable frame rate, rotation, offset audio
  and unknown duration; state when timing cannot be established. Treat HDR,
  color conversion and presentation fidelity as separate qualification work.
- Recommend opt-in analysis tools rather than one all-media bundle. Preserve
  the existing CachyOS capture and WAV analysis API and limits. For larger video,
  select a measured snapshot or stable-input strategy rather than copying its
  128 MiB WAV limit blindly; identify changed inputs and unavailable disk space.
  Define portable private output storage and retention without assuming POSIX
  permissions or persisting full source copies for every operation.

## Milestones

1. **Inventory and baseline.** Define representative debugging clips, audio
   fixtures and a small editable project, including silent, no-audio,
   variable-frame-rate and malformed cases. Inspect the existing manifest,
   setup paths, native evidence code and Windows conventions; record installed
   versions, filters, codecs, package sources and resource measurements on each
   target. Decide minimal opt-in package selections and Windows executable
   provenance/update ownership; retain individual selections and resolve the
   bundle semantics in issue #25. Do not add a default all-media bundle.
2. **Portable file analysis.** Design a shared, versioned machine-readable
   result and CLI/discovery route that fits each platform's existing entrypoints.
   Start with metadata, stream selection, bounded timestamped frames/contact
   sheets and audio levels/silence, with explicit missing, unsupported and
   incomplete results. Reuse measurement logic where practical without
   weakening the native capture contract. Include timestamps and source hashes,
   tool versions, settings and errors; do not imply that metadata or a few
   frames prove quality or sync.
3. **Basic agent production on Debian and CachyOS.** Supply documented, bounded
   recipes for audio trim/fade/level adjustment, video trim/scale/concatenation,
   and short delivery exports, building on the analysis foundation. Preserve
   source files and editable project/media links;
   verify output streams, duration, boundaries and representative decoded media.
   Document stream-copy/keyframe cut limitations versus re-encoding and avoid
   describing a cut as frame-accurate without checking the decoded result.
   Measure CPU, memory, temporary disk and elapsed time on a stated Debian host;
   publish tested envelopes rather than promising real-time performance or a
   universal limit. Defer expensive work to CachyOS when the budget is exceeded.
4. **CachyOS production qualification.** Exercise a human/agent Kdenlive
   project with linked media, proxies, titles, audio and a short render; reopen
   the project and validate the export in a consuming player. Develop first on
   a lower-end Intel Arc system, then qualify and tune on a higher-end NVIDIA
   system. Keep results and limitations separate for each. Compare CPU and
   available discrete-GPU encode/decode on the actual GPU, driver, codec and
   editor version; separately exercise relevant accelerated timeline effects.
   Record what ran on the GPU; fall back explicitly when a feature is absent.
   Qualify OBS capture and audio separately if requested;
   installing a package does not establish portal, device or capture readiness.
5. **Optional speech recognition and richer analysis.** If a use case justifies
   it after the baseline, benchmark a small local model on representative speech
   and silence. Define opt-in model storage, offline behavior, output provenance
   and limits. Add transcript/caption or scene-change features only if they
   demonstrably improve the use cases; evaluate timestamp accuracy and
   hallucinations against reviewed fixtures. Human listening and visual review
   remain necessary for production. Text-to-speech is outside the current plan.

## Acceptance and operating boundaries

- Each supported platform passes native tests for discovery, missing tools and
  paths with spaces and Unicode, plus the same synthetic metadata, frame and
  audio cases.
  Compare measured durations and timestamps within stated tolerances; report
  absent streams and unsupported formats distinctly. Use mocked system calls
  and temporary files in automated tests, and retain separate native evidence.
- Bound input bytes, duration, decoded pixels, output size, process time,
  concurrency and temporary disk; set defaults from baseline measurements and
  expose clear overrides. Treat media as untrusted: allow local regular inputs,
  restrict network/protocol access, avoid shell interpolation, prevent output
  overwrite and fail visibly on truncated or partial results. Reject playlists
  and formats with external references until their access can be confined;
  a protocol whitelist alone does not confine local file reads. Qualify cleanup
  and cancellation on all three platforms. These limits are not a decoder sandbox;
  document the isolation and update policy for processing untrusted media.
- Keep raw media, derived frames, transcripts and logs private by default;
  record provenance and use synthetic fixtures in Git. Do not silently upload,
  record devices, alter audio routing, install drivers or publish outputs.
  Follow existing capture consent and reviewed-export boundaries.
- Update setup/manifest, operator guides and managed skills as capabilities land.
  Record actual native results and remaining limits in the
  [coverage map](testing-coverage.md); unit tests, package installation and
  software rendering alone do not qualify a physical GPU, audio or editor UI.
