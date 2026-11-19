# Development benchmarks

## Resource benchmarks

Use `python3 scripts/benchmark_development.py --output /private/new-evidence.json`
for serial offline CLI/help, smoke and representative validation/network/atomic
test measurements. The output parent must exist; the command creates a new
mode-`0600` file and refuses overwrites. Select `--workload help` (repeatable),
`--samples 20` for short-operation tail latency, or `--cli /absolute/basaltw`
to measure an installed launcher. Tests always use the checkout. This command
does not install dependencies, build wheels or configure services.
Use `--checkout /absolute/other-checkout` to compare revisions with the same
benchmark implementation; both source and benchmark revisions are recorded.

Defaults are three samples, a 60-second trial deadline and a 300-second total
workload budget. Counts/deadlines and captured output are bounded; a failed
workload stops the benchmark and remains a failure. Each trial runs in a fresh
worker, records elapsed/child CPU and explicitly scoped RSS plus before/after
resource observations. Largest-child RSS is not aggregate workload or guest
memory, and shared cgroup/pressure observations are not exclusive job evidence.
Fewer than 20 samples do not produce a p95 claim. Cache labels describe the
state you prepared; the tool does not clear caches. Raw JSON can contain bounded
failure diagnostics; sanitize it before sharing. Bash completion, editor
responsiveness and whole-guest peak/headroom still require separate native
qualification under the [optimization plan](../../plans/resource-aware-optimization.md).
