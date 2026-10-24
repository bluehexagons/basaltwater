# Launch and verify development tasks

For remote Godot development, check project prerequisites with
`basaltw desktop --native develop doctor --project PATH --json`. Use `develop
editor --project PATH` or `develop run --project PATH --scene res://SCENE` to
launch explicitly in the owned logged-in KDE session from T3 or SSH. Use
`develop import --project PATH` for explicit headless asset preparation; wait
for completion before dependent checks. Editor/run accept `--headless`,
`--quit-after ITERATIONS`, and `--rendering-method METHOD`; iteration bounds
are not wall-clock deadlines, and headless success does not verify GPU/input.

For Electron, use `develop node SCRIPT --project PATH -- ARGUMENTS` to execute
one declared package script with the selected installed project Node runtime
and npm/pnpm/yarn policy. It installs no dependencies and disables implicit
Corepack downloads. Preserve the project's sandbox policy. For native commands,
use `develop exec --project PATH -- COMMAND`. Use `--dry-run` to preview.
These user-systemd tasks survive client disconnection and retain private bounded
logs; `develop list`, `develop status TASK_ID`, and `develop stop TASK_ID` manage
only their records/process group. Save changes before stopping an editor.
Launches require the persistent agent control state to be enabled, including
after the portal helper stops. They do not grant capture/input permission.
See the [remote development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-development.md).

Queue project-declared Godot/Electron/native checks with `develop check RECIPE
--project PATH --settings SETTINGS.json --timeout SECONDS`. Use existing
`basaltwater-agent.json` recipes; the project harness owns import ordering,
actions, assertions and actual renderer/runtime observations. It reads
`BASALTWATER_VISUAL_SETTINGS` and writes `BASALTWATER_VISUAL_EVIDENCE`. Native
status returns the private `check_report` path with revision/settings/outcome
evidence and a bounded check log. Record what the harness actually exercised;
exit status alone does not verify GPU, audio, input or complete UI behavior.

For local asset work, launch the active executable from the graphical session:

```fish
blender /absolute/project/model.blend
inkscape /absolute/project/assets/icon.svg
gimp /absolute/project/assets/banner.xcf
krita /absolute/project/assets/sprite.kra
audacity /absolute/project/assets/sound.wav
shotcut /absolute/project/assets/clip.mlt
```

Use the selected application's manifest `executable`/`launch_argv` when PATH
shadows or multiple installations exist. Normal project work uses intended
preferences and add-ons. Disposable checks use isolated profiles and synthetic
files, preserving personal settings and unrelated applications.

Keep the task handle and check it to completion; do not start a duplicate after
silence or a timeout. Read only relevant log excerpts from reported paths. For
other verbose local commands, use the project-development capture helper.
