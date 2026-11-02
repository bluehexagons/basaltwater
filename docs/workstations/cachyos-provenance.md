# CachyOS source and skill provenance

Use `basaltw agent manifest --installation-context --json` to compare the
invoked CLI, managed source, native worker startup source, activation receipt
and last successful setup. `--managed-source /absolute/managed/source` selects
an explicit managed checkout. Inspection does not refresh source, restart a
worker, install packages, request portal consent or inject input.

The manifest's `provenance` summary retains source revisions and delivered skill
identities in capability-scoped reports too. Without installation context,
managed and worker revisions are unknown because those sources were not probed.
The normal text report prints invoked, managed and native worker revisions
together, followed by delivered skill bundle identities. JSON null means unknown.

## Worker startup evidence

`basaltw desktop --native status --json` includes `worker_provenance`, captured
once by the helper at startup and retained in its status receipt. Its source
contains version, commit and dirty state; `captured_at` timestamps the observation
and `evidence_source` distinguishes observed Git from setup/refresh receipts or
unrecorded source. Status never substitutes the current on-disk HEAD for the
startup observation. An older worker without this metadata reports null.

The installation context's `native_worker.source` describes a live helper only.
For a stopped helper the prior startup evidence is historical, retained under
`native_worker.startup`, with no current worker source. A missing or unsafe
status, invalid startup metadata, or unavailable runtime reports unknown.
The managed T3 service remains separate: unit state and invocation identity do
not establish its loaded source revision.

Startup evidence is an observation, not an immutable code snapshot or module
hash attestation. Updating files during startup or subsequently loading Python
modules from a changed checkout can mix code versions. Receipt evidence retains
its original qualification and does not become observed Git evidence at startup.
No inspection operation automatically restarts an old helper.

## Drift and delivered skills

`installation_context.same_revision` compares known commits; null means a
comparison cannot be made. `drift` reports true for differing revisions, false
only for equal known revisions with both sources clean, and null otherwise.
Equal dirty revisions do not prove equal source bytes. Setup and activation
receipts remain separate observations rather than proof of loaded code.

Each delivered skill has a `provenance` object. `bundle_sha256` hashes a sorted
JSON list of known relative filenames, content SHA-256 digests and file modes;
`comparison_bundle_sha256` describes the invoked CLI's corresponding source
bundle. Unknown personal files are not scanned. Missing files, unsafe bundles
or obsolete recorded resources prevent a complete delivered digest. Skill drift
is true for a known difference, false for a current bundle, and null when unsafe,
incomplete or personal. Catalog status retains these distinctions.

Resource inventories establish supporting-file bookkeeping, not a delivery
commit. Missing inventories are labeled `unknown-legacy`. `delivered_revision`
remains null because existing delivery metadata does not record source revisions;
matching content does not establish which commit delivered it. The summary's
`skill_comparison_source` identifies the source used for comparison only.
`session_loaded_skill_revision` and per-skill `session_loaded_revision` remain
null: catalog presence and current delivered bytes cannot prove what instructions
an already-running agent loaded. Use the active session's skill list to establish
availability; existing sessions may retain earlier instructions after delivery.

Keep raw operational reports outside Git. Tests cover these contracts with
temporary files and mocked host probes; they do not qualify a live CachyOS
worker or portal session.
