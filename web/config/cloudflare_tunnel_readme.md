# Cloudflare Tunnel setup

When a host is configured with `--cloudflare`, basaltwater installs
`setup-cloudflare-tunnel` and prepares the Nginx origin for Cloudflare Tunnel.
The first interactive setup run starts the wizard in the original setup
terminal after deploying the sites. Open the authentication URL in your browser;
the configuration's friendly name is suggested as the tunnel name. Subsequent
setup runs refresh the saved tunnel automatically.

## Automated setup

For recovery or manual changes, run the helper on the configured host:

```bash
sudo setup-cloudflare-tunnel
```

It installs `cloudflared` when needed, authenticates with Cloudflare, creates
or reuses a tunnel, discovers enabled Nginx sites, writes the tunnel
configuration, and can install and enable the systemd service. Direct public
HTTP/HTTPS remains open until the helper verifies that service is active; only
then does it apply the saved direct web access policy with UFW. By default it
closes unrestricted ports 80 and 443. Setup's `--lan-access` / `--access-source`
retain direct access from their networks; `--cloudflare-allow-direct` allows
unrestricted direct HTTP/HTTPS alongside the tunnel.

For local changes after setup, run one of:

```bash
sudo setup-cloudflare-tunnel --non-interactive --allow-direct
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --access-source 192.168.0.0/24
sudo setup-cloudflare-tunnel --non-interactive --no-allow-direct --clear-access-sources
```

Omitting these options preserves the saved policy. LAN clients can use hosts-file
entries mapping deployed hostnames to the VM and browse their HTTP URLs. This
is direct origin access alongside the tunnel; a bare IP URL does not select a
particular hostname-based Nginx site.

The helper stores configuration and tunnel credentials under
`/etc/cloudflared`. Its state file is
`/etc/cloudflared/tunnel-state.json`; rerun the helper after adding or removing
an Nginx site to refresh ingress rules.

## Manual setup

1. Install `cloudflared` using the [Cloudflare installation guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Authenticate and create a tunnel as your regular user:

   ```bash
   cloudflared tunnel login
   cloudflared tunnel create basaltwater
   ```

3. Set `TUNNEL_ID` to the ID printed by `cloudflared tunnel create`. Install
   the credentials file for the system service with root-only permissions, then
   write `/etc/cloudflared/config.yml`:

   ```bash
   TUNNEL_ID="replace-with-the-id-from-cloudflared"
   sudo install -d -o root -g root -m 0750 /etc/cloudflared
   sudo install -o root -g root -m 0600 \
     "$HOME/.cloudflared/${TUNNEL_ID}.json" \
     "/etc/cloudflared/${TUNNEL_ID}.json"

   sudo tee /etc/cloudflared/config.yml >/dev/null <<EOF
   tunnel: ${TUNNEL_ID}
   credentials-file: /etc/cloudflared/${TUNNEL_ID}.json

   ingress:
     - hostname: example.com
       service: http://localhost:80
     - hostname: api.example.com
       service: http://localhost:80
     - service: http_status:404
   EOF
   sudo chmod 0600 /etc/cloudflared/config.yml
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
Manifest service components can declare these through `public_udp`; setup
opens the host UFW rules and retains them when closing web ports. Optional UDP
hostnames must use DNS-only A/AAAA records for the server's public IP, with the
same UDP allowance in any provider firewall.
