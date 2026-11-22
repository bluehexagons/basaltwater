# Local contributor checks

Start with the [development environment](README.md#standard-checks).
Fish uses `source .venv/bin/activate.fish`.

Use `make check-fast` for the edit/test loop: it runs syntax, documentation and
package metadata checks plus the smoke suite. Select relevant tests with
`make check-fast SUITE=web`, `make test SUITE="web network"`, or
`make test TEST=tests.test_gogs_steps.TestGogsNginx`. `TEST_ARGS="--durations 20"`
reports slow tests. Suite and explicit test selections combine without repeats.
Quiet runs discard test stdout/stderr by default. `TEST_ARGS="--show-output"`
retains the last 256 Ki characters and prints that tail on failure, with an
omission notice when truncated. Failure tracebacks are still reported normally;
use `make test-verbose` for live, complete test output.
The fast target omits brand export and wheel build/install checks. See
[CI and packaging](ci.md) for broad validation.
Use `python3 run_tests.py --list-suites` to see concise suite names and sizes.
`python3 scripts/test_inventory.py` validates every suite selector and pattern
and reports structural counts without importing tests. Add `--json` for module
ownership, legitimate overlaps, and candidate sleep/process sites for review.
`run_tests.py --durations 30 --timings-json /tmp/test-timings.json` exports
result counts and every test duration, including on test failure. Per-case time
includes `setUp`, `tearDown` and cleanups; suite elapsed time also includes
class/module fixtures and runner overhead. Discovery/import time is excluded.
The JSON contains test IDs and counts, without captured output or tracebacks.
Choose all affected domains; shared configuration, validators, plugin composition,
test infrastructure, or uncertain dependencies warrant a full local run.
Use `make check-ci` for that broad validation or when CI is unavailable.

The installer suite reads and executes the published download and quick-start
examples. When moving them, update the source paths in
[`tests/test_install_script.py`](../../../tests/test_install_script.py) and run
`make test TEST=tests.test_install_script` with fish on `PATH`.

Use the project's environment rather than installing development dependencies
into CachyOS's or Debian's system Python.

`make dev-check` reports the selected Python executable and missing development
modules before tests start. Fast checks, test targets and coverage run it
automatically. It checks module discoverability, not installed version ranges;
install the declared extras to resolve those ranges. It never installs packages.
Standalone compilation and documentation checks still need only the standard
library; direct `run_tests.py` selections remain available for such focused checks.

`make compile` excludes managed `cachyos-t3` application payloads, `node_modules`,
build/output/cache trees, egg metadata and virtual environments (including custom
names containing `pyvenv.cfg`). It skips symlinks instead of compiling external
trees. Syntax errors in maintained source still fail the command. No Git metadata
is required, so the same boundary works in a source archive.

The Python documentation checks also run on native Windows with Python 3.11+
from the repository root (use `python3` instead of `python` where appropriate):

```text
python scripts/check_cli_docs.py
python scripts/check_doc_links.py
python -m unittest tests.test_cli_docs tests.test_doc_links
```

These offline checks need only the standard library. Direct `unittest` avoids
the POSIX imports in the shared test runner. The CLI checker reads actual
root parser registrations without importing the Linux controller. It follows
imported builders and local helper functions, includes literal names and aliases,
and ignores nested commands. Nonliteral names/aliases, conditional registration,
loops or other unsupported uses of root subparsers fail with a source location;
extend the checker when introducing a new registration pattern. Existing alias
exemptions remain explicit in the checker. POSIX tests also compare the static
result with the runtime parser. These commands do not establish native Windows
support for the Linux controller, full test suite or Make targets; full checks
still require the documented Debian environment.

## Browser development

First-party Chromium/Firefox application checks are a separate developer-machine
command: `make browser-test`. They are never dependencies of `check-ci` and do
not install browser payloads. Follow [browser qualification](../browser-testing.md)
for prerequisites, private evidence, the disposable Guacamole boundary, and the
separate native Firefox ESR release check. Keep fast unit/service/render checks
in CI. When creating project instructions, document browser targets and the
local intensive test command in its agent/contributor guidance; projects can
explicitly choose another CI policy when their needs justify it.

For interactive panel edits, run `make panel-dev` in the activated environment.
It serves the production handlers on loopback with sample scenarios, simulated
host actions, private temporary state and automatic source/browser reload.
Use `PANEL_ARGS="--scenario degraded"` for failure states or `PANEL_ARGS="--https"`
for transport-sensitive forms. `PANEL_ARGS="--surface pairing"` opens device
enrollment and the native T3 handoff; `approval`, `desktop`, and `console` select
the other specialized panels with the same reset/reload workflow. Desktop and
Console require reviewed Guacamole assets and enable HTTPS automatically.
See the [local panel workflow](../browser-testing.md#develop-the-web-panel-locally)
for scenario/reset controls, supported simulated actions and certificate trust.
For a focused browser check use
`make browser-test BROWSER_ARGS="--engine chromium --grep 'panel development'"`;
filtered results do not count as full application qualification.
