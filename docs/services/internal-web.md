# Internal HTTPS sites and live previews

`basaltwater-web` publishes static builds, Godot web exports, and loopback development servers
through the HTTPS gateway managed by basaltwater. The gateway reuses the VM's
certificate and saved access-source policy; do not bind development servers to
`0.0.0.0`, edit Nginx, or add UFW rules manually.

The gateway is installed by features that configure the internal web host,
including the T3 Code web interface and the Godot web bundle.
T3 Code installations also place the managed web-gateway skill in compatible
terminal agents, so the publishing workflow is discoverable without enabling
the Godot bundle.

Run `basaltwater-web` commands on that configured VM as the account that owns the
site, not on a separate controller. Check `command -v basaltwater-web` first. If it
is missing, provision a gateway-capable feature before continuing; the
[beginner walkthrough](../getting-started/getting-started.md#5-try-another-feature) shows one path.

In the Basaltwater web panel, open **Web services** to find published sites
and managed project previews. Preview cards show the checkout name, target
loopback service, and systemd unit. Use **Copy URL** to open the LAN address
on the device you want to test. Host-only forwards are labeled **Host URL**.
**Certificate trust instructions** links to enrollment help for that device.
A forwarded service's TCP check runs from the host and only probes the loopback
target. To check HTTPS from the host, run `basaltwater-web preview status NAME`
or `basaltwater-web forward doctor NAME`.
These commands probe the configured URL directly with normal TLS validation.
A redirect status confirms the named listener responded. The destination is
unchecked. The doctor's JSON retains the URL and VM origin on transport failures.
Open the LAN URL on the client device to verify its routing and trust.

## Choose a topic

<a id="try-a-plain-html-page"></a>
<a id="publish-a-static-site"></a>
<a id="publish-a-godot-site"></a>
<a id="start-a-managed-live-preview"></a>
<a id="forward-an-existing-loopback-service"></a>

- [Internal web publishing](internal-web-publishing.md) Publish a site or Godot export, start a preview, or forward a service.

<a id="certificate-trust"></a>
<a id="security-and-state"></a>
<a id="protected-upstream-ports"></a>

- [Internal web trust and security](internal-web-security.md) Handle certificate trust, protected ports, and gateway state.
