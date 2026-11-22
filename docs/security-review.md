# Security review

The audit examined revision `2c78b19b` on 18 November 2026. Six of its
fourteen findings were high priority. The descriptions under **High-priority
findings**, **Access policy and credential handling**, and **Build, update and
release integrity** describe that revision. Use the remediation record for
current behavior and rollout requirements.

## Review scope and verification

The review followed selected trust boundaries from setup or service entrypoints
through their policy, execution identity and generated configuration. It covered
controller and target setup, Proxmox, build and release workflows, broker and
task permissions, network services, storage recovery and workstation setup.
These existing controls narrow the findings and should survive remediation.

- Builder SSH uses strict host-key checking and disables the ambient agent.
  Builder grants constrain projects and resources, target grants independently
  authorize applications, and build commands drop privileges and capabilities.
  Network access and the shared worker identity still matter when choosing
  which projects may share a builder. [Transport](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/build_client.py#L855) ·
  [Build execution](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/build_execution.py#L75) · [Target authorization](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/build_target_policy.py#L426)
- Standard managed Codex setup can grant account-level task scheduling through
  MCP, including when an inspection task has a read-only shell profile.
  Check both the provider profile and effective tool grants when allowing
  unattended diagnostics. [Task worker configuration](agents/agent-tasks.md)
- Samba checks mount identity during permission changes. Managed CIFS mounts
  request sealing and default to owner-only local access. Syncthing validates
  configured folders, and notification webhooks verify HTTPS peers by default
  and reject redirects. These checks apply to their respective code paths.
  [CIFS setup](../smb/smb_mount_steps.py) · [Syncthing guard](../sync/service_tools/syncthing_guard.py) ·
  [Webhook sender](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/notifications.py#L887)

At the reviewed revision, the focused checks passed 20 image catalog and
staging tests, then 833 tests covering the security suite and related modules.
These included guest enrollment, build permissions, gateway configuration,
credentials, HomeBox and Gogs.
Contributors can rerun the security suite with `make test SUITE=security`.
The remediation record reports which acceptance checks have since passed.

The base Python package declares no runtime dependencies. On the review date,
PyPI's release-specific records returned no active advisories for the six
declared build and optional dependency minimum versions. The same check covered
the four installed development and completion dependencies and their ten
transitive dependencies on the audit platform. A pattern scan of the 1,678
tracked files found no matches for private-key blocks with an encoded body,
classic GitHub tokens, AWS access-key IDs or the checked Slack token formats.
Repeating those checks across 1,682 tracked paths after remediation also found
zero matches. Git history, deployed secrets, other dependency resolutions,
distribution packages and third-party service internals remain outside that
evidence. [Package metadata](../pyproject.toml) ·
[PyPI release API](https://docs.pypi.org/api/json/#get-a-release)

The original audit used source and fixtures. The remediation record adds
isolated service checks. Actual listener reachability, effective permissions
and past credential exposure still require evidence from each deployment.
Earlier live qualification and its remaining gaps are recorded in the
[security configuration plan](plans/security-configuration.md#verification-and-release-qualification).

## Choose a topic

<a id="remediation-record"></a>
<a id="follow-up-review"></a>

- [Security review remediation record](security-review-remediation.md) Read the remediation and follow-up review evidence.

<a id="high-priority-findings"></a>
<a id="proxmox-can-replace-a-verified-guest-ssh-key-on-a-rerun"></a>
<a id="panel-and-pairing-backends-receive-the-browsers-basic-credential"></a>
<a id="debian-desktop-setup-writes-into-a-users-home-as-root"></a>
<a id="windows-and-cachyos-elevate-code-from-the-desktop-users-installation"></a>
<a id="homebox-recovery-and-gogs-setup-can-cross-nested-mounts"></a>
<a id="ordinary-static-deployments-trust-the-build-output-tree"></a>
<a id="access-policy-and-credential-handling"></a>
<a id="build-update-and-release-integrity"></a>

- [Security review technical findings](security-review-findings.md) Review high-priority issues, access policy, and build and release integrity.
