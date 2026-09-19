# Antistatic services

Basaltwater can deploy the Antistatic lobby server and antistatic-db on a
Debian web or lightweight server. Services run under systemd and can be
published through Nginx or directly on a host port.

## Lobby server behind Nginx and SSL

```bash
basaltw setup server_lite 192.168.1.10 \
  --antistatic-server lobby.example.com \
  --ssl --ssl-email admin@example.com
```

The release is downloaded from the official
`bluehexagons/antistatic-server` GitHub releases. Reports use bounded JSONL
storage under `/var/lib/antistatic`. The service has health checks and automatic
restart behavior.

Use `--cloudflare` when the hostname is published through a Cloudflare tunnel:

```bash
basaltw setup server_web 192.168.1.10 \
  --antistatic-server lobby.example.com \
  --cloudflare
```

Admin access requires a hostname and either `--ssl` or `--cloudflare`.
Plaintext admin requests are rejected.

## Custom internal port

The default internal port is 8080. Specify another port after the hostname:

```bash
basaltw setup server_web 192.168.1.10 \
  --antistatic-server lobby.example.com:9090 \
  --ssl --ssl-email admin@example.com
```

## Hostless direct mode

Use `:PORT` to listen directly without an Nginx virtual host:

```bash
basaltw setup server_lite 192.168.1.10 \
  --antistatic-server :8080
```

If UFW is active, setup opens the selected direct TCP port. HTTP tunnel
providers do not proxy UDP; the built-in STUN responder remains directly
reachable on UDP 3478.

## Report administration

Store the admin password interactively, then reference the username:

```bash
basaltw credentials set antistatic-admin
basaltw setup server_lite 192.168.1.10 \
  --antistatic-server lobby.example.com \
  --antistatic-admin antistatic-admin \
  --ssl --ssl-email admin@example.com
```

Disable the interface and remove its remote credential with:

```bash
basaltw setup server_lite 192.168.1.10 \
  --antistatic-server lobby.example.com \
  --no-antistatic-admin \
  --ssl --ssl-email admin@example.com
```

The credential is stored in the mode-0600 workspace store and installed in a
root-only environment file. Nginx limits `/admin` traffic to ten requests per
minute per client with a small burst. It conditionally records only failed
Basic Auth attempts, without the Authorization header; five failures within
ten minutes trigger a one-hour Fail2ban source ban. Check the active jail with
`sudo fail2ban-client status basaltwater-antistatic`.

## antistatic-db

Deploy the database service behind Nginx:

```bash
basaltw setup server_web 192.168.1.10 \
  --antistatic-db db.example.com \
  --ssl --ssl-email admin@example.com
```

Use `:8081` for direct hostless mode:

```bash
basaltw setup server_lite 192.168.1.10 \
  --antistatic-db :8081
```

## Related policy

Release selection honors the dependency freshness policy by default. See
[Recurring maintenance](MAINTENANCE.md) and the [Command-line reference](COMMAND_LINE.md).
