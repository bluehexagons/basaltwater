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
configuration. With `--build-server --build-host-policy FILE`, server setup
selects the managed on-demand worker instead of the legacy webhook executor.
The installed source must be root-controlled at `/opt/basaltwater`; see the
[installer contract](build-host-setup.md) for platform and resource requirements.
Combining managed enrollment with `--cicd` is refused until its shared-capacity
adapter is available.

The decoded mapping is saved with the rest of `SetupConfig` and reconstructed
for setup commands and controller-to-target arguments. Omitted policies remain
`None` and are preserved by patch merging. Explicit `--no-build-server` records
de-enrollment intent and clears the saved role and policy; explicit
`--build-server` clears that intent when re-enabling the role.
The selected setup step drains and removes only managed access and service
configuration, retaining accounts, queued jobs, artifacts, and recovery evidence.
It refuses removal while work is active. Omission of the policy and role never
requests removal.

The [pinned SSH client](build-client.md) can submit and inspect jobs on an
enrolled host. Saved builder selection and `--build-on` controller composition
are not available yet; ordinary deployments still build on the target.
