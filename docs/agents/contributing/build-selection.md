# Controller saved builder and target selection

`lib.build_selection` resolves saved `--build-on` intent and controller-local
SSH endpoints. It performs bounded reads and metadata checks only. It does not
start SSH, contact a host, build, deploy, change setup state, create trust files,
or wire the CLI. Standard build-on-target behavior remains unchanged.

## API

- `select_saved_builder(reference, configs) -> SetupConfig` is pure selection
  over a supplied sequence. It matches the entire host, friendly name, or tag,
  case-insensitively. Each record counts once even when several labels match.
  Zero matches or more than one matching record raises `ValueError`. Host
  matches have no precedence over name/tag matches, and ineligible records still
  participate in ambiguity detection. This helper does not check enrollment.
- `resolve_saved_builder(reference, *, workspace=None) -> BuilderSelection`
  loads **all** records with `load_all_setup_commands(workspace)`, selects one,
  then resolves its builder endpoint. Inventory errors propagate. It never uses
  the cache's first-tag-match lookup. References follow the configuration's
  1–128 character bound and reject surrounding whitespace and control characters.
- `resolve_builder_endpoint(config, *, workspace=None) -> SSHEndpoint` resolves
  a supplied builder configuration, requiring `is_build_server=True`,
  `disable_build_host=False`, and strict managed `build_host_enrollment`.
  A legacy bare `--build-server` is rejected.
- `resolve_target_endpoint(config, *, workspace=None) -> SSHEndpoint` uses the
  passed target's `build_target_enrollment`, requires
  `disable_build_target=False`, and revalidates that complete enrollment.
  The target's saved setup username does not select its typed transport account.

Both resolvers require an explicitly saved `machine_type` of `vm` or
`hardware`. Their enrollment parsers enforce the initial Debian 13 x86-64
policy platform. Saved state cannot prove the remote operating system, service
availability, or live grants; those remain installer and authenticated service
checks. Controller machine capabilities are never substituted for target facts.

`BuilderSelection` is a frozen dataclass with `config` (the selected
`SetupConfig`, retained by reference) and `endpoint`. `SSHEndpoint` is frozen
and contains `host`, `username`, `port=22`, `identity_file`, `known_hosts_file`,
and `principal`. Its `connection_kwargs()` returns a fresh `SSHConnectionKwargs`
typed dictionary containing the five SSH constructor arguments; `principal` is
controller metadata. Supply an explicit finite `timeout` separately:

```python
from lib.build_client import BuildHostClient
from lib.build_selection import resolve_saved_builder

selected = resolve_saved_builder("builds", workspace="/home/controller/workspace")
with BuildHostClient(**selected.endpoint.connection_kwargs(), timeout=300) as client:
    grants = client.capabilities()
```

Target endpoints use the same constructor keywords with
[`BuildTargetClient`](build-target-client.md). Selection itself never constructs
either client or sends these local paths or the retained setup config to a host.

## Explicit identity binding and trust

The saved `ssh_key` path is the identity selector, even when it previously
served general setup SSH access. Only when it is `None` is the established
`~/.ssh/id_ed25519` selected. There is no key-directory search, alternate-key
retry, identity override, SSH agent lookup, or ambient SSH configuration.
An unmatched key fails with controller guidance to enroll its `.pub` key or
explicitly update the saved `ssh_key`. A missing selected file also fails;
it never triggers selection of another identity.

Paths expand `~` and must then be canonical absolute text paths using repository
validation. Relative paths and traversal components are rejected. The private
identity is checked through no-follow metadata only: effective-UID ownership,
one link, regular-file type, and no group/other permissions (`0600` or stricter).
Its bytes are never opened, parsed, hashed, or passed to `ssh-keygen`.

The matching `<identity_file>.pub` companion is opened with the
[`BuildHostClient`](build-client.md) trusted-file helper: no final symlink,
single-link regular file, root or effective-UID ownership, and no group/other
writes. Reads are capped at 16 KiB plus one overflow-detection byte; size,
descriptor identity, and pathname identity are checked. It must contain exactly
one canonical Ed25519 public key, optionally followed by an ordinary ssh-keygen
space-separated comment and one terminal newline. Only the comment is removed
before canonical enrollment-key validation; key options, additional lines,
control characters, other algorithms, and malformed/noncanonical blobs fail.
Enrollment keys themselves remain strict and uncommented.

Exactly one enrollment principal must match that public companion. Duplicate
public keys are already rejected by the strict enrollment parsers. The SSH
username is `bw-build-` or `bw-target-`, followed by the first 12 hexadecimal
characters of SHA-256 of the ASCII principal. The public companion establishes
the selected enrollment binding; it does **not** prove that the private bytes
are its matching secret. The later SSH authentication establishes that fact.

Trust uses only `get_known_hosts_path(workspace)` from the selected workspace.
The resolver checks that existing file with the client's no-follow, regular,
single-link, root/effective-UID-owned, non-group/other-writable rules. It does
not scan, create, update, or parse host-key entries. A missing file fails.
The client later pins and revalidates credentials and OpenSSH enforces exact
host-key membership. Resolution does not retain open descriptors or promise
that pathnames cannot change before client construction. The shared client
helper protects the final file component; this slice adds no ancestry policy.

Focused tests use disposable key placeholders and workspaces, mock account and
process/network boundaries, and exercise actual enrollment parsing, ambiguity,
binding, local permissions, cache reads, and client-constructor compatibility.
They do not inspect real user credentials or qualify a live Debian/SSH host.
