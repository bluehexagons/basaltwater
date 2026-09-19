# Expose the CI/CD webhook through Cloudflare Tunnel

Configure the server with `--cicd` and `--cloudflare` first. The webhook
receiver listens on `127.0.0.1:8765`; Nginx exposes it at
`/webhook/health` and forwards the public request to that local receiver.

## Tunnel ingress

Edit `/etc/cloudflared/config.yml` and add the webhook rule before the
catch-all rule:

```yaml
ingress:
  - hostname: webhook.example.com
    service: http://localhost:8080

  # Other routes go here.
  - service: http_status:404
```

Create a proxied CNAME in Cloudflare DNS:

| Field | Value |
| --- | --- |
| Type | `CNAME` |
| Name | `webhook` |
| Target | `<tunnel-id>.cfargotunnel.com` |
| Proxy status | Proxied |

Restart or reload `cloudflared` after changing its configuration.

## GitHub webhook

In the repository's GitHub settings, add a webhook with:

| Field | Value |
| --- | --- |
| Payload URL | `https://webhook.example.com/webhook` |
| Content type | `application/json` |
| Secret | Contents of `/etc/basaltwater/cicd/webhook_secret` |
| SSL verification | Enabled |
| Events | Push, or the events required by the repository |
| Active | Enabled |

The receiver verifies the HMAC-SHA256 signature and accepts only configured
repositories and branches.

## Verify the path

Check the receiver directly, through Nginx, and through the service journal:

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8080/webhook/health
sudo systemctl status webhook-receiver.service --no-pager
sudo journalctl -u webhook-receiver.service -f
```

GitHub's ping event confirms connectivity. It does not build a repository.

The receiver is localhost-only, Nginx applies rate limiting, and builds run as
the dedicated `webhook` user. Treat the webhook secret and URL as credentials.
