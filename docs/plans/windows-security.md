# Windows Security Plan

Status: proposed; no security commands or hardening selections described here
are implemented by this plan. This work is independent of the proposed
[Windows build hosts](windows-support.md) and does not expand the current
stable-release gate.

## Objective and scope

Improve the security of the existing Windows 11 coding workstation with a small
native layer: inspect important protections, explain gaps, and offer a few
explicit, reversible changes. Prefer Windows facilities and documented manual
procedures over new Basaltwater infrastructure.

Start with the current supported Windows 11 x64 workstation and its existing
account. Detect edition, build, privileges and feature availability instead of
assuming every security feature is installed or configurable. WSL security is
a separate environment; a hardened WSL instance does not harden its Windows
host. Windows Server and managed build-worker isolation belong to other work.

## Current boundary and threat model

The [native workstation setup](../workstations/windows.md) installs tools,
skills and account integration. It preserves provider logins, uses limited
interactive tasks for desktop startup, and requests UAC for selected machine
operations. It does not install the Linux controller or privilege broker.
The [Sunshine option](../workstations/windows-sunshine.md) uses a vendor-owned
service and firewall rules. The artifact-token helper already uses current-user
DPAPI encryption; extend existing mechanisms rather than adding a secret store.

This plan addresses accidentally weakened Windows protections, unnecessary
network exposure, excessive permissions and exposure of credentials to unrelated
users. Antivirus, disk encryption and file ACLs do not isolate arbitrary code
from the files and credentials available to its own account. Native process
supervision is also not a security sandbox. Provider sandbox and approval
settings remain provider-owned; setup must not weaken them.

## Keep the implementation small

- Reuse the PowerShell CLI, health reporting, setup lock, saved selections and
  existing UAC/reboot flows. Add at most one focused security helper initially;
  keep collectors and their tests together rather than creating a plugin engine.
- Use built-in Windows commands/APIs. Require no new Python runtime, agent
  service, scheduled scanner, database, web panel or third-party security suite.
- Run checks on demand. Do not continuously reconcile policies or repeatedly
  request elevation during ordinary tool refreshes.
- Use Microsoft's version-appropriate security baseline as a reference for
  selected controls. Do not import the entire baseline or maintain a copied
  registry catalog, compliance score, or historical Windows support matrix.
- Ship useful reporting even if mutation remains manual. Every automated fix
  needs a concrete benefit beyond presenting the documented Windows procedure.

## Improvement candidates

These are delivery priorities, not promises to automate every setting.

| Area | Useful improvement | Initial treatment and boundary |
| --- | --- | --- |
| Account privilege | Report whether the current process is elevated and whether the account belongs to Administrators; keep coding tasks unelevated. | Report and document use of a standard account with separate administrator credentials. Do not remove group membership or create accounts during setup. |
| Windows Firewall | Check enabled state and effective inbound policy for every profile; identify relevant exposure from selected services. | First automation candidate: enable disabled profiles after preview. Preserve existing rules; enabling the firewall alone does not make permissive allow rules safe. |
| Defender | Report active antivirus provider where available, Defender mode, real-time protection, signature status, potentially unwanted application protection and relevant exclusions. | First automation candidate: enable unwanted-application blocking when Defender is active and locally configurable. Report stale signatures with the observation time; leave security-intelligence updates to Windows. Never remove third-party antivirus or bypass tamper protection. |
| Windows updates | Report OS build, available support/lifecycle information and whether update configuration is explicitly disabled or managed. | Document remediation through Windows Update. Unknown patch currency stays unknown; a recent reboot or successful tool refresh does not establish that Windows is patched. No custom update agent or automatic feature upgrades. |
| Managed files and credentials | Check ownership, unexpected write access and reparse points at specific Basaltwater-owned executable/state/credential paths and scheduled-task definitions. | Reuse existing path/ACL guards. Repair only narrowly owned paths with known intended permissions; preserve unrelated files and legitimate Windows principals. Do not recursively reset a home directory or inspect credential contents. |
| Network services | Report relevant exposure for selected Sunshine, RDP, OpenSSH or WinRM components when present. | Keep administration private and offer service-specific manual guidance. No blanket service disabling, port forwarding, network-profile changes or listener scanning across the network. |
| SMB | Detect installed/enabled SMBv1, guest access policy and effective signing requirements. | Initially report and guide. Removing SMBv1 or requiring signing needs an explicit compatibility decision for existing shares; defaults differ by edition/build. Never weaken signing to make a share connect. |
| Hardware and credential protections | Report Secure Boot, memory integrity/VBS, vulnerable-driver protection, LSA protection and Credential Guard where supported. | Initially report only. Hardware/driver, edition and virtualization prerequisites matter; unavailable or unreadable is not equivalent to disabled. Firmware changes remain manual. |
| Disk encryption | Report BitLocker/device-encryption protection status on relevant development volumes without retrieving recovery secrets. | Manual enablement initially. Confirm a recoverable key backup before enabling encryption; encryption protects data at rest, not files accessible to a running agent. |
| SmartScreen and cloud protection | Report supported reputation/cloud protection settings and explain their benefit. | Preserve existing choices and organizational policy. Any later enablement must explain cloud/sample submission behavior; do not silently upload project samples or change submission consent. |
| Attack surface reduction (ASR) | Evaluate selected rules against credential theft and risky application behavior. | Later, explicit audit-only trial before enforcement. Qualify package managers, scripts, compilers and generated binaries; avoid broad development-directory exclusions. |
| Application and ransomware controls | Consider App Control for Business and controlled folder access for a demonstrated restricted workload. | Defer general automation. Application allowlists and protected-folder exceptions can create an ongoing support burden for changing development tools. |

Do not treat stricter PowerShell execution policy as an agent containment
boundary. Existing process-scoped execution-policy bypass is documented in the
installer; this plan does not change the account's saved policy or use bypass
to defeat App Control or organizational policy.

## Increment 1: read-only security report

Extend the existing native doctor surface with an explicit security selector;
choose the final spelling when implementing it. This selector and its JSON
fields are proposals, not commands available today. Keep ordinary tool-health
checks and their exit behavior unchanged.

Start with account/elevation, firewall and Defender observations. Add other
collectors only when they can provide a useful answer through a bounded native
query. Check only known managed paths and relevant service configuration;
do not crawl the filesystem, export all firewall rules or run arbitrary project
executables for discovery.

For each check, return a stable identifier, observed state, applicability,
short explanation and manual next action. Distinguish an observed gap from
unsupported, not applicable, permission denied, managed externally and unknown.
Do not claim a setting is organization-managed unless its policy source can
actually be established. Report configured and effective/running state
separately when Windows exposes both, including pending-restart state.

Queries must have time and output bounds, continue after an individual failure,
and never install tools, request UAC, fetch updates, restart services or write
machine settings. Make incomplete collection visible in text and JSON. Define
exit behavior for the explicit selector without turning optional recommendations
into failures of ordinary doctor. A security report is not a compliance claim.

Return only necessary summaries. Do not emit tokens, recovery keys, credential
contents, full policy dumps or inventories of unrelated users. Keep any raw
qualification evidence outside Git under the
[documentation privacy rules](../agents/project-standards.md#documentation-and-private-operational-state).

## Increment 2: two opt-in fixes

After the report is useful, implement only firewall-profile enablement and
Defender unwanted-application blocking. Choose a compact explicit selection
through the current setup/refresh interface; do not create multiple security
profiles, an arbitrary registry editor or a new command hierarchy.

1. Preview the precise supported settings, observed values, proposed values,
   elevation needs and possible impact before application. A dry run writes
   nothing and does not request elevation.
2. Preserve organization-managed settings. Unsupported features, third-party
   antivirus, tamper protection and uncertain policy authority produce an
   explanation instead of a forced override. Do not change these controls merely
   because the user reruns normal workstation installation.
3. Elevate only the fixed machine operation through the existing UAC pattern.
   Validate inputs before elevation; elevated code must use a trusted Windows
   executable/helper path, not a project or user-writable command resolved from
   PATH. Do not pass arbitrary PowerShell supplied through state files.
4. Save the original value and its presence/type before mutation in a small,
   ACL-protected local record for these settings. Reuse setup ownership and
   locking. Persist intent before changing a value so an interruption is
   inspectable; retain partial results and stop after failure.
5. Read back effective state. Report policy overrides, refusal and uncertainty
   honestly; writing a value is not evidence that protection is active.
6. Provide an explicit restore operation for changed settings only. Restore
   only when the current value still matches the recorded applied value and no
   intervening policy ownership change is detected. Otherwise leave it untouched
   and explain manual recovery. Never restore a whole registry hive or firewall
   export over later administrator changes.

Ordinary refresh can retain the chosen selection and report drift, but must not
silently reapply machine policy. Reapplication and restoration are deliberate
actions. The two initial fixes should not require reboot; future controls that
do must use the existing restart handling and report pending activation.

If safe application/restoration requires a general policy-management framework,
keep that control manual and ship the report instead.

## Later work and explicit deferrals

Evaluate one control at a time after the first two increments. SMB hardening,
LSA protection, memory integrity and a small ASR audit trial are candidates
only when there is a real affected workload and native compatibility evidence.
Promoting audit to block requires an explicit selection after reviewing events;
the absence of observed audit events does not prove compatibility.

Document a manually prepared dedicated standard account for unattended agents
before considering automated account provisioning. Such an account must not
inherit the operator's unrelated credentials. Dedicated managed build identities
remain owned by the [build-host plan](windows-support.md).

Defer a Windows privilege broker, custom privileged service, automatic account
migration, UEFI locks, fleet policy enforcement, centralized secret storage,
automatic BitLocker enrollment and application allowlist maintenance. Use Windows
and existing enterprise management tools for those needs. No Linux-hardening
parity requirement should force them into this workstation layer.

## Verification and acceptance

Keep offline behavioral tests in the existing native PowerShell suites. Mock
machine mutations and use temporary directories/state. Cover supported and
unsupported features, standard/admin/elevated contexts, third-party antivirus,
policy overrides, unreadable state, query timeouts, dry-run nonmutation, UAC
cancellation, partial failure, interrupted application, repeat application and
restore conflicts. Retain existing tool-health behavior and JSON contracts.

Qualify actual changes on a disposable Windows 11 VM with the intended account,
recording edition/build and effective before/after states privately. Exercise
native setup and refresh, tool installation, provider use, a representative
compile/test run and selected WSL/Sunshine workflows. Firewall changes need real
allowed/denied connection checks from a second host; mocked rule output is
insufficient. Test lock/reconnect or reboot only for a control that affects it.
Do not harden or reboot a shared workstation to obtain test evidence.

The first delivery is complete when:

- The report is bounded, read-only and useful without administrator rights,
  with incomplete or unavailable observations explicit.
- Each of the two optional fixes has a preview, limited elevation, effective
  readback and tested restoration/interruption behavior, or is explicitly kept
  manual because those requirements could not be met simply.
- Selected native workflows still work, and policy/compatibility limitations
  are documented without claiming broader Windows qualification.
- Operator documentation explains the shipped selectors, ownership boundaries
  and manual alternatives. The plan and coverage map distinguish implemented,
  qualified and deferred work.

Follow [native qualification](../workstations/windows-qualification.md) and the
[contributor guide](../agents/contributing/README.md) for applicable checks.
Run documentation/link and diff checks for this planning document; no Windows
runtime behavior is established by documentation validation.

## Upstream references

Consult the guidance for the actual Windows version before implementation;
these are references, not baseline imports or extra runtime dependencies.

- [Microsoft Security Compliance Toolkit](https://learn.microsoft.com/windows/security/threat-protection/windows-security-configuration-framework/security-compliance-toolkit-10)
- [Windows Firewall](https://learn.microsoft.com/windows/security/operating-system-security/network-security/windows-firewall)
- [Defender protection features](https://learn.microsoft.com/en-us/defender-endpoint/configure-protection-features-microsoft-defender-antivirus)
- [SMB signing behavior](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-signing)
- [Memory integrity](https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity)
- [BitLocker recovery](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/recovery-overview)
- [ASR deployment questions](https://learn.microsoft.com/defender-endpoint/attack-surface-reduction-faq)
- [App Control policy rules](https://learn.microsoft.com/en-us/windows/security/threat-protection/windows-defender-application-control/select-types-of-rules-to-create)
- [PowerShell execution policies](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies/)
