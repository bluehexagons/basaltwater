# Install and access Gogs

[Topic index](gogs.md)

## Choose a deployment mode

Hostname mode is the usual choice. The port in `--gogs DOMAIN:PORT` is the
public Gogs HTTPS port. Gogs listens on a separate Basaltwater-managed
loopback port and nginx serves the public URL:

```bash
basaltw setup server_web git.example.com deploy \
  --gogs git.example.com:3000 /var/lib/gogs \
  --ssl --ssl-email admin@example.com
```

The example is available at `https://git.example.com:3000/`; Gogs does not
claim port 443. With `--ssl`, Basaltwater obtains and renews a Let's Encrypt
certificate. Port 80 remains the ACME challenge and redirect listener. With
`--cloudflare`, the nginx-to-Gogs connection remains private and the tunnel
serves standard external HTTPS; Gogs does not open public HTTP/HTTPS firewall
ports, and without `--ssl` it creates only the HTTP origin listener.
When `--cloudflare` and `--ssl` are combined, Gogs skips origin ACME requests;
the explicit SSL option retains a self-signed direct listener on the selected
Gogs port. Choose `:443` plus the appropriate direct-access policy when LAN
clients need HTTPS alongside the tunnel, and trust the origin certificate on
those clients. The Cloudflare origin remains local HTTP.

For a lab or private-network service, omit the hostname. With no source rules,
Gogs stays on loopback and setup prints an SSH tunnel command:

```bash
basaltw setup server_web 192.168.1.10 deploy \
  --gogs :3000 /srv/gogs-data

ssh -L 3000:127.0.0.1:3000 deploy@192.168.1.10
```

To make HTTPS reachable directly on a private network, enable SSL and repeat
`--gogs-source` for the exact IPv4 hosts or networks that need access:

```bash
basaltw setup server_web 192.168.1.10 deploy \
  --gogs :3000 /srv/gogs-data \
  --ssl \
  --gogs-source 192.168.1.0/24 \
  --gogs-source 10.0.0.0/8
```

Because a private IP cannot use the normal public-domain certificate flow,
hostless `--ssl` creates a self-signed certificate with the target IP in its
subject alternative name and includes `127.0.0.1` when the listener is
loopback-only. Basaltwater revalidates the certificate identity, key pair, and
remaining lifetime on rerun and replaces it when fewer than 30 days remain.
Trust `/etc/nginx/ssl/192.168.1.10.crt` explicitly on each client, or use a
hostname with Let's Encrypt to avoid certificate warnings. The resulting URL
is `https://192.168.1.10:3000/`.
Remote setup retains the target address for certificates, Git clone URLs and
SSH URLs while running setup commands locally on the target. Saved target
configuration retains this address for refreshes. An explicit Gogs hostname
overrides it; tunnel-only mode continues to advertise the loopback web URL.

Source-restricted mode requires active UFW. Setup stops the existing Gogs
service, installs and verifies replacement source rules, removes obsolete
Basaltwater-managed rules, and only then writes the listener. It refuses
public IPv4 sources from either `--gogs-source` or the generic
`--access-source`/`--lan-access` policy, explicit Gogs IPv6 sources, and
unmanaged allow rules for the same port. Generic IPv6 sources remain available
to other managed services but do not expose Gogs. Plain HTTP sends credentials
and session cookies without encryption. Use it only over localhost or in a
controlled test environment. Hostname mode requires
`--ssl` or `--cloudflare`. Use `:PORT` for hostless mode rather than placing a
literal IP in the Gogs specification. Cloudflare requires a hostname.
Let's Encrypt hostname mode rejects source restrictions
because its HTTP-01 renewal listener on port 80 must remain publicly reachable.
Switching between hostless, hostname, direct TLS, and Cloudflare modes removes
obsolete managed firewall rules and Gogs nginx sites, including the former
port-443 listener.

The direct public HTTP/HTTPS port defaults to 3000, so `--gogs 3000` is also
valid. With `--cloudflare`, that value is the private backend port instead.
Port 80 is reserved whenever nginx, TLS, a hostname, or Cloudflare is involved;
a hostless plaintext direct listener may deliberately use it. The optional data
path must be absolute and defaults to `/var/lib/gogs`.
Use a dedicated data directory. Shared filesystem/account roots, operating-system
trees, and Basaltwater or Gogs runtime directories are rejected before ownership
changes, including when invoking target-side Gogs setup directly.
Paths are validated as literal values; whitespace is not trimmed, and control
characters are refused before target-side work.
The data directory can be on its own backing mount. Keep independent
filesystems and bind mounts outside its descendants, including repository and
LFS subdirectories. Setup refuses nested mounts before applying ownership
changes and checks mount identity again as it walks the tree.

## First login and Git access

Registration is disabled. Setup creates the initial administrator named by
the setup username. To choose its password without exposing it in shell
history, save a matching workspace credential before setup:

```bash
basaltw credentials set deploy --service gogs --destination git.example.com
```

The setup command may instead include `--credential deploy PASSWORD`, but
that exposes the value to shell history and potentially the process list. If
there is no matching credential, setup generates a 24-character random
password. Setup uses Gogs' installer over a private Unix socket before starting
the normal service. It sends the password in the form body and saves the intended
login in the root-only file `/opt/basaltwater/state/gogs_admin_credentials.json`
before submitting it. If setup is interrupted, the file can still contain the
initial password for an account that was committed. Rerun setup to verify the
account. If Gogs already has users, provision a different administrator through
Gogs before changing the setup username. A rerun preserves the existing
administrator's password. Use a unique, high-entropy value and enable MFA for
administrator accounts.

Setup also checks an existing `git` account's home before changing it. System or
shared roots and symlinks in the home, `.ssh`, or `authorized_keys` path are
refused, as are mounts at or below `.ssh`. Setup sets ownership on the home,
`.ssh`, and `authorized_keys`.
The account uses `/bin/sh` to execute Gogs' forced authorized-key command;
`git-shell` rejects that dispatcher. When the managed `remoteusers` group
exists, setup adds `git` to it so the shared SSH group restriction permits
Git connections. It preserves the global group restriction and locks the
account password on every rerun.

Reverse-proxied deployments, including hostless `--ssl`, throttle password and
MFA submission endpoints to five requests per minute per client with a small
burst. Nginx also emits a
privacy-preserving marker for failed current-API web authentication and all
HTTP Basic authentication; five failures within ten minutes produce a one-hour
Fail2ban source ban. This covers Git/LFS and API Basic Auth without throttling
successful high-volume transfers.
Both the current Gogs API sign-in paths and the login/MFA paths used by older
managed releases receive the request limit. Cloudflare deployments derive the
client address through Nginx's trusted real-IP handling. They honor the edge's
HTTPS header only from the tunnel's loopback peer; direct clients retain their
connection scheme and cannot choose another client's address for throttling.
Plain-HTTP hostless mode does
not pass through Nginx, so retain its private source restriction or use the
default SSH tunnel. Gogs session cookies are marked secure whenever the managed
external URL is HTTPS.

The web UI manages repository users and their SSH keys. Clone over HTTPS using
the configured public port, or over SSH through the `git` account:

```bash
git clone https://git.example.com:3000/team/project.git
git clone git@git.example.com:team/project.git
```

The SSH drop-in at `/etc/ssh/sshd_config.d/99-gogs-git-user.conf` permits
public-key Git operations for `git` while disabling passwords, forwarding, and
PTY allocation. Gogs writes forced commands into `authorized_keys` to prevent
shell access; enroll keys through Gogs rather than adding unrestricted keys
to that file. Keep TCP port 22 reachable when SSH cloning is needed.
