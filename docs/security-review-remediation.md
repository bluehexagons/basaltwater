# Security review remediation record

[Topic index](security-review.md)

## Remediation record

For an existing Debian target, update the controller first, then run
`basaltw deploy server.example` with the name of the saved setup when ready
to apply it. This reruns the selected setup on that target. The rows below
identify migrations and checks specific to each finding. [Saved setup operations](operations/operations.md#patch-and-redeploy)

| Finding | Change on main and operator action | Verification |
| --- | --- | --- |
| Proxmox guest SSH enrollment | Hosted setup reads the guest's Ed25519 key through its authenticated node and VMID or CTID. A conflicting pin or unavailable guest identity stops setup. VMs need a working QEMU guest agent. Saved patch, deploy and share uploads require the existing workspace pin. | Fixture tests cover management-channel responses, both trust stores, conflicting and matching pins, and SSH options on the shared upload path. Live VM and LXC provisioning remain unverified. [Guest enrollment and rotation](operations/ssh.md#proxmox-guest-host-keys) |
| Debian cloud images | Routine refreshes retain concrete snapshots and SHA-512. Provisioning checks both new downloads and cached images before import. An operator supplying a pre-uploaded storage reference verifies its provenance separately. | A dry run against the Debian archive resolved all three aliases and their checksums. Tests cover missing digests, rejected downloads and cached-image verification. [Proxmox image guide](reference/machine-types.md#provisioning-a-proxmox-vm) |
| Ordinary static build outputs | The next on-target build copies the completed release into private staging and validates the public directories, files and ancestors before stopping services. Allow disk space for the workspace and snapshot. Regenerate the Nginx configuration to enforce the symlink restriction on existing routes. | A temporary Nginx instance served the expected snapshot bytes at both root and nested routes after a retained build handle changed the original file. Regression tests also check rejected output and restoration of the previous release. [Deployment guide](services/deployments.md#static-site) |
| Debian desktop home files | Rerun workstation setup to apply browser and XFCE preferences as the selected desktop user. Setup no longer recursively changes ownership of the user's configuration trees. | Tests check the execution identity, links and preservation of unrelated files. [Desktop guide](workstations/desktop-control.md) |
| Windows Sunshine and CachyOS SSH elevation | Sunshine repair invokes the Windows service controller for fixed operations. CachyOS SSH setup needs a separately verified administrator-owned helper installation before changing managed SSH. | Linux fixtures check ownership and import isolation. Native PowerShell tests check the fixed Sunshine service commands with service and elevation calls mocked. Live UAC and CachyOS SSH changes still need host-specific verification. [CachyOS provisioning](workstations/cachyos.md) · [Sunshine repair](workstations/windows-sunshine.md) |
| HomeBox and Gogs nested mounts | Move any independent child mount out of the service tree before the next setup, backup or restore. A HomeBox recovery blocked by a mount retains its maintenance marker and recovery archive. | In a disposable Linux mount namespace, preflight and descriptor-based traversal each rejected a real same-device bind mount and a separate tmpfs. The fixture bytes were preserved. [HomeBox](services/homebox.md) · [Gogs](services/gogs.md) |
| Gogs initial password in process arguments | Fresh setup submits the intended administrator login to the native installer over a private Unix socket. An instance with an existing user population requires provisioning a different administrator through Gogs. | The released Gogs v0.14.4 binary created one active administrator through the private socket in an isolated Linux namespace. The login receipt had mode 0600. [Gogs first login](services/gogs.md#first-login-and-git-access) |
| Workspace credential collisions | Bind legacy credentials to an explicit service and destination before replaying saved setups. Git credentials select an HTTPS origin, SMB selects a server and share, and Gogs selects the setup target. | Fixture tests cover separate passwords for the same username, ambiguous input, legacy migration and private target staging. [Migration and examples](agents/credentials.md#migrate-unbound-credentials) |
| Windows build inputs | Native artifact jobs export the requested Git revision into a private directory and record the source archive digest. A locally edited entry script stops the job. Revisions with submodules or symlinks are refused. External dependencies still need their own provenance. | A hosted Windows Server runner executed the committed script and produced the expected artifact with locally modified and untracked project inputs present. It also rejected the edited entry script, a redirected artifact path and a mismatched upload digest. [Native job guide](workstations/windows-wsl.md#run-a-native-powershell-job) |
| Gateway aliases for T3 and pairing | Rerun saved T3 setup to register the protected upstream ports and reconcile existing forwards. All managed aliases inherit the applicable source limits. Retired ports keep their restrictions until an administrator explicitly releases them. | Regression tests cover IPv4 and IPv6 aliases, chains, narrowing and reconciliation of existing firewall rules. Unmanaged local relays and already-open connections remain outside this control. [Gateway guide](services/internal-web.md#protected-upstream-ports) |
| Panel and pairing proxy credentials | Update the target runtime and rerun the saved panel or pairing setup to regenerate Nginx configuration. Review password rotation if an application backend or its logs could have exposed an earlier login. | A temporary Nginx instance using both generated configurations challenged unauthenticated requests, stripped Basic credentials on both routes, passed the signed-in panel username and forwarded only bearer credentials to notification ingest. [Panel](../common/web_panel_steps.py) · [Pairing](../common/t3code_steps.py) |
| Scrub recovery from the panel | Update the broker and panel together. Verification starts on confirmation. Actions that change files or parity require a decision for the exact request. Open the pending request under **Activity & approvals**. | Broker tests cover requester-only and stale decisions, replay, batch review and invalidation of work automatically approved under the old policy. [Operator guide](services/shares-data.md) · [Broker tests](../tests/test_web_panel_data_actions.py) |
| Antistatic server and database binaries | The next setup verifies the GitHub asset digest for the architecture, records it with the tag, and checks the installed bytes on reuse. Investigate a digest conflict before attempting recovery. | Fixture tests cover both services, missing metadata, failed downloads, altered bytes and same-tag drift. Public metadata supplied SHA-256 values for both server architectures. A temporary setup stopped before download because the returned server tag was below the existing minimum. The database release endpoint returned HTTP 404 to an anonymous request. [Service guide](integrations/antistatic/services.md) · [Release tests](../tests/test_release_management.py) |
| Release workflow permissions | Future release tags build and smoke-test a wheel with read-only access. The publishing job receives the artifact ID and SHA-256, checks the wheel and has the repository write permission used to create the release. | Local workflow checks accepted one matching wheel and rejected changed or duplicate artifacts. The tag-triggered publishing job awaits an actual release. [Workflow](../.github/workflows/release.yml) |

The integrated Linux `make check-ci` run completed 10,436 tests with 25
platform or environment skips. Coverage with branch measurement was 82%.
The same run checked the built wheel, package metadata, generated assets,
CLI documentation and Markdown links. Hosted Debian validation, Node
compatibility and Ubuntu packaging also passed. [Linux CI](https://github.com/bluehexagons/basaltwater/actions/runs/44356518039)

Windows qualification uncovered an additional ownership issue. Windows could
assign an administrator's default group as the owner of a new desktop state
file, causing the next operation to refuse it. The desktop helper now creates
control state, keyboard-lease records and worker request markers with the
current account's SID and private permissions. Existing files still undergo
ownership checks. The hosted Windows Server suite passed, including artifact
jobs, desktop state, worker cleanup and mocked Sunshine service operations.
[Windows qualification](https://github.com/bluehexagons/basaltwater/actions/runs/44356517868) · [Native desktop guide](workstations/windows-desktop.md#observe-a-native-application)

### Follow-up review

- Git HTTPS credential setup and removal apply all home-directory changes
  as the selected account. The setup process passes credentials to that
  account over stdin and refuses mounts at or below the managed directory
  before changing Git settings. An isolated Linux check used the installed
  Git to configure and remove a synthetic credential under a separate UID.
  Removal refused a same-device bind mount and preserved the mounted data
  and Git settings. Rerun saved setup to use the updated path.
  [Git access guide](agents/git-access.md#self-hosted-https-and-git-lfs)
- The workspace-pinned SSH path for managed Proxmox guests disables agent
  forwarding, X11, port forwarding, tunneling and local commands inherited
  from client configuration. The controller can still use a local SSH agent
  to authenticate. OpenSSH's effective configuration confirmed these
  overrides with a test client configuration. [Guest SSH guide](operations/ssh.md#proxmox-guest-host-keys)
