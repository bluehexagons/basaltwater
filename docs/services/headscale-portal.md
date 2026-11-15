# Headscale administration in the Web Portal

The Headscale page is available when root setup observes a managed local
Headscale installation and exports `features.managed_headscale` in the portal
manifest. An external registry entry cannot enable administration. Proxmox
panels retain their restricted scope. Rerun portal setup after installing
Headscale. The private Headscale manifest keeps its existing permissions.

Use the authenticated HTTPS portal to inspect people and nodes, create a person,
register their verified pending Auth ID, and request native node expiry or
deletion. Remove a person's nodes before deleting their user. Verify the exact
Auth ID and intended person over an authenticated channel; hostname alone is
insufficient. Approval admits that device into the shared network trust boundary.
New networks default to all-to-all access; adopted networks retain their policy.

Every mutation creates a separate privilege review requiring an explicit decision
through the existing approval service. Portal sign-in and T3 pairing do not
approve mutations. Inspection uses only the broker's authorized requester UID;
the portal never connects to the native socket or reads the database or API keys.
Authenticated SSH administration remains the independent recovery path.
Each review records and displays the trusted managed endpoint. Execution checks
that endpoint again when claiming the review and inside the native adapter;
a changed endpoint requires a fresh review, and invalidation discards any staged
registration input. Browser input cannot select a different administrative endpoint.

Registration input lives only in broker memory for at most five minutes. Durable
plans and reviews contain an opaque token bound to the account, selected user,
operation and exact request digest. Execution consumes it once after approval.
Denial, cancellation, expiry and restart discard it. A lost acknowledgement does
not authorize replay: inspect requests and nodes before initiating and verifying
a fresh native registration. Failed or uncertain executions are never retried.
The Auth ID does not appear in argv, SQLite, review pages or error responses.
The broker retains at most 4096 in-memory input digests to reject duplicate
submissions during its lifetime; reaching that bound refuses further inputs.

Observed native device expiry is distinct from the one-hour bootstrap-key
lifetime used by authorized machine controllers. The page issues no machine
keys. Revoking a bootstrap key does not establish removal of an enrolled node;
verify native node revocation against its connected client.

Integration uses the lazy `lib.headscale_admin.execute_admin_action(action,
parameters, expected_endpoint=reviewed_endpoint)` adapter. Fixed actions are
users_list, users_create, users_delete, nodes_list, register, node_expire and
node_delete. Native errors and mutation
payloads are never exported. Inventory exports only typed identity, ownership,
address, tag, online and observed-expiry fields, bounded to 200 records per list
and the broker response limit. Larger inventories require SSH inspection.

Offline routing, policy, review and transient-input tests are in
`tests/test_headscale_portal.py`. Frozen candidate `68b1ebdc` passes the full
84-check Chromium/Firefox application run, including 18 Headscale checks with
synthetic broker responses. Separate installed Debian HTTPS portal checks pass
BasicAuth, reviewed native user creation/deletion and inventory, forged-CSRF
rejection (403), and changed-review, cross-origin and replay rejection (409).
The actual root broker sandbox also passes native administration and shared-lock
contention with host root. These checks preserve the security boundaries above.
Installed HTTPS checks also pass exact personal Auth-ID approval/denial,
five-minute expiry and replay refusal, with native user ownership verified.
Separately reviewed deletion removes an already connected device; access stays
revoked after its daemon restarts, while independent root SSH remains available.
The public onboarding origin has no administrative routes; direct Unix backend
calls cannot bypass peer identity by forging valid proxy-header names.
Integrated native browser flows, native mobile onboarding and Firefox ESR retain
separate qualification boundaries. Backend candidate `0d0e11c4` passes combined
CI (10,045 tests, 26 skipped); see the
[qualification record](../testing/headscale.md). The public
onboarding origin has no administrative authority; see the
[enrollment contract](../plans/headscale-enrollment.md).
