# Agent tool interface contracts

Use these contracts for Basaltwater-owned CLI and MCP interfaces. A caller
should be able to discover the right operation, submit a typed request, and
pass its result to the next tool without scraping a message. Apply the
contracts in one coordinated rollout of tools, callers, and skills. Project
recipes, native executables, and upstream tools define their own interfaces.
[Current tool inventory](../agent-tool-inventory.md)

## Requests

| Input | Contract |
| --- | --- |
| Simple values | Use named flags or a clearly named positional value. Label help with the type or unit, such as `JOB_ID` or `--timeout SECONDS`. Validate paths, enums and ranges before dispatch. |
| Structured payloads | Name the argument for the content, such as `PLAN.json` or `CAPTURE_RECEIPT.json`. Document the top-level type, accepted fields and byte limit. Use UTF-8 JSON and reject duplicate keys, non-finite values and unknown request fields. Support `-` as stdin only where the help explicitly lists it. State the base directory for relative paths. |
| Child commands | Accept literal CLI arguments after `--` or an `argv` array of strings in JSON. Pass the arguments directly, preserving spaces and empty strings where valid. A quoted shell command shown for display is for a human to read. |
| MCP arguments | Advertise the object shape with `inputSchema` and accept native values. Use the same validation and defaults as the equivalent CLI operation. Keep transport options such as `--json` at the CLI boundary. List operations default to a bounded summary across both transports. |
| Handles | Return and accept opaque IDs verbatim. Document the issuing service, account/environment scope and expiry where applicable. Validate generation, observation freshness and control state again at dispatch when an action depends on them. |

## CLI results

Every one-shot command with a machine result accepts `--json`. Machine-first
commands return the same object with or without it. Use that default when the
primary output feeds another tool, as with preview resolution and desktop
capture. Human-first commands use the flag to select the machine view. State
the default in help. Once the operation and JSON mode have been parsed, emit
exactly one UTF-8 JSON object followed by a newline on stdout, including for
handled failures. Send progress and forwarded child logs to stderr or
retained files. Help and syntax failures before dispatch can use the parser's
normal text output and exit status. Document interactive flows, child-output
passthrough, and foreground readiness streams with their own framing and exit
behavior. For example, a ready line can arrive while its process stays alive.
[Existing readiness stream](../../../lib/browser_test_instance.py)

| Field | Contract |
| --- | --- |
| `schema_version` | Required positive integer. Start at `1` for a new result schema. The version is scoped to the operation's result and independent of the installed tool release. |
| `ok` | Required boolean. `true` means the operation reached its documented milestone. A successful status query can report that the job failed. |
| `issue` | Required with `ok: false`. Stable `snake_case` code a consumer can branch on, such as `invalid_input`, `permission_denied`, `unavailable`, or `wait_timeout`. |
| `error` | Required with `ok: false`. Concise, safe diagnostic explaining the failure and, when known, a way to recover. Consumers branch on `issue`. |
| `stage`, `effect` | Include on mutating failures. `stage` names the operation step. `effect` describes the requested mutation as `none`, `applied`, `partial` or `unknown`. Use `partial` for a known subset and `unknown` if delivery or final state is indeterminate. Retain any known completed steps in either case. |
| Operation fields | Keep domain fields in the result, for example `navigation`, `job`, or `items`. Use `null` for an applicable value explicitly unknown or unobserved, omission for optional fields outside the result's scope, and `[]` for an observed empty list. |

Use exit `0` for `ok: true`, `2` for invalid usage or input, `124` when a
caller-side wait expires, and `1` for other handled failures in this contract.
Keep a child process's exit code in its own result field when known. A
transport interruption can prevent the result from arriving. Reconcile the
original operation when its effects could have survived the interruption.

## MCP results

Use the same operation result object as the CLI machine view. Require a
negotiated MCP revision that supports structured results for these tools.
Advertise an object `outputSchema` and return the machine result in
`structuredContent`. Use `content` blocks for a concise human summary,
actionable diagnostics or media when applicable. Qualify the actual clients
that will consume the structured result in the coordinated rollout.
[MCP structured content](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#structured-content)

Use `isError: true` and `structuredContent.ok: false` for tool execution
failures, with an actionable text diagnostic. The output schema covers the
success and failure objects. Keep JSON-RPC protocol failures at the protocol
layer.
[MCP error handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)

## Artifacts and bounded output

Return file metadata in `artifacts` when the consumer needs to reuse retained
output. Each entry has a stable `kind`, a `path` accessible on the documented
producing filesystem, and a `media_type`. Use absolute paths for live CLI/MCP
results and bundle-relative paths inside portable manifests. Include `sha256`
when a downstream check relies on exact bytes. A remote path needs a documented
access or transfer route. Keep raw evidence private and use the existing
retention owner for its lifetime. [Documentation privacy](../project-standards.md#documentation-and-private-operational-state)

Use explicit kinds for different roles even when the extension is shared.
For a captured frame, the caller opens the `screenshot` entry as an image and
passes the `capture_receipt` JSON entry to the mapper. The mapper's documented
input parameters and the receipt's current observation handle then go to the
input command. The following illustrates the shared result fields. The desktop
guide specifies today's command syntax. [Desktop observations](../../workstations/desktop-observations.md)

```json
{
  "schema_version": 1,
  "ok": true,
  "artifacts": [
    {"kind": "screenshot", "path": "/private/run/frame.png", "media_type": "image/png"},
    {"kind": "capture_receipt", "path": "/private/run/frame.json", "media_type": "application/json"}
  ]
}
```

Bound lists and logs before returning them. When a view omits data because of
its output bound, indicate `output_truncated: true` and provide the applicable
continuation cursor, explicit log query, or path to retained full output.
`--save-output`, where supported, keeps the full result privately and the
bounded reply keeps the operation status and the reference needed to read it.
Report incomplete source coverage separately. A complete copy of an incomplete
scan still has incomplete coverage. [Desktop evidence and output bounds](../../workstations/desktop-evaluation.md)

## Job lifecycle and recovery

Keep discovery, readiness, submission and completion visible. Discovery reports
available prerequisites, installed assets and observed tool exposure. Readiness
states which operation was exercised, its environment and any untested part.
A browser health result carrying `navigation` supplies those arguments for a
subsequent preview call. It reports reachability from the health probe's own
network origin. [Environment manifest](../agent-environment.md), [T3 resolution](../browser-t3-preview.md)

| Operation | Meaning of its result |
| --- | --- |
| Submit | `ok: true` means work was accepted. Return `job.id` and its last observed `job.state`. Keep the handle in failures after admission when it is known. |
| Status | `ok: true` means the job record was read. `job.state` carries the outcome separately. Use `queued`, `running`, `succeeded`, `failed`, `cancelled`, or `unknown` in new shared views. Preserve finer native state separately when needed. |
| Readiness | Report the named readiness condition independently of job state. The project or service defines the concrete probe that satisfies it. |
| Wait | Name the milestone the command waits for. A completion wait succeeds when the job succeeds and reports failure for a failed or cancelled job. On an expired caller wait, return `wait_timeout`, exit `124`, and retain the same handle and last observed state. A later poll or wait resumes observation of that job. |
| Cancel | Say whether cancellation was requested or confirmed. Keep the job handle and any cleanup evidence available through status. Publish `cancelled` when cancellation is confirmed. |
| Verify | Record what was checked, from which environment, and against which revision or artifact when relevant. Keep the verification separate from submission or process completion. |

For example, a wait expiring while a supervised check was last seen running
would return the following object. A subsequent caller gives `job.id` to that
supervisor's status or wait command.

```json
{
  "schema_version": 1,
  "ok": false,
  "issue": "wait_timeout",
  "error": "The wait expired. Inspect this job's status or wait again.",
  "job": {"id": "example-job-id", "state": "running"}
}
```

When a later step fails after a mutation, keep its receipt and any completed
prefix. For example, after acknowledged input and a failed follow-up capture,
return `ok: false`, `stage: "followup_capture"`, `effect: "applied"` for the
input dispatch, and its delivery receipt. The application outcome remains
unobserved. The next operation can take a fresh observation under the current
control state. For retryable submissions, document the scope and lifetime of
any idempotency key and reuse it for the same request. Use the owning tool's
status and receipts to reconcile uncertain effects before submitting again.
[Desktop sequences](../../workstations/short-observation-sequences.md), [task scheduling](../agent-tasks.md)

## Coordinated rollout and acceptance

Keep operation-specific defaults, field definitions and examples in the
owning command or tool reference. Include one example showing exactly which
result field the next operation consumes. Link that reference from the relevant
skill. Expose capability-specific guidance through existing discovery routes
and keep installed availability, session exposure and verified readiness
distinct. Update the command and MCP producers, their direct consumers,
shipped skills, examples, and fixtures as one release. Check that running
helpers, MCP clients and session-loaded skills are using the rollout's
versions. Increment the result schema version when a published schema changes
incompatibly. Consumers of a version should tolerate additional response fields.
The v2 switch covers the full Basaltwater-owned agent interface inventory.
[Scope, stored data and delivery plan](../../plans/agent-tool-interface-switch.md)

| Representative workflow | Acceptance |
| --- | --- |
| Linux desktop capture, mapping and sequences | Return the same result fields with and without `--json`. Show which field names the image and which names the receipt. Preserve the input receipt and completed captures when a later capture fails. |
| Preview resolution | Return one machine result shape by default and with `--json` for native T3 and gateway resolution. Verify the `navigation` object can be passed to the preview tool with the appropriate target for each route. Exercise loopback success, unhealthy upstream and invalid input. |
| Task CLI and MCP | Exercise one request through both adapters, including invalid input, list summary and creation mode. Verify the same result fields reach CLI stdout and the MCP client's `structuredContent`. |
| Linux and Windows supervised development | Exercise submission, readiness, wait timeout, terminal failure and logs using one handle. Keep each backend's underlying state and child result available. |

For each changed interface, capture the actual public entry point's stdout,
stderr and exit code or MCP result fields. Cover a valid request, input
rejection before effects, runtime failure, and any supported partial or timeout
outcome. Feed a returned handle or artifact to the next operation in the test.
Use mocked system calls or private fixtures for the contract tests. Add scoped
native checks for backend behavior where a fixture cannot establish it.
