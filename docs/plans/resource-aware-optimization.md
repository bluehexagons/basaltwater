# Resource-aware optimization

Status: selected efficiency improvements are implemented: focused CLI parser
construction, deferred root metadata, bounded quiet-test output, artifact
inventory during snapshot copying, one periodic source-storage scan and less
CI installer work. Larger allocations can explicitly overlap independent Make
checks while the covered suite stays serial. Artifact export validation uses
indexed lookups, and request hashing reuses its validated canonical encoding.
A bounded [development benchmark](../../scripts/benchmark_development.py)
supports comparable checkout and installed-launcher measurements. The
[coverage map](testing-coverage.md) owns native qualification limits. Shared
resource admission, test worker pools, new caches and multi-job build capacity
below remain proposed; these changes do not enable them.

The agreed minimum for basic CLI use, lightweight editing and focused tests is
**1 vCPU and 1 GiB RAM**. Improve that experience while allowing larger machines
to complete independent work faster within explicit resource budgets. Optimize
elapsed time, total compute, peak memory, disk activity and network traffic;
report each separately instead of treating more parallelism as less work.

This plan complements the [shared build-host plan](cicd-manifest-reuse.md).
That plan owns cross-role capacity, draining and durable leases. It remains a
dependency for concurrent heavy jobs across builders, webhooks and runners.
This plan adds measurement, workload budgets and implementation efficiency;
it does not introduce another host or fleet scheduler.

## Choose a topic

<a id="scope-and-hardware-expectations"></a>
<a id="basic-development-qualification-workload"></a>
<a id="common-design-rules"></a>
<a id="observe-once-admit-explicitly-enforce-at-the-existing-owner"></a>
<a id="give-cached-work-a-defined-lifetime"></a>

- [Resource-aware optimization scope and design](resource-aware-optimization-design.md) Review hardware expectations, the baseline workload, and resource ownership and cache rules.

<a id="delivery-phases"></a>
<a id="0-establish-workload-baselines-and-acceptance-budgets"></a>
<a id="1-define-shared-resource-policy-and-preserve-a-small-serial-path"></a>
<a id="2-reduce-startup-and-memory-work-before-increasing-concurrency"></a>
<a id="3-add-resource-aware-test-parallelism-and-reduce-ci-preparation"></a>
<a id="4-reuse-immutable-setupbuild-inputs-and-qualify-host-throughput"></a>
<a id="integrated-qualification-and-rollout"></a>
<a id="next-bounded-deliverables"></a>

- [Resource-aware optimization delivery plan](resource-aware-optimization-delivery.md) Follow the implementation phases, integrated qualification, and bounded deliverables.

<a id="additional-routes-to-rank-from-measurements"></a>
<a id="deferred-policy-and-scope-changes"></a>
<a id="larger-allocation-follow-up"></a>

- [Resource-aware optimization follow-up](resource-aware-optimization-follow-up.md) Review measurement candidates, deferred policy work, and larger-host options.
