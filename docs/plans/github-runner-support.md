# GitHub Runner on shared build hosts

Status: the current Debian implementation includes repository/organization
registration, a local identity gate, a shared runner slot and deferred CLI/panel
registration. Live Debian/GitHub/reboot qualification remains open; see
[current operations](../services/github-runner-operations.md). Windows currently
uses an unqualified WSL-backed gate. The composition below is planned, not a
claim that either platform is production-qualified.

## Direction and compatibility

GitHub Runner becomes an optional capability of the
[shared build host](cicd-manifest-reuse.md), alongside webhook and on-demand
builds. Replace the dedicated-profile restrictions, unused setup interfaces and
state directly where simpler; no migration or backwards compatibility is
required for runner/build-server/webhook systems. Preserve standard
build-on-target deployment and useful identity, credential and cleanup guards.

Keep GitHub's official `actions/runner`. GitHub schedules workflows, selects
jobs using labels/groups and owns dispatch, rerun, cancellation, workflow logs
and its artifacts. Basaltwater installs the selected tools, validates local
policy, coordinates host capacity and can export declared outputs through the
common artifact interface. Do not translate Actions YAML into a new workflow
engine or implicitly grant workflows deployment privileges.

Support repository or organization registration, including several trusted
repositories on one host. Preserve scope validation, short-lived registration
tokens, pending registration and one-time authenticated HTTPS handoff from the
[implemented registration work](../services/github-runner-improvements.md).
Long-lived GitHub management credentials stay on the controller. Runner reconnect
credentials stay private to their identity; unrelated build jobs cannot read them.

## Trust, capacity and lifecycle

Treat persistent colocated workflows as trusted code. Use separate identities
and writable state for runner and other build services. Job identities have no
sudo, deployment keys, privileged container socket or unrelated operator
credentials. Selected read-only toolchains can be shared. Docker is optional;
a workflow requesting it still requires separately provisioned support.
Disposable untrusted jobs need a separately qualified isolation boundary.

Keep one authoritative policy across setup, panel and local gate. Match
repository/organization and reliable event identity, not commit author text.
Retain the current private-repository, human allowlist, same-repository PR and
supported-trigger restrictions until a replacement is explicitly qualified;
unknown identities/events fail closed before workflow steps. GitHub's
[job hooks](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts)
and [hook ADR](https://github.com/actions/runner/blob/main/docs/adrs/1751-runner-job-hooks.md)
do not establish hostile-code isolation or GitHub-side authorization.

Replace independent CI/runner locks with the common host capacity coordinator.
Acquire before supported workflow execution, account for waiting in GitHub's
job timeout and hold capacity through owned cleanup. Action downloads/setup may
precede the hook; do not claim a global FIFO queue or zero resource use while
waiting. Nested Basaltwater builds must not deadlock on a second slot. Test
worker/guardian failure and surviving children before reporting slot release.
Recheck current policy after acquiring capacity, immediately before workflow
steps; authorization granted before a wait does not survive a policy change.

Drain all installed entry points before setup, policy/toolchain replacement or
reboot. Report assigned GitHub jobs waiting locally, active jobs and remaining
admission honestly. Coordinate listener changes with GitHub assignment rather
than describing a local drain as control of GitHub's remote queue. Keep job and
runner execution independent of panel or notification availability.
Fail assigned-but-waiting workers before their steps when draining, with a
bounded local reason; active jobs finish normally. This local hook failure is
not a claim that Basaltwater cancelled the workflow upstream. Qualify policy
edits and drain while a job is waiting, not only before assignment.

## Panel and platform scope

Use the shared panel design for registration, scope/labels/version, local health,
bounded gate outcomes, capacity state and links to GitHub. Registration uses the
existing one-time secret channel; allowed-user edits and drain-aware lifecycle
actions use typed authorized operations. GitHub workflow mutations remain
upstream initially. Do not store a broad GitHub token to imitate its UI.

Debian uses native services. The [Windows plan](windows-support.md) supplies
native workers for software, game and dedicated-server builds without WSL.
Both platforms share policy, artifact and capacity contracts, with separate
permissions/process adapters. Windows owner-login proof is acceptable first;
unattended headless startup needs additional qualification. The login account
is the dedicated build account, separate from the human operator.

## Acceptance

- Fresh and repeated setup, pending/valid/expired registration, scope changes,
  credential isolation, restart/re-registration and explicitly supported events.
- Allowed jobs run; denied, malformed and missing identities run no workflow
  steps, including `if: always()` canaries. Hook checks do not imply sandboxing.
- Runner and webhook/on-demand roles coexist under bounded capacity; nested
  builds, cancellation, drain, teardown and interrupted-state recovery work.
- Panel inspection and authorized policy/registration actions preserve working
  state on error and show unavailable facts without assuming readiness.
- Real GitHub-connected jobs and reboot evidence on Debian, plus native
  Windows project builds and session/process checks on Windows. Offline tests
  alone do not satisfy these platform gates.
