# Build-host service dispatcher

`lib.build_host_service.BuildHostService` dispatches one bounded request for a
principal already authenticated by a trusted transport such as an SSH forced
command. It owns protocol validation, current project-grant checks, owner-scoped
job operations, and private log/artifact reads. It does not authenticate a
principal, start a worker, execute build commands, or activate a deployment.
The worker runs separately and can outlive the transport connection.

## Construction and transport boundary

```python
service = BuildHostService(
    jobs=job_store,
    load_policy=load_current_policy,
    admit_source=admit_validated_source,
    tool_versions=observe_installed_tool_versions,
    logs_root="/var/lib/basaltwater/build-logs",
    artifacts_root="/var/lib/basaltwater/build-artifacts",
)
service.serve_once(
    principal=authenticated_principal,
    input_stream=stdin,
    output_stream=stdout,
)
```

The principal is a separate argument supplied by trusted transport state. The
request schema has no owner or principal field. The dispatcher accepts no
client path or client command selector; source commands remain data in the
validated build request and are handled later by the trusted worker.

The storage roots must already exist. Each root must be owned by the effective
UID, mode `0700`, and have trusted, non-writable ancestors (with the normal
root-owned sticky-directory exception). Logs are named only
`<request_id>.log`; bundles are named only `<request_id>.bundle`. Each file is
opened without following links and must be an owned regular `0600` file with
one link. No absolute storage path is returned to a client.

`handle(header, ...)` accepts one raw UTF-8 JSON header line including its LF;
the input stream then begins at the payload. `serve_once(...)` reads that line
for the caller. The header is at most 1 MiB plus 4 KiB of framing fields. The
transport caller supervises blocking reads and writes with its own deadline;
this API alone does not bound blocking I/O.

## Request framing

Every header has `schema: 1`, an action, and exactly that action's fields:

| Action | Additional fields |
| --- | --- |
| `capabilities` | none |
| `submit` | `request`, `source_size` |
| `status` | `request_id` |
| `cancel` | `request_id` |
| `log` | `request_id`, `offset`, `max_bytes` |
| `artifact` | `request_id` |

IDs are 32 lowercase hexadecimal digits. `source_size` is a positive integer
no larger than the source snapshot ceiling (5 GiB). A log offset is between
zero and 16 MiB; a log chunk is between zero bytes and 64 KiB. Booleans are not
accepted as integers. Duplicate JSON keys, unknown actions, unknown fields,
unsupported schemas, invalid request documents, malformed IDs, and oversized
headers are rejected before a job or policy operation.

Non-upload actions must reach EOF immediately after the header, before any
job lookup or mutation. For `submit`, the injected `admit_source` callback
receives the parsed request, trusted `owner`, raw input stream, declared
`source_size`, and job store. That callback owns exact source-byte framing,
EOF validation, source staging, and admission. The dispatcher does not drain
remaining bytes after denial or malformed input. The callback receives a
preliminary current-policy authorization with `requested_runtimes=[]`; the
worker later checks installed observations against required tools from the
pinned manifest.

## Responses and actions

A successful response starts with one JSON line:

```json
{"ok":true,"payload_bytes":0,"result":{},"schema":1}
```

The encoded line is capped at 64 KiB. For logs and artifacts, exactly
`payload_bytes` raw bytes follow the line; log data is never decoded or
formatted as text. Errors use only the fixed `rejected` or `unavailable` code,
with no exception text, command, environment, or path. Expected validation,
authorization, and queue errors map to `rejected`; filesystem/read errors map
to `unavailable`. Unexpected exceptions propagate to the outer transport
supervisor rather than being reported as completed work.

- `capabilities` returns only `capabilities_for_principal()` for the trusted
  principal plus validated observations for the fixed `go`, `node`, and
  `python` runtime names. Version strings with unsafe characters or excessive
  length are omitted. Other principals and unrecognized tool names are never
  projected.
- `submit` authorizes the parsed request against the current policy before
  invoking source admission. The admitted receipt is bounded as a public JSON
  response.
- `status`, `cancel`, `log`, and `artifact` first ask the job store for the
  requested ID with `owner=principal`. They then require that principal's
  current policy to still contain the job's project grant. A missing grant
  fails closed. The principal is never inferred from request fields or an
  arbitrary owner lookup.
- `log` reads at most the requested chunk, within the job's own log limit.
  Missing logs for queued or running jobs return an empty payload with
  `waiting: true`. A terminal job with no log is unavailable.
- `artifact` is available only for a succeeded job. The bundle's stored size
  must match the private regular file and the complete file hash must match
  the job store before the response header is written. Bytes are streamed in
  64 KiB blocks. File and directory-entry identity are checked again after
  transfer; a transfer-time mutation terminates the stream. Reads do not
  mutate job state or release retention pins. This first slice has no garbage
  collector.

The service uses only fixed filenames derived from the validated request ID.
It does not create source, log, or artifact storage, choose arbitrary paths,
launch commands, or start the build worker.
