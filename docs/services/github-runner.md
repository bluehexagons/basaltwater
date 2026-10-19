# GitHub Actions runner

Use a dedicated Debian 13/Trixie x86-64 VM for trusted jobs from private
`github.com` repositories. This persistent runner is not a sandbox for hostile
code. Live VM, reboot and GitHub qualification remain open; see the
[operations reference](github-runner-operations.md#qualification).

## Set up

```bash
basaltw setup server_github_runner ci.example.net operator \
  --machine vm --ci-organization my-org --ci-runner-group 'Private CI' \
  --ci-allowed-user alice --ci-label debian-ci
```

`--ci-organization` requires a GitHub organization. For a personal account or
a single repository, replace the organization and group flags with
`--ci-repository OWNER/REPO`. GitHub has no personal-account-wide runner scope;
sharing one registration across repositories requires an organization.
Repeat `--ci-allowed-user` for each trusted person.
For organization runners, choose allowed repositories under **Settings →
Actions → Runner groups → GROUP → Repository access** in GitHub, including
when using the default group.

Choose how to register:

| Option added to setup | Result |
| --- | --- |
| `--ci-register-with-gh` | Request a token using the controller's existing GitHub CLI login and register now |
| `--ci-registration-token-file PATH` | Register now using a private local token file (`chmod 600`) |
| Neither | Install in **Pending registration** state, with the service disabled |

You can add either registration option on the first setup or repeat the same
complete setup command later. Registered runners need no token on routine
setup. Credential errors leave registration pending and let the rest of setup,
including requested restarts, finish. `--dry-run` makes no token request.

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

If a token is rejected, create a fresh one and retry. After an interrupted
request, load status first. **Recovery required** needs the
[recovery procedure](github-runner-operations.md#updates-deregistration-and-recovery).

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
