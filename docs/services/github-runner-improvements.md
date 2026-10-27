# GitHub runner registration status

Debian pending registration, repository/organization scope and private CLI/panel
enrollment are implemented. Real GitHub delivery, runner-group selection and
reboot qualification remain open. Use the [quick start](github-runner.md) and
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
  runner-only host slot, not capacity with webhook or on-demand builds.
- `--ci-register-with-gh` obtains a short-lived registration token on the
  controller; the action is not replayed from saved setup. Reconnect credentials
  remain on the runner.
- The authenticated HTTPS panel uses a one-time volatile handoff to a narrow
  privileged operation. Tokens are absent from durable requests, setup state,
  logs, URLs and responses.

## Remaining checks and work

1. Qualify both scopes with disposable private GitHub repositories: pending and
   failed enrollment, permitted/denied events, runner-group selection, rerun and
   reboot.
2. Compose runners with webhook and managed build-host roles, then replace
   independent capacity/drain paths under the
   [shared-host plan](../plans/github-runner-support.md).
3. Add broader panel controls only after their typed backend operations exist.

Offline lifecycle, policy, registration-channel and unit tests do not establish
live GitHub behavior. GitHub App-based token minting is a later enhancement.
