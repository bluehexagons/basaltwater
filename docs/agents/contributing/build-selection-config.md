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

Patch deployment merging follows the effective `build_on` reference after
selection changes and preservation rules. With a selected builder, each
explicitly patched destination replaces every saved source selection for that
destination. Omitted destinations and the omitted builder selector survive.
Comma-separated destinations are expanded individually; domain case and a final
DNS dot are normalized, and omitted/root slashes and trailing path slashes
identify the same destination. Path case remains significant. The existing
deploy parser/validators and Nginx path validator reject invalid destinations
before merging. Repeated identical destination/source selections collapse;
different sources for one normalized destination within the same patch refuse
before source preparation or transport.

An explicit builder selection or switch uses these replacement rules, including
when selecting a builder for an ordinary saved setup. `--no-build-on` restores
ordinary full-pair merging for that invocation. Artifact mode or target
enrollment alone never selects replacement semantics. Ordinary build-on-target
merging retains its existing behavior. Delivery planning still validates the
complete application/route map; destination replacement does not bypass those
checks or change retained-attempt recovery.

Saved artifact mode also selects [retained target reconciliation](build-target-activation.md#artifact-mode-setup-replay)
during target setup replay. The controller transport skips deployment repository
preparation/upload; the target skips inferred build runtimes and uses installed
descriptors and retained artifacts even without `deploy_specs`. Missing target
inputs require deployment from the controller and never fall back to uploaded
source or a target build. Explicit runtime-install flags keep their ordinary
meanings. Dry runs report reconciliation without reading installed artifacts.
Cloudflare/SSL settings are enrolled separately through administrative target
bootstrap. They are retained in protected target routing state and reused by
activation and refresh; build inputs cannot change them. Cloudflare origins
remain HTTP without an origin HTTPS redirect. Direct TLS uses the ordinary
scoped Nginx/certificate setup.

Builder resolution, capability checks, build submission, initial external
bootstrap and artifact delivery remain separate controller work. This replay
integration does not start that flow or create new admission/build identities.
