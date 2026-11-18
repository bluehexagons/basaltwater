# Security review

**Reviewed 18 November 2026 at `2c78b19b`.** Start by binding
Proxmox SSH enrollment to the intended guest, keeping reusable
panel credentials at the authentication proxy, and removing user-writable
inputs from privileged workstation setup. Each matters during an ordinary
operator action. The six high-priority findings can affect administrator
actions, expose reusable credentials or reach data outside a service's
intended directory. Runtime remediation remains open.

## High-priority findings

### Proxmox can replace a verified guest SSH key on a rerun

On fresh VM or container provisioning and matching saved-guest setup, the
controller authenticates the Proxmox node. The node then scans the guest IP
for its Ed25519 key. A successful scan replaces the address entry in both
the workspace and the invoking user's SSH trust file. If the address answers
for a different guest, subsequent strict SSH can send the setup payload and
its credentials to that endpoint. An isolated provisioning bridge reduces this
risk. [Guest enrollment](../lib/proxmox_guest.py#L1178) ·
[Trust-store replacement](../lib/ssh_enrollment.py#L132) · [Operator guidance](operations/ssh.md)

**Fix and verify.** Read the public key through the node's authenticated
VMID or CTID management channel. Require independently verified authorization
for a key change. Cover fresh enrollment, a matching pin, a conflicting pin
and unavailable guest identity. On uncertainty, preserve both trust stores
and stop before sending the setup payload.

### Panel and pairing backends receive the browser's Basic credential

Nginx authenticates the browser. On the ordinary web-panel and device-pairing
routes it also forwards the original request headers, including
`Authorization`, to the application socket. A compromised backend can see
the reusable password during an authenticated visit over HTTPS. This is
especially consequential when the main panel login also authorizes integrated
approval decisions. Dedicated approval and desktop routes already clear the
header. Use distinct portal passwords and treat the application backends as
trusted with their respective logins until the proxy is corrected.
[Panel proxy](../common/web_panel_steps.py#L477) ·
[Pairing proxy](../common/t3code_steps.py#L1794) ·
[Approval trust model](agents/privilege-approvals.md) ·
[Nginx request-header defaults](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)

**Fix and verify.** Strip the credential on every route where proxy
authentication ends. Pass only the authenticated identity where the backend
needs it. With synthetic credentials, verify header absence on normal panel
and pairing requests, rejection of unauthenticated requests, and successful
bearer authentication on the separate notification-ingest route.

### Debian desktop setup writes into a user's home as root

Graphical workstation setup creates browser defaults, XFCE helpers and
autostart files under the desktop user's home. Several writes use the setup
process's privileges, and browser setup also changes ownership recursively
under `.config` and `.local`. On a rerun, the account controls those directories.
An unexpected link can direct a privileged write outside the intended tree,
and unrelated files can acquire new ownership. Until fixed, treat the home
contents as trusted input to these setup steps. [Profile selection](../plugins/desktop.py#L80) ·
[Browser defaults](../desktop/browser_steps.py#L439) · [XFCE setup](../desktop/desktop_environment_steps.py#L73)

**Fix and verify.** Create, replace and retire per-user files as the desktop
identity. Limit ownership changes to files managed by that step. Cover a fresh
home and a populated rerun, checking the execution identity and preservation of
unrelated files and linked destinations.

### Windows and CachyOS elevate code from the desktop user's installation

Windows Sunshine repair starts elevated PowerShell and loads a helper again
from the per-user source tree when the installed service needs attention.
CachyOS SSH setup invokes a checkout-local Python helper with sudo, and the
helper imports from that checkout. A process able to modify the installation
can change the code an administrator subsequently authorizes. Both paths
require an elevation decision or existing authority. Until the helpers are
isolated, use the operating system's service manager directly when the
desktop installation is writable by less-trusted processes.
[Windows service repair](../windows/Sunshine.Common.ps1#L41) ·
[CachyOS invocation](../common/cachyos_ssh.py#L60) ·
[Privileged imports](../common/service_tools/cachyos_ssh.py#L15)

**Fix and verify.** Move the privileged operation and all its imports to an
administrator-controlled installation, or use a fixed native service
operation. Inspect the actual UAC and sudo launch paths in tests. A modified
user installation must have no influence over the elevated code, including
during repair or removal.

### HomeBox recovery and Gogs setup can cross nested mounts

Both services check the filesystem containing the configured data root.
HomeBox backup enumerates the descendants. Restore clears the live tree
recursively, including during recovery from a failed update. An independent
mount below that root can enter a backup, and files on a writable child mount
can be deleted before restore reports failure. Gogs setup applies recursive
ownership changes below its root. Keep independent filesystems and bind mounts
outside these data trees. [HomeBox backup](../web/homebox_steps.py#L627) ·
[Restore](../web/homebox_steps.py#L703) · [Gogs setup](../web/gogs_steps.py#L401)

**Fix and verify.** Detect nested mounts before backup or mutation and check
mount identity during traversal. Exercise a separate filesystem and a
same-device bind mount. In both cases verify that backups exclude independent
data and that setup, restore and failed-update recovery preserve its bytes
and ownership. The Samba walker already checks mount IDs on open descriptors.
[Samba traversal](../smb/samba_steps.py#L294)

### Ordinary static deployments trust the build output tree

Source staging rejects symlinks before the build account runs. For an on-target
`type: "static"` build, activation then checks that the output directory
exists. Its ordinary generated Nginx location permits link traversal under
default settings. A link left by a build can therefore publish a file outside
the selected output that the Nginx worker can read. Godot exports and
selected-builder bundles have separate validation paths. [Post-build check](../lib/deployment.py#L1451) ·
[Nginx location](../lib/nginx_config.py#L405) · [Deployment guide](services/deployments.md#static-site)

**Fix and verify.** Validate and freeze the final public tree before replacing
the active release. Check the output root, its ancestors and its members.
Enforce containment in the generated location as well. Verify ordinary files
still serve at root and nested routes, and rejected output preserves the
previous release.

## Access policy and credential handling

| Finding and exposure | Action and acceptance |
| --- | --- |
| **Gateway restrictions follow the route name.** An authorized publisher or existing forward can map a second listener to the same local T3 or pairing backend with a broader source policy. Application authentication still applies. [Gateway policy](../common/service_tools/basaltwater_web.py#L424) · [T3 registration](../common/t3code_steps.py#L1963) | Review all mappings to administrative services. Bind their minimum source restriction to the protected upstream. Verify two mappings cannot widen access after creation, narrowing or reconciliation, for IPv4 and IPv6. |
| **Scrub confirmation depends on trusting the requester account.** Shares & Data confirms the action in the panel. The broker automatically authorizes `data.run`, including deletion and acceptance of changed content. The worker still checks eligible findings and file identities. Any process with the configured requester identity and socket access shares that bounded authority. [Broker policy](../lib/privilege_policy.py#L155) · [Worker checks](../common/service_tools/data_job.py#L210) · [Documented trust model](agents/privilege-approvals.md) | Where an independent human decision is required, withhold this authority from unattended accounts. Require decision-side approval bound to the exact destructive action and finding. Verify requester-only, stale and repeated decisions cannot authorize it. |
| **Workspace credential names span services and hosts.** A Git credential is installed for a chosen origin, but the controller looks up its value by username alone. A matching name can also resolve an SMB mount password or initial Gogs admin password. A collision can deliver a valid secret to an unintended target. [Resolution](../lib/credentials.py#L236) · [Operator guidance](agents/credentials.md#workspace-credential-store) | Use distinct credential names or separate workspaces today. Scope stored identities to the intended service and destination. Verify independent secrets for identical usernames and require a decision for ambiguous legacy entries. |
| **Initial Gogs passwords enter target process arguments.** The displayed command is redacted and the saved credentials file is private. Account creation still supplies the password in child arguments, where local process inspection or execution auditing can capture it. Both generated and workspace passwords take this path. [Account creation](../web/gogs_steps.py#L1072) | Use a unique bootstrap password and rotate it after setup. Adopt an input channel supported by the deployed Gogs version that keeps the value out of process arguments. Verify both password sources through account creation, including failure diagnostics. |

## Build, update and release integrity

| Finding and exposure | Action and acceptance |
| --- | --- |
| **A routine cloud-image catalog refresh removes verification.** The reviewed catalog pins each Debian snapshot and SHA-512. The updater's default changes entries to `latest` and clears their digests. Provisioning then accepts new or cached catalog images with a warning. The existing default-alias test detects a bad catalog if run before publication. [Updater](../scripts/update_cloud_images.py#L143) · [Provisioning](../lib/proxmox_vm.py#L1690) · [Catalog test](../tests/test_cloud_images.py#L19) | Follow the [pinned refresh procedure](reference/machine-types.md#provisioning-a-proxmox-vm). Make an ordinary refresh retain verified pins, validate every alias, and reject missing digests before downloading or reusing an image. |
| **Antistatic installs identify binary releases by tag.** Server and database setup select a stable GitHub asset and download over HTTPS. Installation and later reuse trust the tag and presence of the local executable, without an expected content digest. Unexpected bytes can become the service executable. Both services run as dedicated users. [Release selection](../game/antistatic_steps.py#L201) · [Installer](../lib/release_management.py#L304) | Bind each architecture and tag to an expected digest from trusted metadata and verify staged bytes before publication. Detect same-tag drift on reuse. Test mismatch and missing metadata with the previous binary and release state intact. |
| **Release builds have repository write permission.** Tag tests run read-only. A later job builds and smoke-tests the wheel with `contents: write` and the checkout action's default persisted credential. A compromised build dependency in that job would share authority intended for publishing. [Release workflow](../.github/workflows/release.yml#L46) · [Checkout credential behavior](https://github.com/actions/checkout#usage) | Build and test the wheel in a read-only job with credential persistence disabled. Give write permission only to a publishing job that takes the identified, verified artifact. Verify the published digest matches the tested wheel and review both jobs' effective permissions. |
| **Windows artifact receipts identify only part of the input.** The native job verifies HEAD and the entry script, then runs from the live checkout. Changes to another build input can reach an artifact whose receipt still names the clean commit. The upload digest confirms the bytes received. [Native job](../windows/Invoke-BasaltwaterJob.ps1#L56) | Use a fresh private checkout for trusted jobs today. Execute from a snapshot of the declared revision and record any additional inputs. Verify that modifying a second source file cannot change a revision-attributed artifact. |

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
  which projects may share a builder. [Transport](../lib/build_client.py#L855) ·
  [Build execution](../lib/build_execution.py#L75) · [Target authorization](../lib/build_target_policy.py#L426)
- Standard managed Codex setup can grant account-level task scheduling through
  MCP, including when an inspection task has a read-only shell profile.
  Check both the provider profile and effective tool grants when allowing
  unattended diagnostics. [Task worker configuration](agents/agent-tasks.md)
- Samba checks mount identity during permission changes. Managed CIFS mounts
  request sealing and default to owner-only local access. Syncthing validates
  configured folders, and notification webhooks verify HTTPS peers by default
  and reject redirects. These checks apply to their respective code paths.
  [CIFS setup](../smb/smb_mount_steps.py) · [Syncthing guard](../sync/service_tools/syncthing_guard.py) ·
  [Webhook sender](../lib/notifications.py#L887)

The focused checks passed 20 image catalog and staging tests, then 833 tests
covering the security suite and related modules, including guest enrollment,
build permissions, gateway configuration, credentials, HomeBox and Gogs.
Contributors can rerun the security suite with `make test SUITE=security`.
The acceptance checks under each finding describe additional behavior to verify
after remediation.

The base Python package declares no runtime dependencies. On the review date,
PyPI's release-specific records returned no active advisories for the six
declared build and optional dependency minimum versions. The same check covered
the four installed development and completion dependencies and their ten
transitive dependencies on the audit platform. A pattern scan of the 1,678
tracked files found no matches for private-key blocks with an encoded body,
classic GitHub tokens, AWS access-key IDs or the checked Slack token formats.
The scan covered selected formats in the current tree. Git history, deployed
secrets, other dependency resolutions, distribution packages and third-party
service internals remain outside that evidence. [Package metadata](../pyproject.toml) ·
[PyPI release API](https://docs.pypi.org/api/json/#get-a-release)

This was a source and fixture audit. Actual listener reachability, effective
host permissions, credential exposure and native recovery outcomes require
deployment-specific verification. Earlier live qualification and its remaining
gaps are recorded in the [security configuration plan](plans/security-configuration.md#verification-and-release-qualification).
