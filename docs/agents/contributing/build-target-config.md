# Managed typed-target setup configuration

`SetupConfig.build_target_enrollment` stores the decoded schema-1 typed-target
enrollment envelope. The controller-facing `--build-target-policy FILE`
contract accepts a regular, non-symlink UTF-8 JSON file no larger than 4 MiB,
rejects duplicate JSON keys and private-key PEM blocks, then validates the
mapping with `common.build_target_steps.parse_build_target_enrollment()`.
That parser is loaded only when a policy is present, so ordinary setup remains
usable when the typed-target setup module is not installed in this revision.

The envelope shape is `{schema: 1, policy: <lib.build_target_policy>, keys:
{principal: canonical_ed25519_public_key}, max_records, max_bytes}`. The parsed
mapping is saved in setup cache and transported as hidden
`--build-target-enrollment-json JSON`; the local filename is never forwarded.
`--no-build-target` records explicit removal intent and clears the saved policy
during patch merging. Supplying a policy again clears that intent. Omitted
policy and disable intent preserve saved state.

This slice carries configuration only. There is no `--build-on` controller
flow, and it does not make target enrollment ready for controller use. Removing
managed typed-target authority must preserve applications and their data; the
installer owns those target-side effects and is outside this change.
