# Privilege approvals

Use this feature when an agentic VM needs an occasional privileged action but
must not hold reusable sudo access. The agent requests an action; you review it
on a separate HTTPS page and approve it once. The coding account never receives
the approval password or a sudo session.

The agent can request an exact command and argument vector for one-time root
execution. The approval page shows every argument; shell strings, environment
assignments, stdin, and command prefixes are not accepted. VM reboot and exact
administrator-registered service restarts remain available as structured actions.

## Set up the approval page

Run this from the trusted controller:

```bash
infra-tools patch 192.168.1.50 agent \
  --privilege-broker \
  --privilege-broker-password
```

The password flag prompts privately. Pick a 16–256 character password that is
different from the Linux, Git, T3, and web-panel passwords. Only a salted hash
is uploaded; saved configuration and reconstructed setup commands omit it.

The approval page uses the managed VM host and HTTPS port 9444. Use
`--privilege-broker PORT` to choose another port above 1023, outside the shared
gateway's 8443–8999 range. Restrict the VM with `--lan-access` or
`--access-source`, and enroll the VM CA on the device that will approve
requests; see [Client CA trust](CLIENT_CA_TRUST.md).

The broker is available on VM `agent_vm`, `agent_code_vm`, and
`agent_workstation` setups. It cannot coexist with `--nopasswd`,
`--harden-agent`, or `--harden-user`. On an existing VM, end old coding-user
sessions after setup so they cannot retain removed group memberships.

## Request and approve an action

The agent runs one of these commands:

```bash
infra-tools agent privilege request system.reboot \
  --reason "Apply the installed kernel update" --json

infra-tools agent privilege request service.restart --unit example.service \
  --reason "Restart the reviewed service" --json

infra-tools agent privilege wait REQUEST_ID --timeout 300 --json

# Request an exact command, without a shell or a leading sudo.
infra-tools agent privilege request command.run \
  --reason "Refresh package metadata" --command /usr/bin/apt-get update
```

The request returns a `review_url`. Open it on your own device, sign in with
the separate approval password, check the machine identity, requester UID,
parameters, effects, and expiry, then choose **Approve once** or **Deny**.
Treat the agent's explanation as untrusted. The root broker performs one
approved attempt automatically.

Requests expire after five minutes by default. A changed policy invalidates a
request; an interrupted execution is marked uncertain and is never retried.
Check status before submitting a replacement request. A reboot reported as
`dispatched` is not proof that the VM returned.

When the optional [web panel](WEB_PANEL.md) is installed, its **Services** area
links to the approval page. The panel has a different password and cannot read
approval credentials or approve requests.

## Rotate or remove it

```bash
# Prompt for and set a new approval password.
infra-tools patch 192.168.1.50 agent --privilege-broker-password

# Stop the services and remove approval authority.
infra-tools patch 192.168.1.50 agent --no-privilege-broker
```

Omitting these flags preserves the feature and current password. Rotation
expires outstanding approvals. Disabling removes the managed services,
credentials, and polkit rule but retains policy and audit history for review.

## Administrator reference

Administrators who register restartable services, inspect audit records, or
need the detailed security contract should use the [privilege broker
reference](PRIVILEGE_BROKER_REFERENCE.md). Keep root SSH as the recovery
channel; the approval page has no password-recovery flow.
