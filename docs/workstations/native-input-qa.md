# Native input and device qualification

Automated Unicode insertion, headless tests and input-queue acceptance do not
qualify OS composition or physical devices. Use a disposable application profile
and a project-owned event inspector, with the human controlling device tests.
Keep raw evidence outside Git. See [CachyOS desktop](cachyos-desktop.md) and
[Windows desktop](windows-desktop.md) for supported automation and ownership.

## Record the input environment

`basaltw local cachyos-doctor --json` includes `input_environment`: KDE's
observed layout list/active index and Fcitx 5 current engine/group/enabled engines
in that group. The report uses bounded local-bus queries with activation disabled;
it neither starts a framework nor reads personal configuration files. It also
reports whether the standard IBus bus name is already owned; IBus engine details
and frameworks using other buses remain unknown. A false owner observation is
not proof that no IME is installed or operating through another route.

Null fields mean unknown. Malformed/oversized replies, unavailable services and
changing layout lists/groups do not produce invented defaults. An empty engine
list means the observed Fcitx group returned no entries. The layout is a
compositor snapshot, not a guarantee about a particular application's next key;
composition and physical delivery still require the tests below. Text output
includes the same bounded metadata, and `--attention` retains it.
The queries follow [KDE's layout interface](https://github.com/KDE/kwin/blob/master/src/keyboard_layout.h)
and [Fcitx's controller interface](https://github.com/fcitx/fcitx5/blob/master/src/modules/dbus/dbusmodule.cpp).

On KDE, record the session from `printenv XDG_SESSION_TYPE XDG_CURRENT_DESKTOP`,
configured layouts in System Settings → Keyboard, and the currently selected
layout from its indicator. `localectl status` reports system defaults, not the
active per-window KDE layout. Record the input-method framework/version, enabled
language engines and selected engine from its configuration and tray UI.
Unset `XMODIFIERS`, `GTK_IM_MODULE` or `QT_IM_MODULE` alone does not prove IME
absence under Wayland. Unavailable framework/engine cases stay untested.

Opt into Japanese and Korean engines, an accented Latin layout with dead keys
and AltGr, and a Cyrillic layout through the user's chosen framework/settings.
Record actual engine/layout names and restore initial selections after testing.
This manual qualification setup does not change default package selections or
authorize replacing personal preferences. Use Windows language/keyboard settings
for native Windows. Record OS/desktop/session versions, application revision,
backend, framework, configured engines, active engine/layout, device and outcome
in a private report. Unknown fields remain unknown. Native Wayland and XWayland
need separate runs because logical symbols and physical positions can differ.

## Application checks

1. In an ordinary field, physically begin Japanese and Korean composition,
   select a non-first candidate, commit, cancel with Escape, move the caret and
   replace selected text. Check preedit/candidate placement, committed content,
   cursor offsets and duplicated commits.
2. Exercise accented Latin dead keys, AltGr and Cyrillic switching, mixed
   ASCII/non-ASCII and non-BMP text. Compare application content/selection with
   expected code points; pixels alone cannot establish exact text. Include an
   application without useful accessibility.
3. Test copy/cut/paste within the app and across a disposable native peer with
   multiline/non-BMP text. Record ownership transitions and avoid overwriting
   a personal clipboard during agent-only work.
4. Physically test keyboard/controller handoff, hot-unplug, reconnect and
   cancellation. Verify no stuck button/key/axis after focus loss or cancellation.
   Installed libraries or virtual-device prerequisites do not qualify a gamepad.
5. Record logical keys, physical/scancodes, modifiers, repeat, press/release and
   UI outcomes. On Windows, test Left/Home/End/PageUp/PageDown/Delete and F11 in
   WinForms and SDL; verify Ctrl+A, Shift+Arrow and release after failed submission.
6. Qualify audio backend open/close, bus meters, captured samples and human
   listening separately. Silent open/close does not establish audible quality,
   routing or other devices. The [native QA audio tools](native-qa-evidence.md)
   provide a scoped native-PipeWire stereo route and offline analysis;
   bridge routes, real game output and physical listening need qualification.

Preserve physical takeover and disabled states. CachyOS guards capture generation,
geometry and age, but has no authoritative compositor focus identity guard.
Confirm focus before each bounded action and inspect the result. AT-SPI inventory
cannot close that race; screenshot freshness is not a focus guarantee.

## Evidence, advice and closeout

Use the existing evidence recorder and offline hash verification. Inspect a
refreshed capture before reusing coordinates. Compact captures expose age and
remaining validity; expired observations still reject input.
Specialist packets should contain absolute artifact paths, source hashes,
capture times, scene/task context, advice deadline and controller identity.
Observers inspect saved files while the controller retains exclusive input.
Require a list of inputs actually read and any missing/unread files.

Keep originals immutable. For derivatives record source path/hash, crop rectangle
in source pixels or clip time interval, tool/command, output hash, caption and
reviewer. Link sources and derivatives in private Markdown; review pixels and
private content before sharing a portable bundle. The [crop/excerpt/export tools](native-qa-evidence.md)
retain immutable source copies and verify derivative hashes before reviewed export.
Use [application-scoped accessibility recording](accessibility-testing.md) and the
shared QA timeline to correlate observed names, focus and announcements with
actions/audio. AT-SPI events alone do not establish screen-reader speech.
Record phase/tool elapsed times where available. Keep reasoning, tool/transport
latency and orchestration overhead distinct; unavailable model/effort/usage stays
unknown. A long turn alone does not identify its bottleneck.

Enumerate owned launches, task IDs, recorders and workers at closeout. Request
normal app exit and verify termination. Finish/verify evidence, retain receipts
and logs, and list resources still running or uncertain. Preserve shared Steam,
T3, control services and indexed evidence. Finishing a journal does not stop apps.

## Missing timezone data diagnosis

Inspect a failed `cachyos-iw-set-regdomain.service` without restarting it:

```sh
systemctl status cachyos-iw-set-regdomain.service --no-pager
journalctl -u cachyos-iw-set-regdomain.service -b --no-pager -n 40
pacman -Q tzdata
pacman -Qkk tzdata
ls -l /usr/share/zoneinfo/zone.tab /usr/share/zoneinfo/zone1970.tab
systemctl cat cachyos-iw-set-regdomain.service
```

Keep output private. Distinguish missing/corrupt package files from a service
referring to a removed path. Repair through the distribution's supported package
workflow after reviewing evidence; do not fabricate files, change the regulatory
domain or use a partial `pacman -Sy` upgrade. During the 2026-11-02 review on one
workstation both files existed and the service's last result was success. That
does not resolve the failure reported from a separate desktop.
