# GitHub runner improvements

## Goal

Make a persistent GitHub Actions runner installable before GitHub registration,
allow registration later from setup or the web panel, and support either one
repository or an organization with several repositories. This targets a VM used
by one GitHub user or organization; it does not add multi-tenant administration.

## Implemented behavior

- Setup without a registration token installs the runner files and policy in a
  **Pending registration** state. It leaves the runner service disabled and
  does not report the runner as ready. A later setup with
  `--ci-registration-token-file` or `--ci-register-with-gh` registers and
  activates it. Failed attempts remain retryable. Routine setup of an already
  registered runner does not require a token; replacement remains an explicit
  recovery operation.
- Runner scope is either a repository or an organization. Organization scope
  records the organization and optional GitHub runner group. GitHub runner group
  access controls which repositories can assign work; the local gate continues
  to enforce the configured organization and human-actor policy.
- The panel shows registration state and scope-specific instructions. A
  password field submits a one-time token over an authenticated HTTPS POST. The
  unprivileged panel must hand the token to a narrowly scoped privileged
  registration operation through a volatile channel. Do not send it through
  the durable action broker or retain it in panel state, setup configuration,
  logs, URLs, or responses.
- The guide explains where to create a repository or organization runner token,
  how to submit it through setup or the panel, and how to retry an expired or
  rejected token.

Registration tokens expire after one hour; normal reconnect needs no token.
`--ci-register-with-gh` uses the controller's authenticated GitHub CLI to request
one for the configured scope. It transfers only the registration token and is
never replayed from saved setup. Website and manual `gh api` instructions are
in the [quick start](github-runner.md#get-a-registration-token).

## Delivery and acceptance

1. Add pending-registration state and the later CLI registration path. Verify
   tokenless setup succeeds with the service disabled, valid registration
   activates it, invalid or expired tokens leave it retryable, and tokenless
   updates preserve an existing registration.
2. Add repository/organization scope to setup configuration, policy, runner
   gate, and documentation. Verify a runner can receive work from allowed
   repositories in its organization and rejects events from another
   organization.
3. Add the panel form and privileged one-time handoff. Test authentication,
   CSRF, TLS, body limits, error handling, and that the token appears in no
   durable request, response, URL, or log.
4. Qualify both scopes on disposable GitHub repositories, including reboot,
   pending-state recovery, and GitHub's runner-group repository selection.

Steps 1–3 are implemented with offline lifecycle, organization gate, registration
protocol, authenticated HTTP handler and generated-unit checks. The panel uses
a root-owned Unix socket with per-connection activation and kernel peer-UID
verification, rather than a durable request queue. Rejected tokens are
retryable; partial credentials are preserved for deliberate recovery, and a
completed registration with failed service activation can be repaired by
tokenless setup. Existing registered policies migrate without re-registration.
See the [quick start](github-runner.md) and
[operations reference](github-runner-operations.md) for usage and recovery.

Step 4 remains open: no disposable Debian VM or GitHub test organization was
available during implementation. Offline checks do not establish reboot,
GitHub delivery or runner-group repository-selection behavior. Record that live
evidence before claiming production qualification.

GitHub App-based token minting remains an optional later enhancement.
