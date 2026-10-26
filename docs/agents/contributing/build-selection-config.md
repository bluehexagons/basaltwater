# Controller builder selection configuration

`SetupConfig.build_on` stores an opaque saved builder reference in controller
workspace setup state. It is limited to 128 characters and rejects empty,
trimmed, or control-character values; this configuration slice does not resolve
hostnames, names, tags, or credentials. Credentials remain in the controller's
existing local stores and are never added to target setup arguments.

`--build-on SAVED-BUILDER` enables `artifact_deployments` and resets any saved
clear intent. Omitted selection options preserve the cached builder selection
and artifact mode. `--no-build-on` clears the reference and artifact mode during
patch merging. The hidden `--artifact-deployments` flag is accepted by both
controller and remote parsers so a target-only setup replay retains its mode
without receiving a builder reference. Remote arguments never include the
builder reference or clear intent.

This slice transports saved intent only. Builder resolution, capability checks,
build submission, artifact transfer, and target execution remain outside this
configuration change.
