"""Read-only binding of saved client selections to private server references."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from lib.headscale_registry import HeadscaleServer, resolve_server
from lib.headscale_config import validate_headscale_url

if TYPE_CHECKING:
    from lib.config import SetupConfig


def resolve_cli_selection(args: argparse.Namespace, cached: SetupConfig | None = None) -> None:
    """Resolve only an explicit named input before constructing a patch.

    Omission and opt-out must reach the cache merge intact. Direct URLs also
    reach that merge intact so its migration guard can inspect the real input.
    """
    reference = getattr(args, "tailscale_server", None)
    if reference is None:
        return
    selected = resolve_client_server(
        reference,
        server_id=cached.tailscale_server_id if cached else None,
        endpoint=cached.tailscale_login_server if cached else None,
        reference_is_explicit=True,
        workspace=getattr(args, "workspace", None),
    )
    args.tailscale_server = selected.name
    args.tailscale_server_id = selected.id
    args.tailscale_login_server = selected.url


def validate_final_selection(config: SetupConfig, *, workspace: str | None = None) -> None:
    """Validate complete merged settings without locks, state or native calls."""
    from lib.headscale_config import validate_headscale_settings
    from common.headscale_preflight import validate_ingress

    if config.tailscale is not False and (config.tailscale_server_id or config.tailscale_server):
        selected = resolve_client_server(
            config.tailscale_server, server_id=config.tailscale_server_id,
            endpoint=config.tailscale_login_server, workspace=workspace,
        )
        config.tailscale_server = selected.name
        config.tailscale_server_id = selected.id
        config.tailscale_login_server = selected.url
    validate_headscale_settings(config, complete=True)
    validate_ingress(config)


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
