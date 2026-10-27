# Project roadmap

Basaltwater is a small infrastructure reconciler for Debian and Proxmox.
Validate requested state, apply bounded changes, verify the result and preserve
a recovery path. The [plan index](README.md) maps related work; the
[release matrix](../release-qualification.md) gates publication.

## Current baseline

- The package is `2.0.0rc1`; the preview tag is not published.
- Managed operations have durable guards, state validation, unit/Nginx recovery
  and deployment rollback. They do not reverse arbitrary scripts, packages,
  database migrations or application data.
- Selected-builder setup, saved revision updates, verified artifact delivery
  and retained target reconciliation are implemented for the bounded Debian
  static/Go path. Explicit builder retention and offline recovery inspection
  are available. See [current usage](../services/build-systems.md).
- Webhook CI/CD and GitHub Actions runners exist separately. A host combining
  both with managed on-demand builds is not implemented or qualified.

Selected live and controlled checks are summarized in the
[coverage map](testing-coverage.md). Passing CI is not full-host qualification.

## Immediate priority: one shared Debian build/CI host

Deliver a VM that can host on-demand builds, signed webhooks and GitHub Actions
runners together. Other deployments select this VM with `--build-on`; that flag
does not enable build services on the VM itself.

Follow the [shared build-host plan](cicd-manifest-reuse.md):

1. Compose the three roles in setup and saved configuration. Convert an
   existing runner without silently dropping repository scope, allowed users,
   labels or valid registration.
2. Share capacity and draining across entry points. Begin with one heavy-job
   slot; qualify waiting, nested builds, cancellation and child cleanup.
3. Adapt webhooks to the common manifest/artifact path while preserving
   signature, repository/branch and deployment authorization.
4. Qualify fresh setup, existing-runner conversion, mixed-role execution,
   reruns and reboot. Keep credentials and unfinished recovery evidence intact.

A combined setup command is documented only after its parser, composition and
backend behavior exist. Optional panel controls follow the common backend;
they are not required to finish the initial three-role host.

## Other priorities

| Work | Next bounded result |
| --- | --- |
| Stable-release quality | Run the remaining exact-candidate release cases; preserve installer, wheel, metadata and affected CI checks. |
| Read-only planning and drift | Define stable categories and unavailable-fact handling for services, managed files, packages and deployments. Verify observations again before apply. |
| Security monitoring | Detect when journal/audit retention passes an old cursor; see [#108](https://github.com/bluehexagons/basaltwater/issues/108). |
| Recovery | Extend tested same-host backup/restore to off-host recovery; cover remaining worker, storage and interrupted-operation boundaries without automatic replay of uncertain work. |
| Network changes | Extend verified address handoff to reviewed Proxmox firewall changes, with saved artifacts, timed rollback and connectivity confirmation. |
| Deployment secrets | Add target-owned runtime bindings under the [secret-reference plan](deploy-secrets.md); retain secret-free artifact builds as the current default. |

Retention currently reclaims bytes through explicit local actions while keeping
input slots and replay history. Automatic bundle collection or history retirement
must define changed availability/replay guarantees before implementation. These
policies do not block the shared-host work.

## Later scope

- [Native Windows builds](windows-support.md), broader non-Debian support and
  the [mobile proposal](mobile-agent-support.md) follow the Debian host.
- [Game publishing](game-publishing.md) needs live provider/rendering checks;
  Steam default release stays manual. Public-post automation follows the
  [communications plan](game-release-communications.md).
- New Godot bundles, provider/plugin APIs and application services need their
  own ownership, update, recovery and qualification contracts.
- A general fleet scheduler, mandatory container platform and automatic
  resolution of uncertain operations are outside the current scope.
