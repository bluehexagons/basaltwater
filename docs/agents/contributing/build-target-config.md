# Managed typed-target setup configuration

`SetupConfig.build_target_enrollment` stores the decoded schema-1 typed-target
enrollment envelope. The controller-facing `--build-target-policy FILE`
contract accepts a regular, non-symlink UTF-8 JSON file no larger than 4 MiB,
rejects duplicate JSON keys and private-key PEM blocks, then validates the
mapping with `common.build_target_steps.parse_build_target_enrollment()`.
That parser is loaded only when a policy is present; ordinary setup does not
load typed enrollment validation.

The envelope shape is `{schema: 1, policy: <lib.build_target_policy>, keys:
{principal: canonical_ed25519_public_key}, max_records, max_bytes}`. The parsed
mapping is saved in setup cache and transported as hidden
`--build-target-enrollment-json JSON`; the local filename is never forwarded.
`--no-build-target` records explicit removal intent and clears the saved policy
during patch merging. Supplying a policy again clears that intent. Omitted
policy and disable intent preserve saved state.

The [managed installer](build-target-setup.md) consumes this configuration.
The [minimal bootstrap phase](build-target-bootstrap.md) preserves an existing
saved application profile during initial controller preparation. There is no
automatic application activation during that phase. The public
[`--build-on` controller flow](build-setup.md) then delivers artifacts and applies
the full profile against exact expected releases. Removing managed typed-target authority
preserves applications and their data; the installer owns those effects.
