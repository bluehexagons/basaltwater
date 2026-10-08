# CI/CD webhook over Cloudflare Tunnel

The CI/CD webhook uses a dedicated Nginx listener on 127.0.0.1:8080, which
proxies to the receiver on 127.0.0.1:8765. Cloudflare Tunnel must target the
Nginx listener so its rate limiting and request handling remain in place. The
generated application-site ingress normally targets local port 80; use this
separate route for the webhook hostname.

## Prepare the host

Set up the build server with both `--cicd` and `--cloudflare`. Create or refresh
the tunnel with `sudo setup-cloudflare-tunnel`. If setup reports that the
webhook ingress needs a manual update, edit `/etc/cloudflared/config.yml` and
add the webhook hostname before the catch-all rule:

~~~yaml
ingress:
  - hostname: webhook.example.com
    service: http://localhost:8080

  # Keep other host routes above the catch-all.
  - service: http_status:404
~~~

Use the tunnel ID shown by the helper for the proxied CNAME in Cloudflare DNS:

| Field | Value |
| --- | --- |
| Type | `CNAME` |
| Name | `webhook` |
| Target | `<tunnel-id>.cfargotunnel.com` |
| Proxy status | Proxied |

Restart the service after editing the ingress:

~~~bash
sudo systemctl restart cloudflared
sudo systemctl status cloudflared --no-pager
~~~

## Verify the route

Check the local Nginx proxy and then the public hostname:

~~~bash
curl -fsS http://127.0.0.1:8080/webhook/health
curl -fsS https://webhook.example.com/webhook/health
sudo journalctl -u webhook-receiver.service -n 100 --no-pager
~~~

Configure GitHub's webhook path and secret using the
[CI/CD guide](../../docs/CICD.md). A GitHub ping checks connectivity but does
not build a repository. Keep ports 8080 and 8765 bound to loopback; do not
expose either listener directly.
