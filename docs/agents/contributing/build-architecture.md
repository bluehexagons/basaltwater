# Build implementation map

For operator setup, use [Build systems](../../services/build-systems.md) and
[build examples](../../examples/builds/README.md).

## Selected-builder boundary

For `--build-on`, the public remote-setup hook holds the controller setup-target
lock across preflight, minimal target enrollment, artifact delivery and guarded
final profile application. Minimal enrollment preserves the saved profile; final
setup carries transient expected release identities and omits reenrollment.
Keep builder selection and credentials on the controller. Targets retain artifact
mode and installed releases for local reconciliation without source fetches or
build commands. Preserve guards, retained inputs and exact IDs on uncertain
responses; do not add retries or replacement identities. See
[controller setup](build-setup.md) for the bounded scope and recovery contract.
Selected native public-flow coverage includes a fresh Debian target with Node-built
static sites and Go services, guarded final setup, retained reconciliation and idle reboot
without target compilers. See the [coverage map](../../plans/testing-coverage.md)
for remaining boundaries; mocked checks and dry runs do not establish live coverage.

## Find a component

| Component | Reference |
| --- | --- |
| Shared build request and artifact interfaces | [Build contracts](build-contracts.md) |
| Target deployment reservations and replay safety | [Build admission](build-admission.md) |
| Debian build process and resource supervision | [Build execution](build-execution.md) |
| Pinned build inputs and bounded workspace | [Source snapshots](build-source.md), [scratch storage](build-scratch.md) |
| Durable builder jobs and composition | [Job store](build-jobs.md), [worker](build-worker.md) |
| Explicit terminal builder quota maintenance | [Settlement, source/log pruning and bundle release](build-retention.md), [capacity inspection](build-host-entrypoint.md#read-only-local-capacity-inspection) |
| Authenticated builder transport and admission | [Process entrypoint](build-host-entrypoint.md), [dispatcher](build-host-service.md), [project policy](build-host-policy.md), [source admission](build-inputs.md) |
| Managed builder enrollment and controller transport | [Setup configuration](build-host-config.md), [installer](build-host-setup.md), [SSH client](build-client.md) |
| Public selected-builder setup and recovery | [Controller setup](build-setup.md), [saved selection](build-selection.md), [selection configuration](build-selection-config.md), [pinned sources](build-setup-sources.md), [delivery planning](build-setup-plan.md) |
| Completed controller history maintenance | [Explicit archival](build-setup-history.md) |
| Prebuilt service compatibility | [Runtime validation](build-runtime.md) |
| Authenticated prebuilt target deployment | [Target policy](build-target-policy.md), [staging](build-target-artifacts.md), [dispatcher](build-target-service.md), [activation](build-target-activation.md), [process entrypoint](build-target-entrypoint.md), [SSH client](build-target-client.md) |
| Target enrollment | [Target setup](build-target-setup.md), [target configuration](build-target-config.md) |
| Delivery and recovery | [Artifact delivery](build-delivery.md), [transaction safety](transaction-safety.md) |
