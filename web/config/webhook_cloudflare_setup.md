# Build webhooks over Cloudflare Tunnel

Use the shared [Cloudflare setup](../../docs/services/cloudflare.md) with
`--build-server --webhook-builds --webhook-domain builds.example.com --cloudflare`
and explicit repository allowlists. Setup provisions or refreshes the existing
shared tunnel and activates an HTTP origin without ACME. First-time account
authentication and DNS configuration are still required. LAN builders can use
this public route without direct inbound web ports.

Configure GitHub with `https://builds.example.com/basaltwater-builds/github`,
JSON, signed push events and TLS verification. See the
[webhook guide](../../docs/services/webhook-builds.md) for secrets and allowlists.
The old dedicated port-8080 webhook route is retired.
