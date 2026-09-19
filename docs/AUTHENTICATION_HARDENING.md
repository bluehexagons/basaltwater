# Authentication and brute-force protection

Basaltwater combines network reachability controls, request throttling, and
temporary source bans for remotely reachable authentication surfaces. Keep
credentials unique and high-entropy: rate limits reduce online guessing but do
not make a reused or common password safe.

## Protected surfaces

| Surface | Reachability boundary | Online-guessing protection |
| --- | --- | --- |
| SSH and Git-over-SSH | UFW source policy when configured | UFW SSH connection limiting, Fail2ban `sshd`, key-only `git` account |
| XRDP | RDP source policy | PAM lockout and Fail2ban `xrdp` |
| Samba | Samba source policy | Fail2ban `samba` and per-share authentication |
| T3 Code pairing portal | Loopback or web-interface source policy | Nginx 5 requests/minute per client, broker 5 issuances/minute per client, and a one-hour Fail2ban ban after 5 failed Basic Auth attempts in 10 minutes |
| Gogs hostname login and MFA | TLS or Cloudflare ingress, optionally source-filtered | Nginx 5 submissions/minute per client and a one-hour Fail2ban ban after 5 failed current-API or HTTP Basic authentication attempts in 10 minutes |
| Antistatic admin | HTTPS or private Cloudflare origin only | Nginx 10 admin requests/minute per client and a one-hour Fail2ban ban after 5 failed Basic Auth attempts in 10 minutes |
| CI/CD webhook | Public HTTPS or Cloudflare ingress | HMAC verification and Nginx 10 requests/minute per client |
| Proxmox GUI and SSH | Proxmox management source policy | Not published by a general web allow rule; use `--lan-access`, `--access-source`, or `--proxmox-source` to limit management clients |

Gogs without a hostname remains loopback-only by default. Its optional direct
listener is accepted only with private `--gogs-source` or generic access
sources and active UFW; use the SSH-tunnel default when the private network is
not fully trusted. Antistatic admin cannot be enabled in hostless direct mode.

## Failure-only logs

Nginx writes dedicated authentication-failure logs for the T3 pairing portal,
Gogs, and Antistatic. Conditional logging records only the client IP,
timestamp, and the fixed `basaltwater-auth-failure` marker after a failed
credential check. It does not record usernames, request bodies, Authorization
headers, passwords, or pairing URLs. Fail2ban consumes those logs with this
policy:

```text
5 failures in 10 minutes -> 1 hour source ban
```

For Cloudflare tunnel deployments, the generated sites use the tunnel's
client-IP header for throttling and failure records. The corresponding origin
is not published directly by UFW, so an internet client cannot supply that
header outside the trusted tunnel path.

## Operational checks

After applying setup or patch, validate the active controls without submitting
real credentials:

```bash
sudo nginx -t
sudo fail2ban-client status
sudo fail2ban-client status basaltwater-device-pairing
sudo fail2ban-client status basaltwater-gogs
sudo fail2ban-client status basaltwater-antistatic
sudo ufw status numbered
```

Only jails for configured services are expected to exist. Repeated invalid
tests can ban the testing address; unban it with
`sudo fail2ban-client set JAIL unbanip ADDRESS` after confirming the event.
Use a different source address when verifying that a `429 Too Many Requests`
response appears after a burst limit.

The controls are per source address. They substantially constrain ordinary
online guessing, but a distributed attacker can use multiple addresses. Keep
public services behind TLS or Cloudflare, prefer source filters for
administrative interfaces, enable Gogs MFA, and rotate any credential that may
have been reused or exposed.
