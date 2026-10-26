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

Saved artifact mode also selects [retained target reconciliation](build-target-activation.md#artifact-mode-setup-replay)
during target setup replay. The controller transport skips deployment repository
preparation/upload; the target skips inferred build runtimes and uses installed
descriptors and retained artifacts even without `deploy_specs`. Missing target
inputs require deployment from the controller and never fall back to uploaded
source or a target build. Explicit runtime-install flags keep their ordinary
meanings. Dry runs report reconciliation without reading installed artifacts.
Cloudflare/SSL setup options are outside this target policy and are refused.

Builder resolution, capability checks, build submission, initial external
bootstrap and artifact delivery remain separate controller work. This replay
integration does not start that flow or create new admission/build identities.
