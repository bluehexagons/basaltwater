# Audio/video handoff to a CachyOS agent

Continue the [AV plan](audio-video-support.md) on an existing CachyOS KDE desktop.
The Debian/CachyOS file-analysis and modest CPU-production foundation is implemented.
Intel integrated Arc file analysis, VA-API and a Kdenlive project round trip now
have native evidence; use the [editor/GPU recipe](../workstations/cachyos-media.md).
The next target is discrete-GPU and NVIDIA qualification. Keep results
for each target separate. Windows implementation and speech recognition remain
future work; text-to-speech is outside this project.

## Delivered implementation and observed evidence

The branch `t3/av-support-1` delivers `c764264f` (bounded Debian analyzer),
`cc8a562a` (timing, rotation, cancellation and CPU qualification) and `a5991bd3`
(CachyOS `--audio-tools`). Review commits `da7f50f1` and `bad8274d` fix sampling
against the selected video's packet span, covering low-rate, shorter and delayed video,
reject incomplete per-channel audio measurements, and test the actual
refresh-replay path and presence-only doctor behavior. Start from `main`
containing those commits; record `git rev-parse HEAD` and worktree status
before running native checks, rather than assuming an earlier SHA was tested.

- `lib/media_cli.py` implements `python3 -m lib.media_cli` and the wheel's
  `basaltw-media` entrypoint. It is qualified on Debian and CachyOS, uses Linux process limits
  and directory locking, and consumes only bounded immutable local input bytes.
- `scripts/qualify_media.py` creates trusted synthetic fixtures and exports,
  checks the versioned analysis contract, and exercises real cancellation.
  It passed on Debian 13 / FFmpeg 7.1.5 with the review's 18 fixtures, including
  the three new video timing cases. This is separate from mocked unit tests.
- The reviewed implementation passed full local `make -j2 check-ci` at
  `bad8274d` (9,663 tests, six skipped; 82% coverage), including documentation
  and installed-wheel checks. The final handoff-only documentation update
  does not change that tested implementation.
- A 30-second 720p CPU encode/analyze workload passed on Debian; see the
  [measured envelope](../workstations/media-analysis.md).
- At `f237ecf1`, native CachyOS / FFmpeg 9.0.2 passes all 18 fixture outcomes
  and separate VA-API encode/decode/scaling exports. Kdenlive 26.08.3 / MLT
  7.42.0 saves and reopens linked audio/video, a generated proxy and an editable
  title; its five-second CPU render passes decoded analysis and muted MPV review.
  A separate MLT Movit desaturation export has grayscale output and advancing
  same-process DRM render counters. These observations use integrated Meteor
  Lake Arc / iHD 26.3.5, not a discrete Arc board. No Kdenlive interactive GPU
  timeline, physical listening/capture, NVIDIA or fresh package installation is
  established. The read-only workstation doctor has zero failed observations;
  this does not turn deferred capabilities into successes.
- The GPU runner now checks three decoded pictures against the source and
  finite audio RMS within 1 dB; all four native exports pass. Kdenlive's separate
  `VAAPI H264` preset also passes with advancing render-child video-engine
  counters, decoded comparisons against the CPU baseline and muted MPV review.
  Preserve its original MP4 and use the documented lossless faststart remux
  before pipe-based analysis. Interactive Movit is disabled in this editor
  build; retain the explicit CPU timeline fallback and separate MLT effect test.
- A regenerated 320×180 proxy passes full video/audio decoding and picture
  comparison against the scaled source. The project reopens with its original
  and proxy links; full-resolution CPU delivery has identical decoded stream
  hashes to the earlier baseline. Merely changing proxy width can reuse the
  old cache; use the guide's task-owned regeneration steps and verify dimensions.

Raw evidence is private and is not included in Git. On the receiving machine,
create a new private evidence directory outside the checkout; record the exact
revision, dirty state, commands, exit status, versions and artifact paths there.
Only sanitized reusable results belong in the
[coverage map](testing-coverage.md). No work is automatically scheduled or
running on CachyOS as part of this handoff.

## 1. Establish the native package and session baseline

Read `AGENTS.md`, the [contributor guide](../agents/contributing/README.md),
[music tooling](../workstations/music-development.md) and the installed CachyOS
workstation/desktop skills. Work as the existing desktop user. Preserve the
owner's complete setup selection and audio configuration.

Use code containing this change before selecting the new option: an older
installed launcher may reject `refresh --audio-tools`. After upgrading through
the machine's existing source/channel workflow, preview the saved selection:

```bash
basaltw refresh --audio-tools --av-tools --dry-run
```

For a saved managed installation, the corresponding refresh without `--dry-run`
adds the flags while retaining the other saved options. Refresh also upgrades
the managed channel; verify that the channel contains the merged implementation.
If no successful setup was saved, preview the complete existing
`basaltw setup agent_cachyos localhost USER ...` command with the two flags instead.
Retain provider, T3, SSH and other selections; a minimal example is not a
replacement for the owner's existing setup. After a successful run, verify a
subsequent `refresh --dry-run` retains both flags without restating them.

Verify the following separately, retaining private before/after observations:

| Check | Expected result |
| --- | --- |
| Package selection | Native `sox`, `alsa-utils`, `libpulse`; `--av-tools` supplies the native FFmpeg/ImageMagick/ExifTool selection. Only missing requested packages are installed. |
| Existing KDE clients | `pw-cli`, `pw-dump`, `pw-top`, `pw-play`, `pw-record`, `wpctl` resolve from the existing PipeWire/WirePlumber installation. Missing tools are reported, not repaired by replacing audio services. |
| Saved state and diagnostics | `basaltw local cachyos-doctor --json` reports the selected packages/tools; its zero exit status alone does not mean every capability is available. Inspect individual states. |
| Discovery | `basaltw agent manifest --json` resolves the tools without launching editors or performing capture/playback. |
| Session preservation | Existing user audio services and routing are retained; no new default device, MIDI connection or virtual loopback is created. |

Use the desktop's normal full-system update workflow if its package database is
stale; setup does not synchronize repositories or perform an OS upgrade. Do not
delete saved state or uninstall utilities to manufacture a fresh-install test.
Use mocks or a disposable target for missing-tool/failure cases.

## 2. Qualify offline file analysis and CPU exports

Use the project's virtual environment for development checks; do not install
Python dependencies into system Python. From a checkout at the recorded revision:

```bash
python3 run_tests.py test_agent_media test_agent_music_tools test_agent_cachyos test_cachyos_doctor test_cachyos_refresh
python3 scripts/qualify_media.py --output /absolute/private/new-cachyos-media-run
```

The second command invokes real FFmpeg and Linux process tools and creates a
new evidence directory. It is opt-in native qualification, not an ordinary unit
test. It needs the installed encoders/filters used by the fixtures (including
libx264, AAC, FLAC, libvorbis and libvpx-vp9); inspect actual tool versions and
capabilities and record failures rather than silently substituting formats.
Run without sudo. Follow the [analysis guide](../workstations/media-analysis.md)
for limits, private storage and complete-manifest requirements.

Confirm all 18 fixture outcomes, packet/decoded timing and rotation checks,
explicit malformed/playlist/truncated refusals, timeout and cancellation cleanup,
audio fades and decoded export boundaries. Exercise the installed entrypoint
outside the checkout if installing a wheel; a source launcher does not imply a
`basaltw-media` executable exists. Record elapsed time, child CPU/RSS and retained
bytes with the script's stated measurement limitations. File analysis passing
does not qualify devices, perceived sound, HDR or accelerated rendering.

Keep the existing `desktop/audio_analysis.py` native WAV evidence contract and
capture consent unchanged. The Linux file analyzer is now a supported CachyOS
route for this bounded contract; do not extend Windows support from Linux tests.

## 3. Qualify editor projects and acceleration

Select existing `--kdenlive` (primary), `--shotcut` or `--audacity` options only
as needed for the workflow, preserving the complete setup command. Use an
isolated task project/profile and synthetic linked source media. Follow the
[native media guidance](../../common/agent_skills/basaltwater-cachyos-desktop/references/media.md)
and current desktop consent/tool policy.

1. On Intel Arc, create a short Kdenlive project with linked video/audio, proxies
   and a title. Save, close and reopen it; verify links, profile, timeline/range
   and audio. Render a CPU baseline into a new output and validate it with
   decoded samples and a consuming player. Retain the editable project and its
   media/proxy references together.
2. Inventory the actual GPU, driver, FFmpeg/MLT/editor versions, encoders and
   filters. Exercise available hardware encode, decode and timeline effects
   separately. Listing a backend or completing a CPU fallback is not GPU
   evidence. Record the backend actually used, output validation, performance
   and explicit fallback/unsupported results. Do not install drivers or change
   the owner's audio configuration merely to make a test pass.
3. Repeat on NVIDIA with the same source/recipe. Keep GPU-specific results and
   limitations separate; do not extrapolate Arc measurements to NVIDIA.

Physical playback, MIDI, microphone/application capture and OBS require separate
task scope and device/portal consent. Do not start them merely to complete
offline analysis or claim low latency from a presence check. If UI access or a
second GPU target is unavailable, record that acceptance as unverified and
continue the independent CLI/project work that is available.

## Acceptance and return handoff

Update the AV plan and coverage map with sanitized native findings. Return the
tested commit and dirty state, package/tool versions, fixture outcomes, editable
project/export checks, GPU evidence by backend, remaining gaps, and private
evidence locations through the receiving agent's private handoff mechanism.
Record any active/uncertain jobs and their owning cleanup command before handing
back; explicitly verify cleanup after cancellation. See the
[handoff conventions](../agents/handoffs.md). Do not copy raw device/session
inventories, account paths, captures or logs into Git.
