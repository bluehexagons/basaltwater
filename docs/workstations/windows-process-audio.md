# Bounded Windows process audio

This optional facility implements process-tree loopback through an explicit
public CLI action. Ordinary desktop observation never starts recording. Pass
the exact retained project/task and select recording explicitly:

```powershell
basaltw desktop audio --task TASK_ID --project C:\repos\sample-game --seconds 2
```

The CLI builds the optional helper with existing x64 C++/SDK tools when needed;
`--helper EXE` selects a supplied binary. Keep recordings and metadata in private
task evidence outside Git. See [Windows evidence](windows-evidence.md) for GPU
capture, presentation tracing and source CLI invocation before installation.

Build once with existing x64 MSVC C++ tools and a Windows SDK containing
`audioclientactivationparams.h` (SDK 20348 or newer). No dependencies are installed:

```powershell
pwsh -NoProfile -File windows/Audio.Capture.Build.ps1 -OutputDirectory C:\example\evidence\audio-helper
```

The executable uses the static MSVC runtime. Deployment may supply this optional
binary alongside the wrapper; its absence returns an explicit prerequisite error.
Keep the binary paired with this source revision. The build script discovers
existing Visual Studio tools using vswhere and enters the x64 developer environment.

The integration caller uses PowerShell 7 and dot-sources these files in order:

```powershell
. ./windows/Development.Common.ps1
. ./windows/Development.Diagnostics.ps1
. ./windows/Audio.Capture.ps1
$result = Invoke-DevelopmentProcessAudio -Id $taskId -Project 'C:\example\sample-game' `
    -Helper 'C:\example\evidence\audio-helper\Audio.Capture.exe' -OptIn -Seconds 2
# Optional exact retained selection; captures this member and its descendants:
$result = Invoke-DevelopmentProcessAudio -Id $taskId -Project 'C:\example\sample-game' `
    -Helper 'C:\example\evidence\audio-helper\Audio.Capture.exe' -OptIn -Seconds 2 -Member '1234:133000000000000000'
```

Return exit failure when `Capture.ok` is false. `MetadataPath` and `WavPath`
identify private retained artifacts. A caller may pass a .NET CancellationToken
using `-CancellationToken`; its managed callback signals a named event without
needing a PowerShell callback runspace. Cancellation retains a partial WAV when
the helper can finalize it. A helper error or forced termination can leave no WAV
or an incomplete file; only a successful receipt certifies finalization. The
wrapper retains a failure metadata receipt when the native helper fails.

Admission reads existing [task diagnostics](windows-diagnostics.md) identities.
It rejects terminal/uncertain tasks, another exact project, another account or
session, unretained/stale members and helper-role selection. Child selection
requires `--track-children`; the default selects `target.json`. No process-name
lookup or discovery of unrelated PIDs occurs. The helper pins the selected
process handle, validates creation FILETIME, token SID/session and current
membership in the retained named Job before activation/start and every packet
admission. Job query handles close immediately. The existing supervisor owns
the task lifecycle and prevents breakaway; capture does not own or terminate
the selected application. Same-account state tampering is outside this contract.

The implementation follows Microsoft's [application loopback sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)
and [process loopback activation parameters](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params):
`ActivateAudioInterfaceAsync`, `VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK`,
`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK` and
`PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE`. Windows build 20348 is the
minimum. Unsupported OS results never fall back to endpoint/system loopback.
No devices, routing, drivers, volumes or user preferences are changed.

Duration accepts 0.1–30 seconds, rounded to milliseconds. PCM is fixed stereo
44,100 Hz signed 16-bit little-endian with shared-mode automatic conversion,
matching the sample. The retained PCM vector is at most 5,292,000 bytes, WAV
5,292,044 bytes; engine buffer admission is capped at two seconds (352,800 bytes).
These are audio payload bounds, not total process RSS (COM/Windows allocate their
own runtime state). There is no growing packet log. Activation waits at most five
seconds, polling waits at most 20 ms, and an independent helper thread terminates
the helper after 42 seconds or original public caller-process exit. The CLI
passes that caller's PID and creation identity through its PowerShell 7
intermediary; losing the outer CLI stops recording even if the intermediary
survives. Direct wrapper calls default to their own caller. The wrapper waits at most 44
seconds, then kills and confirms exit with a two-second cleanup bound. A blocked
kernel I/O operation can still cause explicit cleanup uncertainty. No helper
children are created. Optional cancellation is checked before activation and in
capture waits; activation itself can take up to five seconds to settle.

One task serializes captures and retains at most eight metadata receipts, at most
16 files and under 48 MiB in its private `audio` folder. Existing evidence is not
overwritten or automatically pruned. An orphan artifact consumes the quota.

Metadata reports actual start/end QPC ticks and frequency, UTC FILETIME anchors,
requested duration, captured frame count (audio duration is frames/44100), packet
count, exact-zero silent sample count and absolute normalized peak. No packets
means no observed audio, not proof of silence. First/last valid packet QPC values
are in WASAPI's 100-ns QPC domain; device positions are frame indices. Timestamp
errors, discontinuity flags, forward position gaps and trimmed tail frames are
separate counters. Lost frames are inferred only from valid forward gaps; zero
does not prove lossless capture. Capture does not pad missing time or replay
audio. Endpoint events are explicitly unavailable because process loopback is
endpoint independent; operator/device recovery observation remains separate.

Offline checks (no audio activation):

```powershell
pwsh -NoProfile -File windows/tests/Run-ProcessAudioTests.ps1
pwsh -NoProfile -File windows/tests/Run-ProcessAudioTests.ps1 -NativeBuild
```

The native gate builds with `/W4 /WX`, tests summaries and bounds, and uses
`--verify` to reject a stale creation identity and a current process outside the
named Job. `--verify` never calls an audio API. Mock tests use temporary state
for opt-in, exact-project/account/session/lifecycle admission, retained child
selection, cancellation, missing helper, failed capture evidence and disk quotas.

The opt-in `windows/tests/Qualify-ProcessAudio.ps1 -Output NEW_DIRECTORY`
runner passes on native Windows with two unrelated project-owned synthetic
tones: it captures the selected 440 Hz tone, excludes the unrelated 997 Hz tone,
and captures silence when only the unrelated fixture plays. Real cancellation
retains partial audio, outer-CLI loss stops the recorder, and both fixtures
settle normally. This is process-loopback signal evidence, without physical
listening or device-recovery qualification. Extend target acceptance with two
unrelated project-owned synthetic audio processes, each
with a deterministic PCM tone generator (for example 440 Hz and 997 Hz):

`windows/Audio.Capture.Tone.ps1` is a runnable parent-only fixture. It emits a
one-second looping synthetic mono tone at approximately five percent full scale,
for at most 120 seconds, using SoundPlayer and the existing default playback
path. It never runs in the offline suite. Start A using the public supervisor
from the checked-out source, with an absolute path to that script:

```powershell
basaltw desktop develop exec --project C:\example\sample-game --test-session --track-children --timeout 90 -- pwsh.exe -NoProfile -File C:\example\basaltwater\windows\Audio.Capture.Tone.ps1 -Frequency 440 -DurationSeconds 60
basaltw desktop develop ready TASK_ID --timeout 30
```

In an independent terminal, create a disposable directory outside A's project,
then run B (it stops itself after 60 seconds):

```powershell
pwsh.exe -NoProfile -File C:\example\basaltwater\windows\Audio.Capture.Tone.ps1 -Frequency 997 -DurationSeconds 60 -ProfilePath C:\example\evidence\tone-b-profile
```

Use the wrapper invocation above while both run, selecting A's retained native
root or tone worker member. Creating an empty `silence` file in A's retained
test-profile directory stops only A's fixture output; deleting it resumes A.
The qualification runner includes bounded PCM spectral assertions with explicit
thresholds. Other project recipes should likewise retain their thresholds.

1. Launch generator A through the existing supervisor with a disposable profile,
   readiness and `--track-children`. Launch generator B independently, outside
   A's Job. Retain both PID/creation identities privately. Do not use commercial
   media or a microphone. Keep volumes comfortable; do not change routing.
2. Capture A's retained child while both generators emit their different tones.
   Read the returned WAV using a project-owned spectral assertion: require A's
   tone and require B's tone below a specified exclusion threshold. Listening
   alone is insufficient. Then mute A in the fixture protocol while B continues;
   A's captured result must contain no B tone. Confirm B remains running.
3. Have A spawn its own tone child; confirm tree inclusion, then capture that
   retained child specifically. Replace it and prove its old token is refused.
   Reject B's identity, wrong project and stale creation time before recording.
4. Exercise 0.1 and 30 seconds, fixture silence, amplitude/peak, early selected
   process exit, task cancellation, cancellation token, caller exit and a forced
   helper termination. Confirm bounded files and helper retirement; task cleanup
   remains under the existing guardian. Retain failure receipts and distinguish
   incomplete WAVs from finalized results.
5. Correlate QPC/UTC against project tone start/stop events, inspect discontinuity
   flags, timestamp errors and gaps under deliberate load. Unsupported OS and
   unavailable audio activation must fail explicitly. Device disconnect/reconnect
   is a separate operator-assisted test; do not infer recovery from packet counts.

The offline suite establishes no live audio. The native runner's synthetic
exclusion/cancellation/caller-loss result does not qualify device recovery,
physical listening, every duration/format, or another game's audio pipeline.
Do not commit raw WAVs, host identities or logs.
