# Build host enrollment configuration

`SetupConfig.build_host_enrollment` stores an optional decoded JSON mapping for
build-host enrollment. Controller `--build-project DOMAIN[/PATH] HTTPS_URL`
generates it from pinned manifests and the selected SSH public companion;
project patches merge into the saved principal without dropping unrelated grants.
See [controller preparation](build-setup.md). Alternatively, the controller
accepts a public policy through `--build-host-policy FILE`; it reads a regular,
non-symlink file up to 4 MiB,
rejects duplicate JSON keys, and refuses private-key PEM material. The policy
mapping and its public key data are then serialized to the target with the
internal `--build-host-enrollment-json JSON` argument. The local filename is
never forwarded.

Bare `--build-server` generates wildcard project authority for principal
`deploy` using the selected Ed25519 public companion. Updates require the
current companion to match existing enrollment. Optional project flags select
restrictive grants. Go, Node and Python are installed automatically.
The decoded policy is saved with setup; omitted patch options preserve it.
`--no-build-server` removes managed authority while retaining records and data.
SSH and signed webhook intake use one managed worker and shared capacity with
optional Actions runners. See [architecture and migration](../../services/build-systems.md).
