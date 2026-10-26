# Build host enrollment configuration

`SetupConfig.build_host_enrollment` stores an optional decoded JSON mapping for
build-host enrollment. The controller accepts the public policy through
`--build-host-policy FILE`; it reads a regular, non-symlink file up to 4 MiB,
rejects duplicate JSON keys, and refuses private-key PEM material. The policy
mapping and its public key data are then serialized to the target with the
internal `--build-host-enrollment-json JSON` argument. The local filename is
never forwarded.

The config layer checks that the JSON top level is an object and that a policy
is only attached to a config with `is_build_server=True`, then calls
`common.build_host_steps.parse_build_host_enrollment()` for strict policy and
public-key validation. The installer must finish that validation before it
makes any target changes. The config deliberately does not require every build
server to have an enrollment policy, preserving legacy build-server
configuration. This slice does not compose or enable a build-host role.

The decoded mapping is saved with the rest of `SetupConfig` and reconstructed
for setup commands and controller-to-target arguments. Omitted policies remain
`None`; the generic config patch merge treats `None` as omission, so clearing a
saved policy requires an explicit merge-level clear rule if a caller needs that
operation.
