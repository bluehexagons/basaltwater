# Git and SMB enrollment across controllers

Status: planned; automatic Gogs/SMB enrollment and the optional-user syntax
below are not implemented. Existing setup composes Gogs, Samba, Git HTTPS
credentials and SMB mounts, but does not provide this two-command workflow.
The [delivery plan](storage-enrollment-delivery.md) owns implementation order,
verification and readiness gates.

## Goal and agreed requirements

Provision a Debian storage VM and a Debian agent development VM with two setup
commands, assuming a prepared infrastructure provider and controller root SSH
access to both systems. Resolve storage servers by their saved friendly names.
Control Git and Samba data disks, services and enrollment independently; either
service may live on a different server. Support multiple enrollments and safe
reruns through different controllers without a shared controller credential
store or manual service-account/password creation.

Usernames are optional in the proposed flags:

```text
--gogs-enroll SERVER [USER]
--smb-enroll SERVER SHARE MOUNTPOINT [USER]
```

Gogs enrollment suppresses the agent profile's implicit GitHub credential
import, removing the need for `--git-auth none`. Explicit GitHub authentication
may coexist with Gogs; existing target GitHub credentials remain intact.
Suppression must survive saved-config loading, patch, refresh and reconstructed
commands. SMB enrollment alone does not change Git authentication defaults.

These requirements do not automate coding-provider authorization or replace
desktop/pairing authentication. The controller's root SSH keys and verified
host identities remain bootstrap inputs. See the existing [agent VM](../agents/agentic-vms.md),
[Git access](../agents/git-access.md) and [Samba](../storage/samba-shares.md) guides
for available behavior.

## Proposed two-command experience

All addresses, names, accounts and pools here are fictional. The Proxmox host
is already registered, with suitable image/snippet storage, guest networking
and verified SSH access. The following is a design example, not runnable
current CLI syntax:

```bash
basaltw setup server_web 10.23.0.40 gitadmin \
  --name storage --provision-on pve1 \
  --memory 2G --cores 2 \
  --storage root local-lvm 32G \
  --storage git-data bulk-lvm 128G \
  --storage-mount git-data /srv/gogs ext4 empty \
  --storage asset-data bulk-lvm 256G \
  --storage-mount asset-data /srv/assets ext4 empty \
  --access-source 10.23.0.0/24 \
  --gogs :3000 /srv/gogs --ssl \
  --samba --share write assets /srv/assets

basaltw setup agent_code_vm 10.23.0.50 agent \
  --name development --provision-on pve1 \
  --memory 8G --cores 4 \
  --storage root local-lvm 96G \
  --access-source 10.23.0.0/24 \
  --codex --node --python --go \
  --browser-automation playwright \
  --gogs-enroll storage \
  --smb-enroll storage assets /mnt/assets
```

Proposed omission of `USERS` from `--share` creates a share with no initial
members and no guest or authenticated-user fallback access. Enrollment adds
the selected client's membership. Existing explicit-user declarations remain
supported. A logical share name such as `assets` resolves to the saved export,
including its actual `assets_write` name; ambiguity is an error.

Both enrollment flags are repeatable and available to setup and patch. Parse
their exact arities, optional usernames and placement among positional setup
arguments deterministically. Identical duplicates converge; conflicting
declarations fail before mutation. Read-only shares remain read-only.

## Proposed identity and state ownership

Defaults in this and subsequent sections are recommendations for implementation,
not claims that every lifecycle choice has already been approved. Settle the
gates below before their dependent milestones.

| Owner | Durable state |
| --- | --- |
| Storage server | Stable system/service identities, service endpoints and certificate identities, export definitions, managed accounts, enrollment grants, recoverable generated credentials, credential generations and operation records. |
| Client | Stable system identity, root-owned enrollment intent and operation records, server/service IDs and last-known endpoints, installed credentials, certificate trust and observed applied generations. |
| Controller | Optional private name/address inventory, SSH configuration and non-secret observations. No required durable enrollment secrets or unique recovery records. |

Create persistent system identities on the targets under root control. Bind
enrollments to IDs, not mutable friendly names or IP addresses. Include the
local client user and selected remote account in the relationship. Generate a
readable, validated account name from the initial client name plus a stable
suffix; renaming the client preserves the account. Generated accounts are
separate for Git and SMB. Reuse one account per client system/local user/server/
service across that client's repositories or SMB mounts.

An explicit username may create a managed account or select an existing managed
one. Adoption of an unrelated existing account requires an explicit operation;
ordinary enrollment must not reset it. Intentional shared accounts share one
credential lifecycle and revocation scope. Reject multiple Git accounts for
the same local user and HTTPS origin initially; repository-specific credential
selection would need a separate qualified contract.

A fresh controller can read client intent and server records over verified SSH
and reconcile without the original workspace. Unknown friendly names still
need a non-secret inventory import or an explicit address bootstrap. Define
that route before the fresh-controller milestone; automatic network discovery
and fleet inventory replication are outside initial scope. Reject ambiguous
names, mismatched server IDs and unexpected endpoint/host-key changes before
granting access. Inventory import conveys discovery data, not SSH authority.

## Credential and access contract

Generate high-entropy service credentials on the storage server and preserve
them on ordinary reruns. Gogs/Samba password hashes cannot reconstruct client
passwords, so keep recoverable enrollment secrets in protected server-owned
state. Use root-owned private directories and mode-0600 files with bounded,
versioned schemas and atomic durable writes; reject unsafe files and unsupported
schema versions. Include these records and secrets in consistent service
backups. Define their final paths during implementation.

Relay only the selected client credential through verified SSH and private
stdin or bounded protected staging, with cleanup on success and failure. It
may pass through controller memory but must not enter controller credential
stores, argv, logs, saved commands, operation metadata or published artifacts.
Install Git credentials for the intended local user only; SMB credential files
remain root-only. The client never receives a Gogs administrator credential or
server root SSH private key. Use supported Gogs administrative interfaces;
do not mutate its database directly. Verify support and secret-safe invocation
against the managed releases before choosing password/token mechanics.

Create ordinary Gogs service users and non-interactive Samba accounts. Grant
no unrelated repository or share access. Root SSH authority permits the
controller to request the declared enrollment grants; credentials alone are
not an authorization policy. Resolve missing service capabilities, conflicting
accounts and invalid grants before changing either system.

Effective Samba membership is the union of explicitly declared members and
active managed enrollment contributions. Full setup, share-only changes,
panel changes and server-local refresh must preserve those contributions.
Removing one enrollment removes only its contribution; another explicit grant
may still authorize that account. Share deletion denies further access and
invalidates dependent enrollments while preserving files. Never reset passwords
for unrelated users or use a client enrollment to reconcile an incomplete
replacement list of server shares.

Install Git LFS before requested clones, derive the advertised HTTPS origin,
and retrieve certificate trust through authenticated server SSH. Scope trust
to that Git origin; do not disable TLS verification. Check client reachability
and report missing ingress rules without silently widening the firewall.
Keep native systemd SMB automount behavior. Preserve existing package/service/
firewall capability checks and declared-storage mount protections.

Live Gogs databases, repositories and LFS objects stay on server-local storage;
active worktrees, caches and builds stay on client-local storage. Samba holds
assets and deliberate exports. Enrollment never moves these paths or turns a
share into a Git transport.

## Reconciliation and lifecycle

Remote state is authoritative for existing relationships. Controller caches
cannot overwrite a newer remote generation. Read and validate the required
state before mutation; never resolve missing state by silently generating a
replacement credential. Local refresh repairs local installed configuration;
cross-system grants, rotation and recovery require a coordinating controller.

Use target-side helpers behind the setup mutation boundary and existing setup
locks. Define a deterministic lock order and bounded acquisition across server
and client operations. Locking must work across controllers and avoid nested
setup-lock deadlocks. Persist an operation ID, intended generation and phases
before external changes. Record account/grant preparation, client installation,
verification and completion separately. There is no atomic transaction across
both machines: uncertain results retain guards and require inspection of actual
state before recovery. Another controller must be able to complete a supported
recovery without guessing, deleting markers or blindly replaying mutations.

Omitting an enrollment flag preserves saved intent. Targeted removal detaches
one client configuration independently from server grant/credential revocation
and account deletion. Account deletion and stored-data deletion are distinct;
ordinary removal preserves repositories and share files. Do not delete a shared
credential while other consumers remain. Revocation must cover existing SMB
sessions as well as new authentication, with explicit disruption scope.

Rotation is explicit, generation-tracked and recoverable across every consumer
of the selected account. Do not promise uninterrupted rotation where a service
supports only one current password. Clone operations need a new client identity;
intentional restores preserve identity and reconcile credential generations.
An IP address alone cannot distinguish a clone from a restore. Older snapshots,
missing enrollment state and restored credentials require guarded recovery.

Dry runs generate no secrets, IDs, grants or state. Keep ordinary offline plan
behavior; any live inspection is an explicitly read-only operation. Health
reports expose IDs, generations and readiness separately without secret values.
Document what was actually verified rather than treating installed tools as
proof of end-to-end access.

## Decisions and implementation gates

| Decision | Recommendation | Gate |
| --- | --- | --- |
| Repository creation and grants | Enrollment creates an ordinary user/namespace only. Add explicit private-repository creation and read/read-write grants if a usable shared project must be part of the first delivery; keep `--repo` responsible for cloning. No implicit access to all private repositories. | Product scope remains open; settle before final CLI/schema and Git milestone. |
| Account/credential mechanics | Use independent generated service credentials and supported native administrative operations. Preserve operator-supplied legacy flows separately. | Prove managed Gogs account creation, credential installation, rotation and revocation without disclosure before coding the transport. |
| Inventory bootstrap | Import non-secret records from a known managed system or use a validated explicit address, then bind to verified server ID. | Finalize invocation and minimum metadata before fresh-controller acceptance. |
| Share grants | Omitted users means deny-all until enrollment; enrollment adds to the declared share access mode. Support an explicit deny/revocation model rather than inferring it from list omission. | Settle precedence and removal interface before Samba reconciliation changes. |
| Rotation, adoption and identity recovery | Explicit operations with retained guards and targeted effects; no automatic adoption or identity reset. | Finalize commands, generation checks and supported recovery cases before declaring lifecycle support. |

Broader Git hosting, off-host backup tooling, automatic discovery, other client
platforms, per-repository multi-account credentials, and a general external
secret manager are separate scope. Follow the [VM/storage plan](vm-management-and-lightweight-git-hosting.md)
for independent recovery and disk-growth work.
