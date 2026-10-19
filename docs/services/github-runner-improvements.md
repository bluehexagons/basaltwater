# GitHub runner improvements

## Goal

Make a persistent GitHub Actions runner installable before GitHub registration,
allow registration later from setup or the web panel, and support either one
repository or an organization with several repositories. This targets a VM used
by one GitHub user or organization; it does not add multi-tenant administration.

## Planned behavior

- Setup without a registration token installs the runner files and policy in a
  **Pending registration** state. It leaves the runner service disabled and
  does not report the runner as ready. A later setup with
  `--ci-registration-token-file` registers and activates it. Failed attempts
  remain retryable. Routine setup of an already registered runner does not
  require a token; replacement remains an explicit recovery operation.
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

GitHub's runner registration token expires after one hour and cannot be made
long-lived. It is needed only to register a runner; normal reconnect uses the
credentials created during registration. Do not pass a PAT or GitHub App key to
the runner. If automated token creation is later needed, evaluate a GitHub App
on the trusted Basaltwater control plane: its private key can mint short-lived
API credentials and a registration token just before use, but those generated
tokens also expire. See GitHub's [runner registration API](https://docs.github.com/en/rest/actions/self-hosted-runners),
[runner group guidance](https://docs.github.com/en/actions/hosting-your-own-runners/managing-access-to-self-hosted-runners-using-groups),
and [App private-key guidance](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps).

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

GitHub App-based token minting is an optional later enhancement, separate from
the manual token flow.
