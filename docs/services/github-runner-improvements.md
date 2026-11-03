# GitHub runner registration status

Debian pending registration, repository/organization scope and private CLI/panel
enrollment are implemented. Selected tokenless checks and actual CLI registration,
manual dispatch, denied rerun/drain, nested builds/cancellation and registered
reboot pass on a shared Debian host; see the
[dated evidence](../testing/evidence.md#shared-debian-build-and-ci-host).
Runner-group and broader event/registration qualification remain open.
Use the [quick start](github-runner.md) and
[operations reference](github-runner-operations.md) for commands and recovery.

## Available behavior

- Tokenless setup leaves an unregistered runner pending with its service
  disabled. Later setup or the panel can register it; invalid/expired tokens
  leave a retryable state.
- Routine setup preserves valid registration without a token. Scope, group and
  label changes use the managed replacement path and retain retired credentials
  privately for recovery.
- Repository scope supports up to 20 independently registered repositories;
  organization scope supports an optional runner group. Runners share one
  host slot with managed on-demand and webhook builds.
- `--ci-register-with-gh` obtains a short-lived registration token on the
  controller; the action is not replayed from saved setup. Reconnect credentials
  remain on the runner.
- The authenticated HTTPS panel uses a one-time volatile handoff to a narrow
  privileged operation. Tokens are absent from durable requests, setup state,
  logs, URLs and responses.

## Remaining checks and work

1. Extend live checks to organization/group, registered multi-repository,
   push/PR and broader denied events, panel enrollment and expired tokens.
2. Extend the selected guardian/managed-worker SIGKILL and inspected-recovery
   checks to other process-loss and cleanup-acknowledgement timings under the
   [shared-host plan](../plans/github-runner-support.md).
3. Add broader panel controls only after their typed backend operations exist.

The [coverage map](../plans/testing-coverage.md) separates selected native GitHub
results from automated lifecycle, policy, registration-channel and unit tests.
GitHub App-based token minting is a later enhancement.
