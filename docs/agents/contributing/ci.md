# CI and packaging

See [local checks](checks.md) for the usual edit/test loop. Workflows and Python
metadata are authoritative for current CI versions and dependency floors.

## Packaging checks

`make artifact-check` builds, installs and smoke-tests the wheel. It suppresses
successful build/install chatter and replays failed command output. The Linux
installed-CLI check includes offline desktop instruction paging, coordinate
mapping and durable run continuation/refusal with private temporary files.

## Debian and runner compatibility

CI installs the same `dev` and `completion` extras from `pyproject.toml`, so new
test dependencies such as Guacamole's aiohttp are included automatically.

Python 3.11 is the minimum supported interpreter; release CI also checks Python
3.15. Keep this range aligned with `pyproject.toml` and the installer checks.
The build backend requires setuptools 84.2.1 or newer, and the development
extra requires aiohttp 3.14.5 or newer, excluding versions affected by the
[setuptools path traversal advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf)
and [aiohttp security fixes](https://github.com/aio-libs/aiohttp/releases/tag/v3.14.4).
Target services use distribution-managed packages and their security updates.

Routine CI uses Python 3.13 in Debian Trixie. Branch and release test jobs run
[`scripts/run_debian_ci.py`](../../../scripts/run_debian_ci.py) on an
`ubuntu-24.04` hosted runner. The Python and Node tool-cache binaries run inside
Trixie, so keep their runtime ABI compatible with its glibc; the wrapper checks
Python's `sqlite3`, `ssl` and `termios` imports before installing dependencies.
Ubuntu 26.04's tool-cache Python can require glibc 2.42 (including its
`termios` extension), which Trixie does not provide. Keep the Debian test host
on 24.04 until compatible Python builds are available or interpreter provisioning
is moved inside Debian. Branch CI separately builds, installs and smoke-tests
the wheel natively on Ubuntu 26.04 with Python 3.15, so current platform
packaging stays covered without mixing runtime ABIs. Release wheel packaging
uses `ubuntu-latest`; Debian release tests retain the compatible host.
Requalify the runtime imports inside Debian before changing that host image.
The wrapper tries
Docker's official Debian image from
[public ECR](https://gallery.ecr.aws/docker/library/debian), Google's
[Docker Hub cache](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images),
then Docker Hub, with a two-minute bound per pull. This keeps registry fallback
inside a runnable step; job-container initialization would fail before fallback.
The selected `actions/setup-python` interpreter and supported `actions/setup-node`
runtime are mounted read-only from the runner tool cache. Python creates a private
container venv; checks still run in Debian. Branch CI follows the latest
available patches on Node 24 and 26, and release CI follows Node 24. Only image
pulls fall back:
failed package installation, checks and coverage are never replayed. Cleanup
removes only that invocation's named container. An all-registry failure means
checks did not start. Inspect the failed step before changing application code.
The wrapper reports elapsed seconds and exit status for APT update/install,
venv creation, Python dependencies and checks. It uses the selected interpreter's
bundled pip and disables pip update notifications, avoiding a separate pip
upgrade/download before installing the declared dependencies. These timings
separate environment preparation from validation before considering caches.
`make check-ci` runs all static, brand and wheel checks and executes the full
suite once with branch coverage, matching branch CI. Do not also run
`make check` and `make coverage` for the same unchanged source: that repeats
the full suite. `make check` remains available for full verification without
coverage, and `make coverage` produces a standalone full-suite report. Fast
selections do not narrow `check-ci` or `coverage`.
On a larger allocation, `make -j2 check-ci` overlaps independent static/artifact
checks with the single serial coverage run. Plain `make check-ci` stays serial.
The Debian wrapper exposes `--check-jobs 1|2` (default `1`); hosted full and
release checks select `2`, while Node compatibility stays at `1`. This is a
native Make job limit within one invocation, not automatic test workers or
shared admission across invocations. The operator/outer runner owns the
allocation and concurrent runs. Use serial checks on constrained machines;
full validation is separate from the 1 GiB basic-development workload.
Wheel snapshots exclude live coverage databases and their SQLite side files,
but retain `.coveragerc`, so concurrent coverage cannot race their source copy.
Branch CI runs the complete covered suite and all static/artifact checks once
on Node 24. The Node 26 job runs only `make test SUITE=node`: generated OpenCode
hooks, Guacamole browser behavior, and the certificate-install UI. Keep this
selection in `run_tests.py` current when adding tests that execute JavaScript.
Both jobs retain the Debian environment and registry fallback. Release CI still
runs full checks at both supported Python version endpoints.

CI replaces obsolete runs when a newer revision is pushed to the same branch
or pull request within the same event type. Full checks also run for merge
queues and nightly at 05:23 UTC; scheduled triggers activate after the workflow
reaches the default branch. PR coverage remains full. The Tests workflow summary
reports results and elapsed time; its per-job JSON timing artifacts last 14 days.
The coverage run reports the ten slowest modules and tests; module totals reveal
accumulated fixture cost that individual timings can hide. Use
`make check-ci TIMINGS_JSON=/tmp/test-timings.json` to retain full local timings.
The output parent must already exist. Maintainers triage failures in Actions
before merging; see the [test maintenance plan](../../plans/test-maintenance.md) for the cadence and future gates.
