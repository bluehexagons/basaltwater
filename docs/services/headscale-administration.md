# Headscale native administration

The local recovery adapter uses Headscale **0.29.7**, its private Unix socket,
and the managed server manifest. Install distribution `python3-grpcio` and
`python3-protobuf` before using native operations. Parser/help imports need only
the Python standard library. No public REST/gRPC API key is created.

Install the verified pinned server package, managed configuration and service,
then the adapter and its runtime dependencies before wiring the CLI or portal.
The portal must retain its existing authorization, CSRF and privileged review
boundary. Never give its service account the Headscale socket or group access.

The root command `basaltw headscale-admin` uses the exported parser
`add_headscale_admin_parser(subparsers)` and handler
`run_headscale_admin_command(args)`. Recovery also works over an independently authenticated
root SSH session with the installed service tool:

```bash
python3 /opt/basaltwater/common/service_tools/headscale_admin.py users-list
python3 /opt/basaltwater/common/service_tools/headscale_admin.py users-create --name alice
python3 /opt/basaltwater/common/service_tools/headscale_admin.py nodes-list
python3 /opt/basaltwater/common/service_tools/headscale_admin.py approve --user alice
python3 /opt/basaltwater/common/service_tools/headscale_admin.py expire --id 7
python3 /opt/basaltwater/common/service_tools/headscale_admin.py revoke --id 7
```

Each example also works as `sudo basaltw headscale-admin ACTION [OPTIONS]`.
Approve accepts the exact native user name or a reviewed numeric portal user ID.
Busy or unsafe operation locks return a bounded failure and preserve private state.

Approve prompts without echo on a private terminal; `--auth-id-stdin` supports
private authenticated transport and `--auth-id-file` accepts a private owned
regular file. Never put an Auth ID in a command argument or logged request body.
Verify the exact person, user and native Auth ID through a trusted channel.
There is no pending-device enumeration or hostname-based approval. Expired or
consumed requests fail natively; start a new client registration after inspection.
`--dry-run` validates input without native calls or file/lock changes and omits
the Auth ID from output. List operations only observe native state.

`execute_admin_action(action, parameters, *, expected_endpoint=None)` exports
these exact bounded schemas:

| Action | Parameters | Result |
| --- | --- | --- |
| `users_list` | `{}` | `users` array |
| `users_create` | `name` | `user` |
| `users_delete` | numeric string `id` | verified `deleted` ID |
| `nodes_list` | `{}` | `nodes` array |
| `register` | numeric user ID or exact user name in `user`, private `auth_id` | verified `node` |
| `node_expire` | numeric string `id` | observed `node` with expiry |
| `node_delete` | numeric string `id` | verified `deleted` ID |

Only approved output fields escape the adapter. Node fields are `id`, `node_id`,
`hostname`, `addresses`, `tags`, `user_id`, `online`, `expiry`. Users expose `id`
and `name`. Tagged nodes have no personal owner. The native decimal node ID is
the Tailscale `Self.ID`: the pinned source's `NodeID.StableID()` formats it in
base ten. Node expiry is independent of bootstrap-key expiry. User deletion
refuses remaining owned nodes and checks native absence afterward.
Portal reviews bind the numeric user ID and trusted managed endpoint; execution
resolves the current name for the native registration RPC and verifies the
returned node's ownership. The optional `expected_endpoint` is trusted review
metadata, separate from the action parameters. The native adapter checks it
against the verified running endpoint before any RPC and refuses changes.

The management-side DNS publication lease is the fixed private
`/var/lib/basaltwater/headscale/dns-publication.json`, using the existing DNS
binding schema and `dns_binding_lease`. Its key is the managed endpoint plus
decimal node stable ID. The publisher must hold that same lease while reserving,
publishing, withdrawing or replacing DNS. An advertised reservation blocks both
expiry and removal; native global/split resolver addresses also block removal.
The DNS node's separate local binding cannot substitute for this management
record. The explicit `basaltw headscale-dns` workflow holds this management lease
while reserving, publishing, withdrawing or replacing DNS.

Root/controller machine enrollment uses `issue_machine_key(approved_service_tags)`.
The explicit tags are the caller's authorization; portal actions cannot issue
keys. This creates a tagged, single-use, non-ephemeral key expiring one hour from
issuance, verifies returned attributes, and writes its value only to a newly
created mode-600 transient file in a root-owned mode-700 directory. The result
contains only ID, expiry, tags and nonsecret flags. A private issuance intent
tracks delivery and blocks further issuance even after success or lost ack.
The explicit controller enrollment workflow delivers the transient key through
private SSH transport and removes it after submission. Never print or copy the
file into setup/cache/audit artifacts. See [controller enrollment](headscale-management.md).

`manage_machine_key(action, key_id)` accepts `expire`, `revoke` (native expiry)
or `delete`. It verifies the result without returning a key value. Revoking a
key prevents future use; separately expire/remove existing nodes to offboard.
For uncertain issuance, inspect native keys and nodes through private root
recovery using `inspect_machine_keys()` (safe metadata, never native key values).
`reconcile_machine_key(expected_key_ids, expected_node_ids)` requires
the exact reviewed new-key IDs since the intent and all current node IDs,
expires outstanding keys, verifies expiry, discards private delivery and clears
the intent. It preserves enrolled nodes. Never automatically replay issuance.

With no arguments, the service tool accepts one bounded JSON object on private
SSH stdin with `action` and `parameters`. It returns safe JSON metadata only.
Besides portal actions, root transport supports `machine_key_issue` with
`approved_service_tags`, `machine_keys_list` with `{}`, `machine_key_expire`/`machine_key_revoke`/
`machine_key_delete` with `id`, and `machine_key_reconcile` with
`expected_key_ids` and `expected_node_ids`. Callers must suppress stdin/body
logging, bound SSH execution, and reconcile missing acknowledgements.

The small protobuf descriptor subset follows the official
[v0.29.7 schemas](https://github.com/juanfont/headscale/tree/v0.29.7/proto/headscale/v1)
and [node identity implementation](https://github.com/juanfont/headscale/blob/v0.29.7/hscontrol/types/node.go).
Calls use ten-second deadlines, bounded messages and disabled retries; raw
native errors are suppressed. Offline tests use mocked calls and temporary
files. See the [qualification record](../testing/headscale.md) for verified
native socket, machine enrollment, personal approval/revocation, SSH and broker
sandbox behavior, and the remaining platform checks.
