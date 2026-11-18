# Security configuration plan

This plan tracks fixes to Basaltwater's generated configuration and setup
transitions. The implementation covers the findings below. Release qualification
also exercises fresh installs, saved reruns, and recovery on affected profiles.

## Design decisions

- **HTTP panel access.** Keep HTTP available over localhost and in controlled
  test environments. Setup and dry-run summaries warn that login credentials
  and integrated approvals travel without encryption. Use HTTPS over shared
  or untrusted networks. HTTP's effective reachability depends on the listener
  and firewall. Setup also warns for direct HTTP T3 Code, pairing, and Gogs.
  [Web panel operator guide](../services/web-panel.md#install-and-sign-in)
- **Task workers.** Broad access to the assigned account and selected host
  diagnostics is intentional, including the worker's journal access. Treat
  provider execution and the worker's service identity as part of the host's
  agent trust boundary. The operator guide describes that scope with `--harden-user`.
  [Worker unit](../../common/agent_task_steps.py) · [Task worker operator guide](../agents/agent-tasks.md)
- **Shared service identities.** GitHub runners that share a service identity
  belong to the same trust domain because that identity can read each
  registration's credentials. Headscale's default peer policy assumes enrolled
  devices can reach one another, subject to host firewalls. Use separate runner
  identities or hosts, and a restricted tailnet policy, when deployments cross
  those boundaries. [Runner trust model](../services/github-runner.md) · [Headscale plan](headscale.md)

## Verification and release qualification

| Area | Evidence | Remaining deployment checks |
| --- | --- | --- |
| Authentication and privilege | Debian's PAM generator produced a stack that rejected authentication during lockout and cleared eligible failure tallies after success. Fixtures cover SSH `Match` and daemon launch options, SSH key publication, Proxmox activation scope, and auditd live versus boot policy. | Exercise SSH recovery and the audit boot policy on affected hosts. Exercise first-time firewall activation on a single-node Proxmox host and verify existing policy on a multi-node cluster. |
| Network ingress | Isolated Debian UFW verified IPv4 and IPv6 rule chains for scoped SSH, rate-limited RDP, and loopback-only HTTPS. The packaged xRDP daemon used the generated loopback and wildcard bindings and dropped privileges. Debian Nginx accepted the rendered T3 and pairing routes. Fixtures cover saved source replacement and removal, operator rules, gateway recovery, and Cloudflare activation failures. | Check fresh and saved deployments from both address families, including Guacamole desktop login, RDP deselection, private T3 pairing, and a public Cloudflare tunnel. |
| Storage | Isolated Samba permission walks left both a same-device bind mount and a separate filesystem untouched. SMB password rotation preserved the Unix shadow entries for locked and unlocked accounts in disposable Debian. Debian Syncthing accepted an in-root folder and rejected readable paths outside it, including service state. CIFS fixtures verify recovery of the prior endpoint and credential generation. | Check CIFS with servers that offer POSIX metadata and servers that do not. Exercise Syncthing under systemd through the HTTPS gateway. |
| Updates and source provenance | APT verified a signed Debian security fixture and simulated its update. TLS checks covered a trusted private CA and rejection of untrusted or wrong-host certificates. Artifact checks verified the published Helium and Browsh packages. On disposable Debian, system installation and writable-checkout migration preserved runtime ownership and durable state. Playwright launchers picked up the newer checksum-verified Node runtime and reused their verified npm packages. Installer fixtures also cover unexpected commits and interrupted publication. | Exercise privileged setup with service activation, a browser session after the Node upgrade, a newer xRDP package with a working desktop, and scheduled updates and webhook delivery. |

## First fixes

| Finding | Implementation and acceptance |
| --- | --- |
| **Privileged runtime ownership.** Root installation for a non-root user can leave a writable managed checkout at `/opt/basaltwater`. Local setup can preserve that checkout while root maintenance units reference its scripts. | Separate the writable checkout from the root-owned runtime. Verify ownership of executable files, import directories, and their ancestors. Cover a fresh local install, migration of an existing managed checkout, and interrupted publication. [Installer](../../install.sh) · [Local activation](../../lib/setup_common.py) · [APT timer](../../security/security_steps.py) |
| **Debian PAM lockout.** The generated profile can place faillock outside the effective success path and omit its pre-authentication check. | Verify the generated PAM stack, including the jump targets. In a disposable Debian guest, confirm that a correct password is refused during lockout and that successful authentication clears eligible failure tallies. [PAM setup](../../security/security_steps.py) |
| **Samba password synchronization.** Samba provisions existing Unix identities and can propagate an SMB password change to Unix via PAM where policy permits it. | Default Unix password synchronization to off and constrain attachment of privileged accounts. Verify a Samba password rotation preserves the existing Unix password and lock state. [Samba setup](../../smb/samba_steps.py) |
| **xRDP listener and removal.** The generated `address=` setting does not constrain the listener, and `--no-rdp` can skip cleanup when RDP was the only reason to install a desktop. | Use upstream's bind syntax and reconcile the managed listener, firewall rules, and Fail2ban jail independently of desktop installation. Probe IPv4 and IPv6 listeners for direct RDP and Guacamole-only access. Cover an enabled-to-disabled rerun and check the service runs with the package's supported runtime identity. [xRDP template](../../desktop/config/xrdp.ini.template) · [Desktop composition](../../plugins/desktop.py) |
| **SSH-only source policy.** `server_lite` can skip firewall setup when SSH-only sources are the sole trigger. Proxmox and WSL can accept the option without applying it. | Wire selection and clearing into supported profiles and reject unsupported combinations before mutation. Test fresh selection, replacement, clearing, and preservation of the current management connection. [Server profile](../../plugins/server.py) · [Proxmox profile](../../plugins/proxmox.py) · [WSL profile](../../plugins/wsl.py) |

## Network and trust boundaries

| Finding | Implementation and acceptance |
| --- | --- |
| **Effective firewall rules.** SSH, T3, Gogs, and the shared HTTPS gateway can miss existing rules that change access. `ufw status` also omits earlier chains. | Verify ordered coverage per listener and address family, including other source networks, port ranges, app profiles, rate limits, and earlier rules. Detect both unintended allows and rules that block the management peer. Preserve operator rules and report conflicts that setup cannot resolve. [SSH and UFW setup](../../security/security_steps.py) · [T3 setup](../../common/t3code_steps.py) · [Gogs setup](../../web/gogs_steps.py) · [Gateway](../../common/service_tools/basaltwater_web.py) |
| **SSH effective configuration.** A probe for the current peer can miss `Match` clauses for other users, addresses, and ports. Firewall and ban rules assume TCP 22. | Validate the effective listener and socket configuration, derive enforcement ports or reject unsupported configurations, and test policy-affecting `Match` clauses on the rendered daemon configuration. [SSH setup](../../security/security_steps.py) |
| **T3 and pairing gateway sources.** The shared gateway can retain revoked sources or open forwards globally when no sources are configured. | Give each administrative route a replaceable source policy. Verify that narrowing and clearing it affect direct and HTTPS access as intended, including existing shared forwards. [Gateway configuration](../../common/godot_web_steps.py) · [T3 setup](../../common/t3code_steps.py) |
| **Cloudflare origin exposure.** Early setup can open a tunnel-only origin before tunnel reconciliation. Vhost discovery can also include administrative routes. | Defer origin firewall changes until activation and select public hostnames from managed setup or an explicit helper list. Fixtures cover fresh setup, saved reruns, failed activation, and legacy route migration. External IPv4 and IPv6 reachability checks remain pending. [Server ordering](../../plugins/server.py) · [Tunnel setup](../../web/service_tools/setup_cloudflare_tunnel.py) · [Operator guide](../services/cloudflare.md) |
| **Notification webhook verification.** Outbound HTTPS can accept an unverified peer even when it carries a bearer token. | Default to certificate and hostname verification, support trusted private CAs, and make compatibility overrides explicit. Test valid, untrusted, and wrong-host certificates and saved configuration migration. [Webhook sender](../../lib/notifications.py) |
| **Proxmox firewall scope.** Setup can enable the cluster firewall based on checks of the current node. | Verify the local management path and cluster membership before first activation. On a multi-node cluster, require the operator to enable the cluster firewall before setup reconciles management sources. Fixtures cover first enablement and refusal on a multi-node cluster. [Firewall setup](../../security/security_steps.py) · [Proxmox operator guide](../operations/proxmox.md) |

## Storage and host policy

| Finding | Implementation and acceptance |
| --- | --- |
| **Samba nested mounts.** Recursive ownership and permission changes can cross into separately managed data. | Detect nested mounts and enforce the boundary during traversal. Cover mounts on separate devices and same-device bind mounts. [Share setup](../../smb/samba_steps.py) |
| **CIFS local access and rollback.** Default modes can expose mounted data to unrelated local users. Failed reconfiguration can restore an old endpoint with newly written credentials. | Default to owner-only access with explicit local sharing. Verify effective local modes with and without negotiated POSIX metadata. Stage credentials per configuration and test endpoint and credential rollback together. [Mount setup](../../smb/smb_mount_steps.py) |
| **Syncthing storage boundary.** Setup validates folder paths, while GUI changes can add folders the service can read outside the share root. | Enforce containment at runtime, including exclusion of service state, and verify that a GUI or API change cannot share readable out-of-root content. [Syncthing setup](../../sync/syncthing_steps.py) |
| **SSH home-directory publication.** Root-managed file updates below a user-controlled home must keep their path checks valid through publication. | Perform writes as the target identity where possible or use descriptor-relative, no-follow operations. Verify ownership and permissions on existing and newly created paths. [SSH home setup](../../common/common_steps.py) · [Atomic file writes](../../lib/atomic_io.py) |
| **Auditd policy ownership.** Generated `-e 1` can supersede operator immutability at reboot, and an immutable live ruleset can leave a successful load command without changes. | Preserve the operator's enable mode, verify the live rules, and report when activation requires reboot. Test existing immutable rules and merged boot policy. [Auditd setup](../../security/security_steps.py) |

## Update and artifact policy

| Finding | Implementation and acceptance |
| --- | --- |
| **Node patch updates.** CI follows the latest patches on supported majors and the browser runtime uses a verified Node 24 release. NVM's automatic updates followed the newest supported LTS major, so an older supported major could remain installed at an old patch. | Maintain each installed supported LTS line while preserving project-owned exact pins. Rotate the browser runtime on upstream security releases and verify the version and both Linux archive checksums. Saved setup updates existing browser launchers and reuses verified npm packages. [Service runtime](../../lib/service_node.py) · [NVM updater](../../web/service_tools/auto_update_node.py) |
| **xRDP update channel.** A low-priority Sid pin addresses a desktop regression but prevents ordinary APT upgrades from selecting newer Sid xRDP builds. | Qualify a maintained stable or backported package, or establish a verified refresh path. Verify that a newer security fix is selected and the shared desktop still works. [xRDP package setup](../../desktop/xrdp_steps.py) · [Operator guide](../workstations/xrdp.md) |
| **APT security coverage.** An auxiliary index failure or unrelated proposed removal can defer all automatic security updates. | Run an independent signed Debian security update before the guarded full upgrade. Proxmox uses its guarded upgrade with Proxmox-specific preflight. Tests cover an auxiliary outage and an upgrade that would remove packages. [APT updater](../../common/service_tools/auto_update_apt.py) |
| **Installer and browser artifact provenance.** The bootstrap source and selected browser packages can come from moving upstream references. | Define trusted source verification and artifact version or signature requirements before root installation. Verify rejection of unknown or mismatched artifacts and recovery from failed refresh. [Installer](../../install.sh) · [Browser package setup](../../desktop/browser_steps.py) |
