# Windows local qualification report

Copy this template into private evidence; replace placeholders before sharing
a reviewed summary. Do not commit raw logs, credentials or identifying paths.
Follow the [local qualification procedure](../workstations/windows-qualification.md).

- Checked UTC: `<timestamp>`
- Basaltwater checkout revision and dirty state: `<commit; clean/modified>`
- Saved setup revision/phase: `<commit; phase>`
- Windows edition/version/build and architecture: `<values>`
- PowerShell version: `<version>`
- GPU/driver: `<values; discovery only unless rendered checks ran>`
- Selected tool versions: `<doctor report reference>`
- Project commits, dirty states and required pins: `<values>`
- Private evidence location: `<local reference; redact before publishing>`

Use `Pass`, `Fail`, `Blocked` or `Not run`. Evidence source is `Command`,
`Observed` or `User report`. Record exit codes and meaningful skips for commands.

| Check | Result | Evidence source | Command/observation and evidence reference |
| --- | --- | --- | --- |
| Basaltwater doctor | Not run | Command | |
| Basaltwater offline native suite | Not run | Command | |
| Antistatic locked install, doctor and build | Not run | Command | Include Node/npm and native generator. |
| Antistatic lobby build and Windows gate | Not run | Command | Include effective Go toolchain and any skipped cases. |
| Antistatic broader or focused gameplay checks | Not run | Command | Record budgets, pass/skip/cancellation counts, retained failures and focused controls. |
| Antistatic staged package and extracted archive | Not run | Command | Include bundled runtime, archive digest, launch CWD/PATH, private data isolation and asset diagnostics from actual engine startup. |
| snake2d source suites and composed-scene smoke | Not run | Command | Include Godot version. |
| snake2d Windows export and exported startup | Not run | Command | Include matching template source/digest. |
| Reboot/login and fresh-session tool discovery | Not run | Observed | Identify which parts were observed or operator-reported. |
| Rendered desktop, resize and display scaling | Not run | Observed | Record application, settings and display conditions. |
| Physical input, audible audio and persistence | Not run | Observed | Name devices/actions and isolate player data. |
| Setup interruption/recovery on a disposable system | Not run | Observed | |
| Fresh account / clean-machine installation | Not run | Observed | Distinguish account setup from missing-package installation. |

## Findings and limits

- Reproduction and owning repository: `<details>`
- Corrections and focused retest: `<commit/command/result>`
- Remaining failures, blocked checks and manual checks: `<details>`
- Tested source versus later documentation-only changes: `<details>`
