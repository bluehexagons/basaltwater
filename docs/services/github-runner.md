# GitHub Actions runner

Use a dedicated Debian 13/Trixie x86-64 VM for trusted jobs from private
`github.com` repositories. This persistent runner is not a sandbox for hostile
code. Live VM, reboot and GitHub qualification remain open; see the
[operations reference](github-runner-operations.md#qualification).
For native Windows jobs with WSL access, use the
[Windows runner guide](../workstations/windows-github-runner.md).

## Set up

```bash
basaltw setup server_github_runner ci.example.net operator \
  --machine vm --ci-repository OWNER/REPO1 --ci-repository OWNER/REPO2 \
  --ci-allowed-user alice --ci-label debian-ci --ci-register-with-gh
```

Repeat `--ci-repository` for up to 20 private repositories, including personal
repositories. Each has its own registration and workspace. **Only one job runs
workflow steps at a time across the VM**; other assigned jobs wait in the hook.
Waiting counts toward the job timeout, and order across repositories is not
guaranteed. These repositories share the same trusted service account.
For one build that consumes several repositories, see
[multi-repository builds](github-runner-repositories.md).

Repeat `--ci-allowed-user` for each trusted person. For an organization, replace
the repository flags with `--ci-organization ORG` and optional
`--ci-runner-group GROUP`. Choose allowed repositories under **Settings →
Actions → Runner groups → GROUP → Repository access** in GitHub, including
when using the default group.

Choose how to register:

| Option added to setup | Result |
| --- | --- |
| `--ci-register-with-gh` | Request a token using the controller's existing GitHub CLI login and register now |
| `--ci-registration-token-file PATH` | Register a single configured repository or organization using a private token file (`chmod 600`) |
| Neither | Install in **Pending registration** state, with the service disabled |

You can add either registration option on the first setup or repeat the same
complete setup command later. Registered runners need no token on routine
setup. Credential errors leave registration pending and let the rest of setup,
including requested restarts, finish. `--dry-run` makes no token request.
On later setup runs, include the complete desired repository list. Omitted
repositories are disabled locally; re-adding them reuses their registration.

## Get a registration token

**GitHub CLI:** Install `gh` on the computer running setup and sign in with
`gh auth login --hostname github.com`. For an organization, your account needs
runner-management access and the browser login needs the `admin:org` scope:

```bash
gh auth refresh --hostname github.com --scopes admin:org
```

For a repository, you need repository admin access and the `repo` scope
(included in a default `gh` login). Then add `--ci-register-with-gh` to setup;
only the generated registration token goes to the runner. `gh auth status
--hostname github.com` shows the active account and scopes. `GH_TOKEN` or
`GITHUB_TOKEN`, when set, overrides the saved login; refreshing that login does
not change an environment token's permissions. After fixing access, retry the
same setup command; the existing VM can be reused.

To create a token yourself, run the command for your scope:

```bash
# Organization
gh api --hostname github.com --method POST \
  orgs/ORG/actions/runners/registration-token --jq .token

# Single repository
gh api --hostname github.com --method POST \
  repos/OWNER/REPO/actions/runners/registration-token --jq .token
```

**Website:** Open the repository or organization's **Settings → Actions →
Runners**. Choose **New self-hosted runner** (organizations: **New runner →
New self-hosted runner**), then copy only the value after `--token` in the
configuration command. Basaltwater handles installation; you only need the
token. See [GitHub's instructions](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).

Tokens expire after **one hour**. Use one for the exact repository or
organization configured above. Save it in a private file for
`--ci-registration-token-file PATH`, or paste it into the panel. Delete your
local file after setup. A PAT or GitHub App key is not a runner registration
token. API permissions are listed in
[GitHub's runner API reference](https://docs.github.com/en/rest/actions/self-hosted-runners).

## Register in the panel instead

Add `--web-panel 443 --ssl` to setup; see [panel access](web-panel.md) for
password and certificate settings. Sign in, open **GitHub runner**, and press
**Load runner status**. Follow the GitHub settings link, paste the token, and
press **Register runner**. Success enables the service and refreshes status.
For multiple repositories, register the scope shown in the form, then repeat
for the next pending repository. Each needs its own token.

Failures identify whether registration could start, timed out, failed to
configure, or registered but could not start its service. Follow the displayed
retry or setup repair instruction. After an interrupted request, load status
first. **Recovery required** needs the
[recovery procedure](github-runner-operations.md#updates-deregistration-and-recovery).

## Host administration

**Admin controls** is always visible in the Linux panel. To enable package
updates, Basaltwater refresh and power actions on a VM, run from an updated controller:

```bash
basaltw patch ci.example.net operator --privilege-broker --privilege-broker-password
```

Choose a separate approval password at the hidden prompt. This preserves saved
runner settings and gives only the non-root operator account access to request
approvals; the build account remains separate. The broker removes the operator's
sudo/group privileges; finish old operator sessions afterward. Keep root SSH for
recovery. See [privilege approvals](../agents/privilege-approvals.md) for access
restrictions and incompatible sudo/hardening options. Drain jobs before maintenance.

## Check and use it

Confirm **Online/Idle** in GitHub's runner settings. On the VM:

```bash
sudo systemctl status basaltwater-github-runner.service
sudo systemctl is-enabled basaltwater-github-runner.service
```

Select it in a workflow with `runs-on: [self-hosted, Linux, X64, debian-ci]`.
The gate allows listed human users for `push`, `workflow_dispatch`, and
same-repository PRs (`opened`, `reopened`, `synchronize`). Forks, public
repositories, bots, reruns and other events are denied.

To change allowed users, drain jobs and repeat setup with the complete desired
allowlist. Setup also applies scope, group and label changes; provide a fresh
token or register later. Remove the retired runner entry from GitHub settings.
See the [operations reference](github-runner-operations.md) for logs, isolation,
updates and qualification.
