# Agentic VMs

This guide helps you choose and configure an infra-tools VM for coding agents.
It covers both headless and graphical machines, from a narrow disposable
environment to a fully capable coding desktop. Start with a fresh VM when an
agent will inspect unfamiliar repositories or execute unfamiliar build scripts.

The setup command runs from a trusted controller. The target account is the
coding identity that runs Codex, OpenCode, T3 Code, and project tools. Root SSH
remains the provisioning and recovery path; it is separate from the coding
identity's privileges.

## Choose a configuration

| Configuration | Profile | Suitable for | Privilege model |
| --- | --- | --- | --- |
| Headless coding VM | `agent_vm` | SSH, terminal agents, CI-like project work | Password-required sudo by default, or browser-approved operations |
| Full coding VM | `agent_code_vm` | T3 Code, RDP, Geany, Git write access, and active agent authentication | Browser-approved operations recommended; it keeps the agent capable without reusable root access |
| Unrestricted maintenance VM | `agent_code_vm` or `agent_vm` with `--nopasswd` | A VM where a trusted operator intentionally wants agents to administer the whole guest | Unrestricted passwordless sudo; use only for trusted work and disposable guests |
| Hardened evaluation VM | `agent_vm` with `--harden-user` | Unfamiliar code, unattended work, and disposable evaluation | No escalation path; many agent, browser, network, and credential capabilities are disabled |

`agent_workstation` is the graphical alternative when you need a desktop and
Firefox but not the T3 Code/RDP/pairing defaults of `agent_code_vm`. See
[Agentic coding security](AGENT_SECURITY.md) for the exact security boundaries
of each privilege posture.

## A headless coding VM with browser-approved privileged actions

This is a good default for a terminal-only agent. It gives the coding account
the normal agent toolchain and read-only Git access while removing its direct
sudo authority. The user can still approve a supported maintenance action from
their own device.

```bash
infra-tools setup agent_vm 192.168.1.50 agent \
  --agent-config active \
  --git-access read \
  --repo https://github.com/example/project.git \
  --access-source 192.168.1.0/24 \
  --privilege-broker https://192.168.1.50:9444 \
  --privilege-broker-password
```

The final flag prompts for an independent approval-page password. Do not use
the Linux, Git, T3, or web-panel password again. After setup, the agent makes a
request such as:

```bash
infra-tools agent privilege request system.reboot \
  --reason "Apply the installed kernel update" --json
```

It receives a review URL. Open that URL on your own trusted device, sign in,
review the exact operation and effect, then approve or deny it. The agent never
gets a sudo session or the approval password. See [Privilege approvals](PRIVILEGE_APPROVALS.md)
for the supported actions, service allowlists, recovery path, and policy rules.

## A fully capable coding VM with a linked approval panel

`agent_code_vm` is the complete graphical profile: it includes T3 Code, Geany,
RDP, protected T3 pairing, read-write Git, active authentication sources, and
the GitHub CLI and Codex defaults. Add OpenCode and Playwright when the project
needs those tools. This example provides the web panel as a convenient launch
point for the separate approval page.

```bash
infra-tools setup agent_code_vm 192.168.1.60 agent \
  --provision-on pve1 --name full-agent-1 \
  --memory 8G --cores 4 --storage root local-lvm 64G \
  --agent-tool opencode \
  --browser-automation playwright \
  --lan-access \
  --web-panel 9443 --ssl \
  --web-panel-password 'replace-with-a-separate-panel-password' \
  --privilege-broker https://192.168.1.60:9444 \
  --privilege-broker-password
```

The approval password flag prompts without displaying the secret. Replace the
web-panel placeholder with a different password through your approved secret
input method; do not reuse it for the approval page. The web panel at its
managed HTTPS URL lists **Privilege approvals**. That link opens a different
HTTPS origin with its own password and service identity: signing into the web
panel never grants approval authority.

This is a fully capable development machine, not an unrestricted root machine.
Codex/OpenCode can use the normal standard capabilities, T3 Code and browser
automation are present, and the user can approve the narrow operations offered
by the broker. That boundary is usually preferable to `NOPASSWD:ALL` because a
prompt injection or compromised repository cannot retain root access.

For a VM accessed through a VPN, VLAN, or another routed management network,
replace `--lan-access` with the precise `--access-source` range. Review
[Client CA trust](CLIENT_CA_TRUST.md) and enroll the managed VM CA on the
device used for the panel and approval page; do not bypass certificate warnings.

## An intentionally unrestricted maintenance VM

Use this only when the operator has deliberately decided that the agent should
be able to administer the whole guest without a browser approval. It is useful
for a disposable maintenance VM or a tightly controlled project, but any code
running as the agent account can use root through sudo.

```bash
infra-tools setup agent_code_vm 192.168.1.70 maintainer \
  --provision-on pve1 --name maintenance-agent-1 \
  --memory 8G --cores 4 --storage root local-lvm 64G \
  --agent-tool opencode \
  --browser-automation playwright \
  --lan-access \
  --nopasswd
```

Do not combine `--nopasswd` with `--privilege-broker`, `--harden-agent`, or
`--harden-user`. Keep it off machines that hold personal credentials, trusted
network access, or data you cannot readily replace. A VM snapshot before work
and a short-lived project-specific credential set make this mode more practical
to recover from.

## A hardened disposable evaluation VM

Use a fresh VM for untrusted dependencies or repository analysis that does not
need interactive browser, plugin, credential, or elevation features:

```bash
infra-tools setup agent_vm 192.168.1.80 evaluator \
  --provision-on pve1 --name evaluation-agent-1 \
  --harden-user \
  --git-access read \
  --no-browser-automation \
  --no-default-web-ports
```

Hardened mode intentionally has no browser approval fallback. If work requires
packages, network access, a browser, credentials, or root actions, create a
separate VM with the appropriate explicit profile rather than weakening this
one in place.

## Day-two operation

After setup, verify the agent environment from the controller:

```bash
infra-tools agent doctor 192.168.1.60 agent --all-capabilities --json
infra-tools agent maintenance status 192.168.1.60 agent
```

Use `infra-tools info` and `infra-tools cmd` before changing a saved VM. Add
features with `patch`, first using `--dry-run`. To add the approval panel to an
existing compatible agent VM, run:

```bash
infra-tools patch 192.168.1.60 agent \
  --privilege-broker https://192.168.1.60:9444 \
  --privilege-broker-password
```

Changing from `--nopasswd` or a hardened posture requires explicitly removing
that posture as described in [Privilege approvals](PRIVILEGE_APPROVALS.md).
Terminate old coding-user sessions after enabling the broker so they cannot
retain removed group memberships.

## Related guides

- [Agent systems](agents/README.md)
- [Agentic coding security](AGENT_SECURITY.md)
- [Privilege approvals](PRIVILEGE_APPROVALS.md)
- [Privilege broker reference](PRIVILEGE_BROKER_REFERENCE.md)
- [Minimal web panel](WEB_PANEL.md)
- [Agent authentication](AGENT_AUTHENTICATION.md)
- [Agent browser automation](BROWSER_AUTOMATION.md)
- [T3 Code server](T3_CODE.md)
