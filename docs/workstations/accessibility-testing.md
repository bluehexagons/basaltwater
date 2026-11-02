# CachyOS accessibility evidence and Orca qualification

Run recording as the existing desktop user in their KDE session. It does not
start, stop, unlock, or change the desktop/control services, request portal
permissions, install packages, or change accessibility preferences.

```bash
basaltw desktop --native accessibility-record --pid 12345 --seconds 30 \
  --max-events 500 --max-output-bytes 262144 --max-label-chars 256 \
  --output /absolute/private/evidence/new-accessibility-bundle --json
```

Select the application's actual AT-SPI PID using desktop window/inspection
evidence. A browser renderer PID may differ from its accessible application PID.
The recorder verifies current-user ownership, Linux boot ID and `/proc/PID/stat`
start ticks. `--process-start TICKS` additionally rejects a selection whose start
identity changed before invocation. It never follows a reused PID or silently
switches to another application. Missing or unresponsive applications fail.

The output parent must exist, belong to the invoking user and prohibit writes
by other users/groups. The new bundle directory is mode 0700;
`manifest.json` is mode 0600. Existing files/directories/symlinks are refused.
Keep bundles outside Git: accessible names and announcements can contain private
document information even without text-interface reads. Only share reviewed,
sanitized evidence. Private modes do not protect against another process running
as the same desktop account.

Collection runs in the foreground for 1–120 seconds, including worker lookup
and registration. The parent watchdog allows three additional seconds for worker
startup/cleanup, then terminates the isolated worker process group using the
shared bounded subprocess supervisor. Ctrl+C also cleans up the worker and
finalizes accepted events. Forced termination of the parent itself can leave an
initial `status: recording` manifest; that is incomplete evidence, not success.

Limits are 1–5000 events, 16384–4194304 output bytes (the complete compact JSON
manifest), and 1–512 characters per label. Reaching an event ceiling reports
conservative truncation even if no further event would have arrived. Output-size
truncation, per-event `labels_truncated`, and collection errors are explicit.
Partial evidence survives a worker timeout/failure; inspect `status`, `errors`,
`truncated`, and `truncation_reasons` before using it.

The worker uses [AT-SPI application-scoped event registration](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/method.EventListener.register_with_app.html)
and independently verifies each source's accessible application and process
identity. It collects name/value property changes, state/focus events and
announcements. Values are finite numeric Value-interface values; arbitrary text
and raw property payloads are excluded. Protected/password and defunct sources
or ancestors, unrelated applications and unverifiable ancestry are omitted.
There is no global keyboard listener or text-change subscription. This is not a
general secret scrubber: an application can put sensitive text in a normal label.

The timeline manifest uses `schema_version: 1`, `kind: accessibility-recording`,
`started_at`/`finished_at` ISO UTC timestamps and corresponding
`started_monotonic_ns`/`finished_monotonic_ns`. Each event has `at` and
`monotonic_ns`; `clock` contains `boot_id` and `monotonic_scope: linux-boot`.
`source.process` stores PID, start ticks, UID and boot ID. Align local action,
capture and audio evidence by matching boot IDs and monotonic timestamps. UTC
can jump; monotonic offsets from different boots/machines cannot be compared.
Event timestamps mark observation in the callback, not original emission.

## Optional Orca TESTING PROFILE

AT-SPI events do not establish spoken output, intelligibility, navigation quality,
or screen-reader success. Orca is a separate opt-in qualification step.
Consult [upstream Orca help](https://gnome.pages.gitlab.gnome.org/orca/help/introduction.html)
and [Orca's upstream dependencies and speech backends](https://github.com/GNOME/orca/blob/main/README.md).
Options and speech dependencies vary by installed version; save bounded local
`orca --version` and `orca --help` evidence before selecting flags. Upstream
documents `-u` for alternate preferences and `--replace` for replacing a running
reader; never use `--replace` in this workflow.

1. Discover installed packages with `pacman -Q python-gobject at-spi2-core orca
   speech-dispatcher espeak-ng`, plus `command -v orca speech-dispatcher`.
   These are read-only probes, not proof that speech works. Use a fully updated
   CachyOS system and review missing package names/backend requirements against
   installed Orca and current distro metadata.
2. For AT-SPI prerequisites, use the supported
   [CachyOS setup path](cachyos.md), previewing an actual required desktop
   application selection such as `basaltw setup agent_cachyos localhost
   --inkscape --dry-run` before explicitly applying it. Do not select an
   unrelated application just to obtain dependencies. Existing setup has no
   dedicated Orca testing-profile flag: do not invent `--orca`, pass pacman
   packages through `--apt-install`, or claim setup installed a speech backend.
   If Orca is missing, obtain separate install opt-in and use the distro's
   supported package path (`sudo pacman -S --needed orca speech-dispatcher
   espeak-ng` when those match the reviewed backend). Never perform a partial
   upgrade with `pacman -Sy`. No install is performed by the recorder.
3. Qualify in a disposable graphical login/session with synthetic application
   content and no existing Orca instance, reader service or automatic restart.
   Alternate prefs alone do not isolate Orca's desktop-wide AT-SPI subscription,
   speech service, D-Bus identity, or audio routing. Do not use the operator's
   active assistive-technology session. If this isolation is unavailable, stop
   at dependency/AT-SPI qualification and report speech as untested.
4. In that disposable session, create an isolated child profile and bound both
   setup and the run. Review the installed help first; this example uses `-u`.

   ```bash
   umask 077
   testing_profile=$(mktemp -d "${TMPDIR:-/tmp}/orca-testing.XXXXXXXX")
   mkdir "$testing_profile/prefs" "$testing_profile/config" "$testing_profile/cache"
   timeout --signal=TERM --kill-after=3s 60s \
     env XDG_CONFIG_HOME="$testing_profile/config" XDG_CACHE_HOME="$testing_profile/cache" \
     orca -u "$testing_profile/prefs" --setup
   timeout --signal=TERM --kill-after=3s 60s \
     env XDG_CONFIG_HOME="$testing_profile/config" XDG_CACHE_HOME="$testing_profile/cache" \
     orca -u "$testing_profile/prefs"
   ```

   Keep setup changes inside this TESTING PROFILE. Do not enable desktop
   autostart, change KDE/global accessibility settings, reuse the ordinary Orca
   config, or enable debug logging against private desktop content. Timeout exit
   124 is an expected bound, not evidence of successful speech.
5. Exercise a fixed synthetic sequence: focus a named button, change a numeric
   value, open a dialog and trigger an announcement. Record expected spoken
   phrases and actual operator observations. Run the PID-scoped AT-SPI recorder
   concurrently from another terminal. Check protected controls and a second
   application do not enter its timeline; check an application restart stops
   collection rather than attaching to the replacement.
6. Capture speech only with separate consent and an explicitly identified
   Speech Dispatcher/selected speech-backend stream or isolated sink. Record
   application audio separately. A mixed desktop monitor cannot establish
   which source produced a sound; don't label it isolated speech evidence.
   Store audio start/finish monotonic timestamps, boot ID, route identity,
   format and known buffering offset alongside the recording. Keep raw audio
   private. Human listening or an independent speech assertion is required.
7. After the bounded run, verify only the owned test Orca process and its
   owned children exited; do not use `orca --quit`, `pkill`, or stop shared
   speech services. Restore only routing explicitly changed for this disposable
   test, retain reviewed evidence, then remove the exact temporary profile
   after verifying no owned process uses it. Report uncertain cleanup rather
   than killing unrelated readers. Close the disposable session.

No automatic Orca runner is supplied: preference isolation does not establish
safe session/audio isolation or reader exclusivity. Offline mocked tests cover
the recorder's filtering, bounds and subprocess contracts. Native CachyOS
GI signatures, toolkit event delivery, target-exit behavior under load, real
speech routing and cleanup still require this separate live qualification.
