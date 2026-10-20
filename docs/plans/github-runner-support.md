# GitHub self-hosted runner support

Status: Debian phase 1 implementation is available, with offline identity,
credential and worker-denial checks. Clean Debian VM, reboot, GitHub-connected
trigger qualification and upgrade/re-registration evidence remain open; do not
claim production readiness yet. See [Debian runner operations](../services/github-runner-operations.md).
Deferred registration, organization scope and the panel's one-time HTTPS
registration handoff are implemented; see the
[improvement delivery plan](../services/github-runner-improvements.md).
Native Windows runners with WSL access are implemented; live Windows 11/WSL
qualification remains open. See the [Windows runner guide](../workstations/windows-github-runner.md).

## Decisions

- Use GitHub's official `actions/runner` application, with a persistent service
  on Debian and owner-login startup on Windows.
- Support up to 20 repository registrations with a shared job slot, or one organization. GitHub
  runner groups control organization repository access; ephemeral runners remain deferred.
- Do not require or install Docker as part of runner setup. The current CI
  wrapper invokes Docker to run Debian Trixie checks on Ubuntu-hosted workers.
  If that workflow runs unchanged on a self-hosted runner, it still needs
  Docker; adapting CI to run natively on Debian is a separate change.
- Defer a web-panel approval workflow. Initially accept repeatable
  `--ci-allowed-user <login>` options. Allowlisted authors may run jobs;
  non-allowlisted, missing, or unrecognized identities must fail closed before
  workflow steps execute when no approval mechanism is configured. This is a
  runner-side execution gate, not GitHub-side queue control or PR approval.
- Treat jobs from allowed authors as trusted code on the persistent VM. Keep
  production runners dedicated to CI and do not put unrelated production
  credentials on them.

## Initial test environment

- Use one manually provisioned, current stable Windows 11 x64 VM on Proxmox
  for Windows runner qualification, with Antistatic build/tests as the first
  workload. Follow the [runner-only Windows plan](windows-support.md); WSL
  and a managed desktop are not requirements. Basaltwater does not provision
  the Windows VM.
- Run the CI instance as a dedicated non-administrator account without unrelated
  desktop/T3 credentials. Owner-login startup is sufficient initially. Treat
  allowlisted workflow code as trusted code with access to that account's
  credentials. Optional T3 Code/package installation is independent of CI setup.
- Windows qualification does not replace phase 1's native Debian qualification.

## Phase 1 start gates

- The lightweight allowlist candidate is GitHub's `ACTIONS_RUNNER_HOOK_JOB_STARTED`
  hook. It runs after GitHub assigns a job but before workflow steps; a nonzero
  exit fails the job rather than holding it for approval. The runner project's
  [hook ADR](https://github.com/actions/runner/blob/main/docs/adrs/1751-runner-job-hooks.md)
  describes hooks as setup, cleanup, and telemetry tools, not a security policy
  mechanism. Qualify the hook as a local fail-closed guard, keep its script and
  configuration outside job-writable paths, and do not describe it as
  GitHub-side authorization. If the gate cannot be trusted to run for every job
  before user code, do not send untrusted jobs to the persistent runner. See
  [GitHub's hook behavior](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts).
- Decide which repository visibility and triggers are supported. GitHub warns
  that code from pull requests can compromise persistent self-hosted runners
  and [recommends private repositories](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).
  Define identity by trigger (for example, PR author for pull requests and
  triggering actor for push or manual dispatch); never use commit author text
  as authorization, and deny events whose identity cannot be established
  reliably.
- Use one authoritative allowlist. If the panel can change it, define its
  authorization and audit path; otherwise show the CLI-configured value
  read-only in phase 1. Record only bounded gate outcomes, not full event
  payloads.
- Supply a short-lived registration token for the configured repository or
  organization securely; GitHub
  expires it after one hour ([runner setup](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)).
  The configured runner retains local credentials to reconnect, so run it as a
  dedicated unprivileged account with no sudo or Docker access and protect its
  credential files. Workflow code from an allowlisted author runs as this
  account and must be treated as trusted.

## Initial phases

### 1. Debian runner

Choose the initial Debian release and architecture before implementation
(Debian Trixie x86-64 is the current project CI baseline). Add a Debian runner
setup type/profile for a single repository. Install and configure the official
runner service, accept registration credentials without persisting a long-lived
GitHub API credential, and apply the fail-closed author allowlist. Confirm that
supported workflow triggers expose reliable author identity to the runner-side
gate; deny any trigger that cannot be identified safely. Document service
health, runner updates, deregistration, and recovery.

The Debian-only web panel should show runner health, repository and labels,
configured allowlist, and recent gate outcomes. Allowlist editing can be added
only after its authorization and audit path is defined; otherwise show the
value read-only. Pending registration through HTTPS is implemented;
panel policy editing and lifecycle controls remain deferred. Setup replacement
and a host-wide queue are implemented.
This is separate from the existing **Review GitHub Actions failures** template,
which inspects workflow failures but does not report local runner health or
policy.

**Phase 1 is ready when** a clean Debian VM can be set up for one repository,
the service survives reboot, runner upgrade and re-registration are documented,
and a Docker-free smoke workflow succeeds. For every supported trigger, test an
allowlisted identity, a denied identity, and missing or malformed identity;
verify denied jobs execute no workflow steps. The panel exposes the configured
state. Docker must not be a setup prerequisite.

### 2. Windows runner

Native Windows installation, repository/organization registration, shared identity
evaluation and serial workflow steps are implemented. Limited login tasks run as
the WSL owner because distributions are account-scoped; job code shares that
owner's credentials and is trusted. There is no Windows panel or pre-login service.
The current WSL-backed path lacks live Windows 11 registration, denied-job,
queue, restart and build evidence; see the Windows guide. Replacement
qualification follows the runner-only plan below. Multi-repository source builds
use workflow checkouts or locked dependencies, independently of runner registrations.

The [runner-only Windows plan](windows-support.md) targets current Windows 11
and Antistatic build/tests, with a small panel for current system/runner status,
allowed-user editing, and outgoing webhooks. Target one native gate/panel
implementation; the current unused Windows code can be replaced without
migration or backwards compatibility. Preserve shared policy safeguards.
Owner-login startup is sufficient; pre-login service mode, desktop workflows,
spoke roles, and notification ingest are excluded. Current panel and
policy-editing limitations remain until changes are delivered and qualified.

### Registration extensions

Organization scope and deferred registration now extend the initial implementation
through the [runner improvements](../services/github-runner-improvements.md).
Disposable job environments remain deferred. Decide the isolation boundary and
container runtime separately; Docker is not a default requirement.
