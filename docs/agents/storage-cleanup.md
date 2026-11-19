# Storage cleanup reviews

Use **Agents → Agent tools → Review disk cleanup** or the **Review storage
cleanup** task template for a one-time inspection. Both start with measured
local candidates, rank the best opportunities in the visible summary, and
request a follow-up that applies a selected safe cleanup and verifies reclaimed
space. The summary must contain the actual candidates, measurements and retention
decisions, rather than just saying that the review finished. Opening a template or
running the review does not authorize deletion. Existing saved tasks keep their
prompts; create a fresh review to use the updated guidance.

## Choose a topic

<a id="host-evidence-and-approved-cache-operations"></a>
<a id="gather-evidence"></a>

- [Collect storage cleanup evidence](storage-cleanup-evidence.md) Gather host evidence and review the approved cache operations.

<a id="shared-cache-conventions"></a>
<a id="go-build-cache-cleanup"></a>
<a id="podman-image-cleanup"></a>

- [Clean up Go and Podman caches](storage-cleanup-caches.md) Use shared cache conventions, review the cleanup scope, and verify results.

<a id="inspect-project-conventions-economically"></a>
<a id="choose-a-follow-up"></a>

- [Inspect project storage conventions](storage-cleanup-follow-up.md) Review project storage and choose a follow-up.
