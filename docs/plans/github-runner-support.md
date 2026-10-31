# GitHub Runner on shared build hosts

Status: dedicated Debian runner setup is implemented; composition with webhooks
and managed on-demand builds is planned. Selected native tokenless setup, rerun
and pending-state reboot checks pass; real GitHub job, runner-group and registered
reboot qualification remains open. See [current usage](../services/github-runner.md)
and [operations](../services/github-runner-operations.md).

The immediate goal is the Debian three-role VM in the
[shared build-host plan](cicd-manifest-reuse.md), not another workflow scheduler.

## Existing contract

Keep GitHub's official runner. GitHub owns workflow dispatch, labels/groups,
reruns, cancellation, logs and Actions artifacts. Basaltwater installs tools,
enforces local policy and coordinates host capacity. Workflows may call
project-owned build commands; do not translate Actions YAML.

Repository/organization registration, multiple trusted repositories, pending
registration and CLI/panel token handoff exist. Long-lived GitHub management
credentials remain on the controller. Registration tokens use the private
one-time channel; reconnect credentials stay private to their runner identity.
See [registration status and remaining checks](../services/github-runner-improvements.md).

Persistent workflows are trusted code, not sandboxed tenants. Preserve the
private-repository, human-allowlist, same-repository PR and supported-event
restrictions. Unknown identities fail closed before workflow steps; commit
author text is not authorization.

## Next implementation

1. **Role composition:** extend setup/profile builders so runners coexist with
   webhook and on-demand roles. Save each capability and tool selection without
   silently ignoring flags. The dedicated profile is not the combined-host API.
2. **Existing-runner conversion:** preserve declared repository scope, allowed
   users, labels and valid registration. Validate identities before changes;
   require explicit re-enrollment when needed and retain private recovery evidence.
3. **Shared capacity:** replace the runner-only lock with the common host
   coordinator. Qualify bounded waiting, nested builds and child cleanup.
4. **Maintenance:** drain all installed entry points before tool/policy updates
   and reboot. Recheck policy after capacity acquisition. Fail assigned-but-waiting
   jobs before steps when draining; this is not upstream workflow cancellation.

GitHub remains authoritative for its remote queue. Action setup/downloads may
precede hooks; local capacity does not guarantee FIFO ordering or zero waiting
resource use. Nested lease reuse must be coordinator-validated, never an
environment-variable bypass. Panel availability must not govern execution.

## Acceptance

- Fresh setup, conversion and saved reruns retain every requested role and
  runner setting; valid registration survives routine updates.
- Pending, valid, expired and failed registration paths remain safe and
  retryable; no token appears in durable state, logs, URLs or responses.
- Real allowed jobs run; denied/malformed events execute no workflow steps,
  including `if: always()` canaries.
- Mixed-role waiting, nested builds, cancellation, drain, policy changes and
  surviving children preserve authorization and capacity ownership.
- Restart and reboot preserve working registration, role state and credentials.
  Offline tests alone do not close live GitHub qualification.

## Later scope

Panel controls follow the shared backend and existing authenticated registration
channel; GitHub workflow mutations stay upstream initially. Native
[Windows runners/build hosts](windows-support.md) need separate process and
permissions adapters. The current WSL-backed runner is unqualified; neither
Windows path blocks the Debian deliverable. Untrusted jobs and privileged
container access need separate isolation and authorization contracts.
