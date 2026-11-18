# Security review

The audit examined revision `2c78b19b` on 18 November 2026. Six of its
fourteen findings were high priority. The descriptions under **High-priority
findings**, **Access policy and credential handling**, and **Build, update and
release integrity** describe that revision. Use the remediation record for
current behavior and rollout requirements.

## Remediation record

For an existing Debian target, update the controller first, then run
`basaltw deploy server.example` with the name of the saved setup when ready
to apply it. This reruns the selected setup on that target. The rows below
identify migrations and checks specific to each finding. [Saved setup operations](operations/operations.md#patch-and-redeploy)

| Finding | Change on main and operator action | Verification |
| --- | --- | --- |
| Proxmox guest SSH enrollment | Fresh and saved setups read the guest's Ed25519 key through its authenticated node and VMID or CTID. A conflicting pin or unavailable guest identity stops setup. VMs need a working QEMU guest agent. Verify intentional rotations before updating either trust store. | Fixture tests cover management-channel responses, both trust stores, conflicting and matching pins, and the SSH options for the subsequent upload. Live VM and LXC provisioning remain unverified. [Guest enrollment and rotation](operations/ssh.md#deliberate-rotation-or-address-reuse) |
| Debian cloud images | Routine refreshes retain concrete snapshots and SHA-512. Provisioning checks both new downloads and cached images before import. An operator supplying a pre-uploaded storage reference verifies its provenance separately. | A dry run against the Debian archive resolved all three aliases and their checksums. Tests cover missing digests, rejected downloads and cached-image verification. [Proxmox image guide](reference/machine-types.md#provisioning-a-proxmox-vm) |
| Ordinary static build outputs | The next on-target build copies the completed release into private staging and validates the public directories, files and ancestors before stopping services. Allow disk space for the workspace and snapshot. Regenerate the Nginx configuration to enforce the symlink restriction on existing routes. | A temporary Nginx instance served the expected snapshot bytes at both root and nested routes after a retained build handle changed the original file. Regression tests also check rejected output and restoration of the previous release. [Deployment guide](services/deployments.md#static-site) |
| Debian desktop home files | Rerun workstation setup to apply browser and XFCE preferences as the selected desktop user. Setup no longer recursively changes ownership of the user's configuration trees. | Tests check the execution identity, links and preservation of unrelated files. [Desktop guide](workstations/desktop-control.md) |
| Windows Sunshine and CachyOS SSH elevation | Sunshine repair invokes the Windows service controller for fixed operations. CachyOS SSH setup needs a separately verified administrator-owned helper installation before changing managed SSH. | Linux fixtures check ownership, import isolation and the generated elevation commands. [CachyOS provisioning](workstations/cachyos.md) · [Sunshine repair](workstations/windows-sunshine.md) |
| HomeBox and Gogs nested mounts | Move any independent child mount out of the service tree before the next setup, backup or restore. A HomeBox recovery blocked by a mount retains its maintenance marker and recovery archive. | In a disposable Linux mount namespace, preflight and descriptor-based traversal each rejected a real same-device bind mount and a separate tmpfs. The fixture bytes were preserved. [HomeBox](services/homebox.md) · [Gogs](services/gogs.md) |
| Gogs initial password in process arguments | Fresh setup submits the intended administrator login to the native installer over a private Unix socket. An instance with an existing user population requires provisioning a different administrator through Gogs. | The released Gogs v0.14.4 binary created one active administrator through the private socket in an isolated Linux namespace. The login receipt had mode 0600. [Gogs first login](services/gogs.md#first-login-and-git-access) |
| Workspace credential collisions | Bind legacy credentials to an explicit service and destination before replaying saved setups. Git credentials select an HTTPS origin, SMB selects a server and share, and Gogs selects the setup target. | Fixture tests cover separate passwords for the same username, ambiguous input, legacy migration and private target staging. [Migration and examples](agents/credentials.md#migrate-unbound-credentials) |
| Windows build inputs | Native artifact jobs now export the requested Git revision into a private directory and record the source archive digest. Revisions with submodules or symlinks are refused. External dependencies still need their own provenance. | A Git fixture verifies that modified tracked files and untracked files in the working checkout stay out of the export. [Native job guide](workstations/windows-wsl.md) |
| Gateway aliases for T3 and pairing | Rerun saved T3 setup to register the protected upstream ports and reconcile existing forwards. All managed aliases inherit the applicable source limits. Retired ports keep their restrictions until an administrator explicitly releases them. | Regression tests cover IPv4 and IPv6 aliases, chains, narrowing and reconciliation of existing firewall rules. Unmanaged local relays and already-open connections remain outside this control. [Gateway guide](services/internal-web.md#protected-upstream-ports) |
| Panel and pairing proxy credentials | Update the target runtime and rerun the saved panel or pairing setup to regenerate Nginx configuration. Review password rotation if an application backend or its logs could have exposed an earlier login. | A temporary Nginx instance using both generated configurations challenged unauthenticated requests, stripped Basic credentials on both routes, passed the signed-in panel username and forwarded only bearer credentials to notification ingest. [Panel](../common/web_panel_steps.py) · [Pairing](../common/t3code_steps.py) |
| Scrub recovery from the panel | Update the broker and panel together. Verification starts on confirmation. Actions that change files or parity require a decision for the exact request. Open the pending request under **Activity & approvals**. | Broker tests cover requester-only and stale decisions, replay, batch review and invalidation of work automatically approved under the old policy. [Operator guide](services/shares-data.md) · [Broker tests](../tests/test_web_panel_data_actions.py) |
| Antistatic server and database binaries | The next setup verifies the GitHub asset digest for the architecture, records it with the tag, and checks the installed bytes on reuse. Investigate a digest conflict before attempting recovery. | Fixture tests cover both services, missing metadata, failed downloads, altered bytes and same-tag drift. Public metadata supplied SHA-256 values for both server architectures. A temporary setup stopped before download because the returned server tag was below the existing minimum. The database release endpoint returned HTTP 404 to an anonymous request. [Service guide](integrations/antistatic/services.md) · [Release tests](../tests/test_release_management.py) |
| Release workflow permissions | Future release tags build and smoke-test a wheel with read-only access. The publishing job receives the artifact ID and SHA-256, checks the wheel and has the repository write permission used to create the release. | Local workflow checks accepted one matching wheel and rejected changed or duplicate artifacts. The tag-triggered publishing job awaits an actual release. [Workflow](../.github/workflows/release.yml) |

## High-priority findings

### Proxmox can replace a verified guest SSH key on a rerun

On fresh VM or container provisioning and matching saved-guest setup, the
controller authenticates the Proxmox node. The node then scans the guest IP
for its Ed25519 key. A successful scan replaces the address entry in both
the workspace and the invoking user's SSH trust file. If the address answers
for a different guest, subsequent strict SSH can send the setup payload and
its credentials to that endpoint. An isolated provisioning bridge reduces this
risk. [Guest enrollment](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/proxmox_guest.py#L1178) ·
[Trust-store replacement](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/ssh_enrollment.py#L132) · [Operator guidance](operations/ssh.md)

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
[Panel proxy](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/web_panel_steps.py#L477) ·
[Pairing proxy](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/t3code_steps.py#L1794) ·
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
contents as trusted input to these setup steps. [Profile selection](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/plugins/desktop.py#L80) ·
[Browser defaults](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/desktop/browser_steps.py#L439) · [XFCE setup](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/desktop/desktop_environment_steps.py#L73)

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
[Windows service repair](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/windows/Sunshine.Common.ps1#L41) ·
[CachyOS invocation](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/cachyos_ssh.py#L60) ·
[Privileged imports](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/service_tools/cachyos_ssh.py#L15)

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
outside these data trees. [HomeBox backup](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/web/homebox_steps.py#L627) ·
[Restore](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/web/homebox_steps.py#L703) · [Gogs setup](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/web/gogs_steps.py#L401)

**Fix and verify.** Detect nested mounts before backup or mutation and check
mount identity during traversal. Exercise a separate filesystem and a
same-device bind mount. In both cases verify that backups exclude independent
data and that setup, restore and failed-update recovery preserve its bytes
and ownership. The Samba walker already checks mount IDs on open descriptors.
[Samba traversal](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/smb/samba_steps.py#L294)

### Ordinary static deployments trust the build output tree

Source staging rejects symlinks before the build account runs. For an on-target
`type: "static"` build, activation then checks that the output directory
exists. Its ordinary generated Nginx location permits link traversal under
default settings. A link left by a build can therefore publish a file outside
the selected output that the Nginx worker can read. Godot exports and
selected-builder bundles have separate validation paths. [Post-build check](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/deployment.py#L1451) ·
[Nginx location](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/nginx_config.py#L405) · [Deployment guide](services/deployments.md#static-site)

**Fix and verify.** Validate and freeze the final public tree before replacing
the active release. Check the output root, its ancestors and its members.
Enforce containment in the generated location as well. Verify ordinary files
still serve at root and nested routes, and rejected output preserves the
previous release.

## Access policy and credential handling

| Finding and exposure | Action and acceptance |
| --- | --- |
| **Gateway restrictions follow the route name.** An authorized publisher or existing forward can map a second listener to the same local T3 or pairing backend with a broader source policy. Application authentication still applies. [Gateway policy](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/service_tools/basaltwater_web.py#L424) · [T3 registration](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/t3code_steps.py#L1963) | Review all mappings to administrative services. Bind their minimum source restriction to the protected upstream. Verify two mappings cannot widen access after creation, narrowing or reconciliation, for IPv4 and IPv6. |
| **Scrub confirmation depends on trusting the requester account.** Shares & Data confirms the action in the panel. The broker automatically authorizes `data.run`, including deletion and acceptance of changed content. The worker still checks eligible findings and file identities. Any process with the configured requester identity and socket access shares that bounded authority. [Broker policy](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/privilege_policy.py#L155) · [Worker checks](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/common/service_tools/data_job.py#L210) · [Documented trust model](agents/privilege-approvals.md) | Where an independent human decision is required, withhold this authority from unattended accounts. Require decision-side approval bound to the exact destructive action and finding. Verify requester-only, stale and repeated decisions cannot authorize it. |
| **Workspace credential names span services and hosts.** A Git credential is installed for a chosen origin, but the controller looks up its value by username alone. A matching name can also resolve an SMB mount password or initial Gogs admin password. A collision can deliver a valid secret to an unintended target. [Resolution](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/credentials.py#L236) · [Operator guidance](agents/credentials.md#workspace-credential-store) | Use distinct credential names or separate workspaces today. Scope stored identities to the intended service and destination. Verify independent secrets for identical usernames and require a decision for ambiguous legacy entries. |
| **Initial Gogs passwords enter target process arguments.** The displayed command is redacted and the saved credentials file is private. Account creation still supplies the password in child arguments, where local process inspection or execution auditing can capture it. Both generated and workspace passwords take this path. [Account creation](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/web/gogs_steps.py#L1072) | Use a unique bootstrap password and rotate it after setup. Adopt an input channel supported by the deployed Gogs version that keeps the value out of process arguments. Verify both password sources through account creation, including failure diagnostics. |

## Build, update and release integrity

| Finding and exposure | Action and acceptance |
| --- | --- |
| **A routine cloud-image catalog refresh removes verification.** The reviewed catalog pins each Debian snapshot and SHA-512. The updater's default changes entries to `latest` and clears their digests. Provisioning then accepts new or cached catalog images with a warning. The existing default-alias test detects a bad catalog if run before publication. [Updater](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/scripts/update_cloud_images.py#L143) · [Provisioning](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/proxmox_vm.py#L1690) · [Catalog test](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/tests/test_cloud_images.py#L19) | Follow the [pinned refresh procedure](reference/machine-types.md#provisioning-a-proxmox-vm). Make an ordinary refresh retain verified pins, validate every alias, and reject missing digests before downloading or reusing an image. |
| **Antistatic installs identify binary releases by tag.** Server and database setup select a stable GitHub asset and download over HTTPS. Installation and later reuse trust the tag and presence of the local executable, without an expected content digest. Unexpected bytes can become the service executable. Both services run as dedicated users. [Release selection](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/game/antistatic_steps.py#L201) · [Installer](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/lib/release_management.py#L304) | Bind each architecture and tag to an expected digest from trusted metadata and verify staged bytes before publication. Detect same-tag drift on reuse. Test mismatch and missing metadata with the previous binary and release state intact. |
| **Release builds have repository write permission.** Tag tests run read-only. A later job builds and smoke-tests the wheel with `contents: write` and the checkout action's default persisted credential. A compromised build dependency in that job would share authority intended for publishing. [Release workflow](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/.github/workflows/release.yml#L46) · [Checkout credential behavior](https://github.com/actions/checkout#usage) | Build and test the wheel in a read-only job with credential persistence disabled. Give write permission only to a publishing job that takes the identified, verified artifact. Verify the published digest matches the tested wheel and review both jobs' effective permissions. |
| **Windows artifact receipts identify only part of the input.** The native job verifies HEAD and the entry script, then runs from the live checkout. Changes to another build input can reach an artifact whose receipt still names the clean commit. The upload digest confirms the bytes received. [Native job](https://github.com/bluehexagons/basaltwater/blob/2c78b19b/windows/Invoke-BasaltwaterJob.ps1#L56) | Use a fresh private checkout for trusted jobs today. Execute from a snapshot of the declared revision and record any additional inputs. Verify that modifying a second source file cannot change a revision-attributed artifact. |

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
