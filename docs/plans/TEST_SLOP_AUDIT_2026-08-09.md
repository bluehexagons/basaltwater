# Test Slop Audit (2026-08-09)

Status: complete for this review slice. At the 2026-08-09 audit baseline the
repository had 102 test modules and about 1,900 test methods; these are
historical counts, not current suite totals. This audit removes tests that add
no distinct behavioral evidence, while retaining boundary, failure, security,
and integration coverage.

## Review criteria

- Keep a test when deleting or breaking a distinct branch, boundary, safety
  rule, failure path, or externally visible output would make it fail.
- Cut tests that only repeat another input/output case, assert a trivial
  framework/parser default, or check setup mechanics without proving the
  resulting behavior.
- Treat command mocks as useful only when the exact command/order is itself a
  contract or a safety property.

## Initial candidates

| Candidate | Initial disposition | Reason |
| --- | --- | --- |
| `test_arg_parser_hosted.py`: default-`None` tests for hosted node, key, memory, and storage | cut | The parser declarations already establish ordinary optional-argument defaults; the surviving hosted-command and remote-parser tests prove the meaningful CLI boundary. |
| `test_progress.py:test_custom_width` | cut | It passed `width=40` but only checked `50%`, duplicating `test_half_progress`; it never proved the width behavior. |
| `test_operation_log.py`: `test_log_metric`, `test_log_context_public`, `test_set_to_none` | cut | They only checked that calls did not raise; they asserted neither logged output nor changed state. |
| `test_proxmox_summary.py` formatting presence tests | undecided | Some are single-substring smoke checks, but the summary is user-facing CLI output. |
| `test_ruby_setup.py` existing-install and step-wiring tests | undecided | They are mock-heavy, but idempotency and system-profile wiring may be important contracts. |

## Decisions

The parser-default candidates were checked against `lib/arg_parser.py`,
`SetupConfig` defaults in `tests/test_config.py`, and its hosted/remote
callers. The remaining positive flag tests and full hosted command test cover
values that affect setup behavior; the default-only tests added no distinct
evidence. Four tests were removed.

The progress candidate was checked against `lib/progress.py`: because its only
assertion was the percentage, it could not detect a broken `width` argument.
It was removed rather than replaced by a misleading test. One test was
removed.

The Proxmox summary formatting tests remain. Although each is small, they cover
user-facing output fields and conditional swap output; together they protect
the CLI summary contract rather than merely exercising a parser declaration.

The Ruby existing-install and step-wiring tests remain. The first protects
idempotency and the second protects profile selection, both of which can fail
without changing the mocked command shape.

The second pass also reviewed every test with no direct assertion. The
remaining cases either intentionally verify that valid input is accepted, or
assert behavior indirectly through a `unittest` result, mock call, filesystem
state, or surrounding failure-path test. The three operation-log no-op tests
were the only cases where a passing implementation could do nothing useful
and still satisfy the test.

## Additional follow-up passes

Three more scans were performed after the operation-log cleanup:

- An AST duplicate-body scan found no duplicate test bodies across modules.
- A tautology scan found no assertions comparing a value with itself or merely
  repeating setup constants.
- A mock-only and comment scan found only dispatch, idempotency, safety, and
  exact command-contract tests; no further pure no-op tests remained.

These passes produced no additional safe removals. The remaining small tests
are intentionally narrow boundary or acceptance checks, especially in the
validation, security, and generated-configuration suites.

## Verification

- Run the full unittest discovery command after edits.
- Run `git diff --check` and inspect the final diff.
- Targeted parser/progress/config check: 115 tests, passing.
- Full discovery before this second pass: 1,895 tests ran, 1 skipped, all
  passing; after the additional three removals: 1,892 ran, 1 skipped, all
  passing.
- Commit and push the audit changes only after the working tree and test suite
  support the decisions above.
