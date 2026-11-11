# Bounded optimization experiments

Before changing code for performance, record a named, measured bottleneck in
one representative project workload, its baseline and measurement method, and
the correctness/quality gates that must continue to pass. State a falsifiable
hypothesis for the proposed change. Tool availability or an expensive-looking
function alone is not a bottleneck measurement.

Set a minimum useful benefit before the first experiment: a concrete absolute
or relative improvement in the user-relevant metric, larger than observed
measurement noise and worth the added complexity. The project owns this
threshold and its rendering/quality acceptance. Record CPU power preferences,
renderer/device, temperatures, competing work and build/revision identity when
they can change results. Use shared resource reservations and an optional
[performance session](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/performance-sessions.md)
where available; retain a native measurement route.

Choose a finite experiment count and elapsed-time budget in advance, counting
variants and repeated attempts against those bounds. Stop when either budget
is exhausted, the bottleneck is removed, correctness/quality fails, or measured
benefits fall below the useful threshold. Keep the simplest verified useful
result and discard unsuccessful variants. Do not silently expand the budget.
A further bounded batch needs new bottleneck evidence or a materially different
hypothesis with its own stated threshold and budget. Report the measured benefit,
uncertainty and why work stopped.
