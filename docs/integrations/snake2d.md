# snake2d: a Godot project example

This optional example uses [snake2d](https://github.com/bluehexagons/snake2d).
It is not required for Basaltwater workstation setup or qualification. Prepare
the host with [Windows game tooling](../workstations/windows-game-development.md#godot-project-qualification),
then follow the project's engine pin and matching export-template requirements.

From its native Windows checkout, use the project-owned PowerShell commands:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test.ps1 source
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 windows
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test.ps1 windows-export
```

Source tests require no templates. The native scripts resolve the actual Godot
executable from `BASALTWATER_GODOT_EXE` when no project override is supplied;
use `GODOT_BIN` for a different project pin. Git Bash does not find `godot.cmd`
as `godot`. Batch launchers also expand literal `%NAME%` argument text, so use
the real executable for such paths. A successful source suite/export smoke
does not replace rendered gameplay, physical input and audio checks. Keep
qualification and detailed manual results in the project's
[testing guide](https://github.com/bluehexagons/snake2d/blob/main/docs/manual-testing.md).
