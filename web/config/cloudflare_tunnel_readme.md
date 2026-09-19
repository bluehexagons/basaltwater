# Cloudflare Tunnel setup

When a host is configured with `--cloudflare`, basaltwater installs
`setup-cloudflare-tunnel` and prepares the Nginx origin for Cloudflare Tunnel.

## Automated setup

Run the helper on the configured host:

```bash
sudo setup-cloudflare-tunnel
```

It installs `cloudflared` when needed, authenticates with Cloudflare, creates
or reuses a tunnel, discovers enabled Nginx sites, writes the tunnel
configuration, and can install and enable the systemd service. Direct public
HTTP/HTTPS remains open until the helper verifies that service is active; only
then does it close ports 80 and 443 with UFW.

The helper stores configuration and tunnel credentials under
`/etc/cloudflared`. Its state file is
`/etc/cloudflared/tunnel-state.json`; rerun the helper after adding or removing
an Nginx site to refresh ingress rules.

## Manual setup

1. Install `cloudflared` using the [Cloudflare installation guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Authenticate and create a tunnel:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create basaltwater
   ```

3. Replace `<tunnel-id>` below with the ID returned by `cloudflared tunnel create`.
   Write the result to `/etc/cloudflared/config.yml`:

   ```yaml
   tunnel: <tunnel-id>
   credentials-file: /etc/cloudflared/<tunnel-id>.json

   ingress:
     - hostname: example.com
       service: http://localhost:80
     - hostname: api.example.com
       service: http://localhost:80
     - service: http_status:404
   ```

4. Install and start the service:

   ```bash
   sudo cloudflared service install
   sudo systemctl enable --now cloudflared
   ```

Keep the `http_status:404` rule last. Add DNS records for each hostname in
Cloudflare Zero Trust/DNS and point them at the tunnel.

## Origin behavior

With `--cloudflare`, generated Nginx sites serve the tunnel origin over local
HTTP instead of redirecting it to HTTPS. Applications still receive
`X-Forwarded-Proto: https`, so they generate secure public URLs correctly.

Verify the tunnel and generated routes with:

```bash
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
sudo sed -n '1,160p' /etc/cloudflared/config.yml
```

Cloudflare Tunnel does not proxy UDP. Services such as Antistatic STUN still
need their direct public UDP port and any required direct TCP port.
