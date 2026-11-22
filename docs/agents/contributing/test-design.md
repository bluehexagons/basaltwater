# Test design and fixtures

## Fixture isolation

Tests run on Debian and must not modify the local system. Use `--dry-run` for
setup-plan validation; never run a test against a real target.
Debian CI runs as root inside its container. When a fixture mocks a desktop
user's UID, model ownership of its temporary files consistently while retaining
the permission and file-type checks being tested.
Mock user-runtime locks in setup/refresh tests that do not exercise locking;
use a temporary file descriptor when refresh needs an exec handoff.
Set required fixture directory modes explicitly rather than relying on the
runner's umask. An unsafe ancestor can otherwise prevent a negative test from
reaching the file or failure it intends to exercise.
`Path.mkdir(parents=True, mode=...)` applies the requested mode only to the
leaf; set required ancestor modes explicitly too, and use `chmod` when a
restrictive umask would remove required access.
When running those tests from a CachyOS workstation, fixtures must mock the
target distribution, package-manager discovery, and account privileges. A
Debian test must not accidentally enter the local CachyOS setup path or inspect
and remove files from the developer's host.
When mocking `sys.flags`, preserve the interpreter's other flags and override
only the flag under test; standard libraries such as Python 3.14's argparse
also consult that global object. Mock `Path.lstat` directly when testing its
callers rather than relying on which OS function a Python version uses beneath
it. Executable-search fixtures must provide their own required tools and an
isolated PATH: an invalid fixture executable can otherwise fall through to a
real workstation installation.
Register new test modules in the appropriate `TEST_SUITE_PATTERNS` entry in
`run_tests.py`; the default suite checks that every discovered module belongs
to a domain suite.
Generated OpenCode context hooks also have offline behavioral tests using
Node.js. Keep a supported Node.js available when running those tests locally;
the Debian CI container uses the explicitly selected runner runtime so this
coverage is exercised there.

## Maintaining tests

Use the [test suite maintenance plan](../../plans/test-maintenance.md) for the
suite inventory, measured baseline, pruning decisions and execution cadence.
Recompute counts with the inventory command instead of treating dated counts as
fixed requirements. Review each pruning candidate against the observable contract
and retained coverage.

The shell-safety scan checks normalized keyword spelling before parsing source;
unrelated Unicode text does not require an AST walk. Keep its positive Unicode
keyword and negative comment/string cases when changing that prefilter.

Keep tests that exercise an observable contract: parsed arguments, generated
configuration, state changes, failure propagation, permissions, rollback, or
credential handling. A regression's age alone is not a reason to remove it.

Before adding a regression, check the owning domain suite for an existing case
that can cover it. Remove settled feature-presence checks, import/re-export
identity assertions, and catalog snapshots when behavior tests already cover
the contract. Test ordering with small fixtures rather than copying the entire
built-in catalog. Tests must call Basaltwater code; recomputing the same expected
value with a standard-library function does not verify the application.

When pruning, name the retained coverage in the commit description and preserve
distinct failure inputs, even if their assertion bodies match. Use `subTest`
for a shared behavioral contract when it improves clarity. Sweep independent
options separately and keep representative interaction cases; avoid Cartesian
products unless the options interact. Reuse parsers and immutable fixtures
within the owning test class. Use temporary repositories or checked-in examples
for manifest fixtures so results do not depend on unrelated sibling checkouts.
Generated HTML and service configuration assertions remain useful when they
verify access, escaping, activation, or other behavior. Run the affected selection and review
coverage when pruning execution paths; a broad cleanup warrants `make check-ci`.

Keep related variants in the owning module: optional development bundles share
`test_development_tool_bundles.py`, disk-space calculations belong in
`test_disk_utils.py`, and mount boundaries belong in `test_mount_utils.py`.
Prefer one fixture per contract over rerendering identical configuration for
each directive. Mock host observations such as disk usage and assert exact
values and threshold boundaries. Coverage overlap helps locate candidates;
it does not establish that their assertions or failure inputs are redundant.
For storage limits, lower the fixture's limit instead of generating large
volumes of data. For timeout logic, advance fixture timestamps and control the
owning module's timer; retain real transport tests for connection lifecycle.
Synchronize concurrent tests on events instead of fixed sleeps, and keep mocks
local so they do not change the event loop's clock or unrelated tasks.
