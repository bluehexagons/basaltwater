# Test output and task logging audit (2026-08-24)

Status: complete for the CI-output slice reviewed on 2026-08-24.

## Baseline

The 2026-08-24 audit baseline contained 137 discovered test modules and 2,631
test method definitions. The default runner executed 2,621 tests with one
skip. A local `make check` passed but produced about 100 lines (11 KiB) of
service log output after the test summary. These are historical audit counts,
not current suite totals.

The primary leak was in `lib/logging_utils.py`: non-root processes cannot write
`/var/log/infra_tools`, so their fallback `stderr` handlers remained active
even when `run_tests.py` set `INFRA_TOOLS_TEST=1`. Those handlers were attached
to the original stream and bypassed the runner's temporary capture.

The second source of routine noise was `lib.remote_utils.run`, which printed a
`Running:` line for every command. Setup already has step-level progress and
completion messages, so command-level echoing was redundant for normal runs.

## Changes

- Test-mode logger fallback handlers are now null handlers, and logger setup
  diagnostics are suppressed in test mode. Test mode is evaluated when the
  handler is configured, so import order does not reintroduce console output.
- The concise runner separates unittest result output from captured task
  output. Failures show the unittest report only; `--show-output` opts into the
  captured stream for diagnosis. `-v` remains the deliberately noisy mode.
- Individual task command echoes are opt-in through
  `INFRA_TOOLS_VERBOSE=1`. Dry-run command plans continue to show every
  command.
- The common CLI bundle now installs Debian's `pv` package. Existing rsync
  transfers retain their native progress behavior rather than adding another
  progress layer.
- A duplicate project-manifest test was removed. Similar-looking security,
  maintenance, and notification tests were retained because their patched
  inputs exercise distinct branches or safety boundaries.

## Verification

- Focused logging, runner, remote-command, common-package, and manifest tests
  pass.
- Full `make check` passes: 2,623 tests run, one skipped, six lines and 174
  bytes of output. That is down from 100 lines and 11,349 bytes locally.
