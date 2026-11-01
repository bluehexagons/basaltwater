"""Web server setup steps."""

from __future__ import annotations

from .web_steps import (
    install_nginx,
    configure_nginx_security,
    create_hello_world_site,
    configure_default_site,
)

from .ssl_steps import install_certbot

from .cloudflare_steps import (
    configure_cloudflare_firewall,
    create_cloudflared_config_directory,
    configure_nginx_for_cloudflare,
    install_cloudflared_service_helper,
    run_cloudflare_tunnel_setup,
)

from .dev_tools_steps import (
    configure_auto_update_node,
)

__all__ = [
    "install_nginx", "configure_nginx_security", "create_hello_world_site", "configure_default_site",
    "install_certbot", "configure_cloudflare_firewall", "create_cloudflared_config_directory",
    "configure_nginx_for_cloudflare", "install_cloudflared_service_helper", "run_cloudflare_tunnel_setup",
    "configure_auto_update_node",
]
