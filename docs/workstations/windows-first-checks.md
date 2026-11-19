# Verify the Windows account

[Topic index](windows.md)

## Verify this account

Allow the queued T3 start/restart to finish, then open a new terminal so providers
inherit PATH. If its completion status says `manual-restart-required`, close and
reopen T3 Code yourself.
Select the local Windows connection and open a native repository, for example
`C:\Users\NAME\repos\PROJECT`. T3 controls separately installed, authenticated
[provider CLIs](https://github.com/pingdotgg/t3code#installation).

```powershell
basaltw --version
basaltw info --json
git --version
gh --version
gh auth status
codex --version
codex login status
node --version
npm.cmd --version
uv --version
python --version
git config --get user.name
git config --get user.email
git config --get core.longpaths
```

Only check optional runtimes you selected. Sign in with `gh auth login` or
`codex login` if needed; Codex's [native Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
is configured through Codex, not by weakening provider approvals in setup.
Configure your preferred Git name/email if the final commands return nothing.
Login available through a temporary environment token in one session may not
be available in a fresh terminal. Setup does not collect or copy credentials.

`info` describes saved selections and the last installer phase; `complete`
does not establish current tool health. `doctor` checks that phase and runs
version probes for Git, GitHub CLI, Codex, and saved optional providers/runtimes,
including dependencies selected by providers and the game bundle. It checks
Node/npm for Codex and OpenCode even without explicit `-Node`, identifies a
failing Microsoft Store Python alias, resolves managed Godot to its actual
executable, and validates configured storage paths,
required directories and the six persisted cache/runtime variables. Storage
diagnostics do not repair missing directories or variables; rerun setup to repair them.
Selected Visual Studio/Windows SDK and T3 desktop checks establish discovery;
they do not compile code or launch the desktop. The `t3-desktop` check also
reports `InstalledVersion` from the executable's product-version metadata when
available. Missing, unreadable or malformed metadata leaves it null while
retaining executable discovery; this does not verify the running About version
or establish that the installed build meets the integration baseline.
A separate `t3-login` check
verifies that the current account's task is enabled with its limited interactive
principal, account-specific logon trigger and expected hidden helper from the
saved source. Missing, disabled or changed tasks fail doctor; reapply desktop
setup to repair them. Intentionally disabling login startup also fails that check.

`doctor` runs no installers or sign-in commands and does not change saved
setup, PATH, skills or storage configuration. Each executable probe has a
30-second timeout. Vendor code starts only after its hidden wrapper joins an
owned Windows Job Object; remaining descendants are terminated and their exit
is verified on success, failure and timeout, including when the vendor root
exits before its output pipes close. Cleanup has a separate five-second bound;
unconfirmed cleanup fails the check. Output is drained while retaining at most
512 characters from the first nonblank line; stderr is discarded. It keeps
checking other tools after a failed version probe. `--json` returns a schema-1
report with `Healthy` and named `Checks` containing `State`, `Command` and
`Detail`; it exports no arbitrary saved-state fields or vendor stderr.
Exit status is zero when all selected host checks pass, or one for failed,
missing, timed-out or incomplete setup. Provider login, project dependencies,
builds, engine/export-template pins and GPU readiness need separate checks.

After updating the CLI, use `basaltw doctor`. To check a saved installation with
the current checkout's diagnostics before applying an update, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Test-AgentWorkstation.ps1 -Json
```

The script also accepts `-TimeoutSeconds 10` to change the per-probe bound.
It requires the account's saved native installation, including when optional
selections are absent in an older state. Checkout-only setup has no saved
selection to diagnose. `refresh --dry-run` checks discovery, storage paths and
managed skill collisions without running version probes. Use the
[game tooling guide](windows-game-development.md#native-project-qualification)
for native prerequisites, or the optional
[Antistatic integration](../integrations/antistatic/windows.md) for its runtime,
bootstrap and test commands.

Python's Microsoft Store execution alias can exist before Python is installed.
Use the project's uv environment, or disable that alias in Windows App execution
aliases if it shadows the managed interpreter in a fresh terminal. Do not
consider `Get-Command python` alone proof of a usable interpreter.

Python packages that fall back to a native build require the project's Windows
build prerequisites. The basic profile supplies Python/uv; the optional
`-GameDev` bundle supplies MSVC and the Windows SDK. Honor the project's Python version
pin and dependency choices when qualifying Windows builds.

Codex/OpenCode receive skills under `%USERPROFILE%\.agents\skills`; Claude
uses `%USERPROFILE%\.claude\skills`. Setup installs Windows workspace, project
development, frontend design, subagent, and T3 preview guidance, including their
supporting files. It records file hashes, refreshes unchanged managed files,
and stops before overwriting personal edits. Each managed file is copied and
hash-checked in a temporary sibling before replacement, so an interrupted copy
can be retried without mistaking partial contents for a personal edit.
Generated Python caches are excluded when applying a checkout.
Junctions/symlinks in a skill path
are refused. No browser payload is installed, and skills do not grant tools
to sessions that do not expose them.

`doctor --skills [--json]` compares only known Windows bundles in both provider
catalogs against the **saved installation's source**, reporting that path and
saved revision. It does not compare against the invoking developer checkout or
run tool probes. `current` means all known resources match and delivery completed;
`different` means owned files differ from source; `incomplete` means missing
resources or unfinished/unknown completion; `personal` identifies unowned or
edited resources; `unsafe` identifies invalid inventories, paths, reparse points
or untrusted ownership/write ACLs. Unselected, absent Claude bundles are
informational. Other noncurrent bundles return exit status one. Arbitrary personal
skills, provider configuration and credentials are not scanned. `SessionLoaded`
remains `unknown`: matching installed files does not establish what an agent loaded.

The existing version-1 inventory now records boolean `Complete`. Setup persists
`false` with old/new digests before file changes, then delivers files, removes
unchanged stale resources and persists the final hashes before publishing `true`.
Interrupted delivery remains `incomplete` even with matching bytes; explicit setup
can safely retry. Older inventories without the marker remain readable but report
unknown completion until setup succeeds. Malformed/unsupported inventories fail
closed. Personal files and edited stale bytecode remain preserved.
Source resources must fit the same inventory path contract; copied inventories,
reserved Windows names and unrecordable resource names fail preflight before
the destination checkpoint changes.

Before updating an installed CLI, the checkout equivalent is
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Test-AgentWorkstation.ps1 -Skills -Json`.

The installer and repository catalog check share the
[Windows skill catalog](../../windows/agent-skills.json), keeping the delivered
bundle selection aligned with the complete shipped skill collection.

## Validation boundary

Use the [local qualification procedure](windows-qualification.md) and its report
template to retain host, workload and operator-reported lifecycle evidence.
Windows qualification can run entirely on the workstation.

Run the offline native setup tests without changing this account's tools:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-AgentWorkstationTests.ps1
```

These tests use mocked package/provider commands, desktop completion, reboot
signals and temporary catalogs. They verify completion follows a successful
receipt, that hidden login tasks stay within the current account, and that
graceful close failures do not force termination or launch a duplicate desktop.
Restart tests mock Windows shutdown and Scheduler operations; they cover default
reboot selection, opt-outs, installer interruption, one-time boot/login recovery,
stale request rejection and dispatch failures. No live reboot was performed.
The desktop warning is compiled, while countdown decisions are mocked; offline
tests do not qualify live dialog interaction, locking or display scaling.
Sunshine tests mock package and service operations; they do not install it.
The [manual Windows workflow](../../.github/workflows/windows-manual.yml) also
runs them with the existing native/WSL bootstrap tests and Python output-capture
checks. A real plan on an existing Windows 11 machine verifies discovery, not
installation on a fresh PC.
Fresh WinGet repair, UAC, package downloads, reboot recovery, provider login,
Task Scheduler independence when refreshing inside T3, graceful desktop relaunch,
and a new T3 coding session still require live qualification before declaring
this experimental profile production ready.

The complete native suite requires Python 3.11+ on PATH and stable PowerShell 7.
It also runs directly on a workstation, using the local temporary directory when
GitHub Actions' `RUNNER_TEMP` is absent. Disposable process fixtures verify native
descendant cleanup after failure, timeout, cancellation, early parent exit and
guardian termination; Scheduler, notifications and sleep requests remain mocked:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1
```
