"""Read-only binding of saved client selections to private server references."""

from __future__ import annotations

from lib.headscale_registry import HeadscaleServer, resolve_server
from lib.headscale_config import validate_headscale_url


def resolve_client_server(
    reference: str | None,
    *,
    server_id: str | None = None,
    endpoint: str | None = None,
    reference_is_explicit: bool = False,
    workspace: str | None = None,
) -> HeadscaleServer:
    """Resolve discovery without changing an existing client's network identity.

    Saved selections follow their stable record ID after a rename. A newly
    supplied name is checked against that binding, and every saved endpoint is
    compared before returning configuration for remote transport. Neither a
    changed record nor a different selection authorizes client migration.
    """
    if server_id is not None and endpoint is None:
        raise ValueError("Saved Headscale client selection is missing its endpoint")
    if endpoint is not None:
        endpoint = validate_headscale_url(endpoint)
    if server_id is not None:
        selected = resolve_server(server_id, workspace)
        if reference_is_explicit:
            if reference is None or resolve_server(reference, workspace).id != selected.id:
                raise ValueError("Client network change requires explicit leave or migration")
    elif reference is not None:
        selected = resolve_server(reference, workspace)
    else:
        raise ValueError("A named Headscale server reference is required")
    if endpoint is not None and endpoint != selected.url:
        raise ValueError("Headscale endpoint changed; reconcile the client before migration")
    return selected
