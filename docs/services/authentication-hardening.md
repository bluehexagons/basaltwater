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

On VM and hardware profiles, setup reconciles the managed `pam_faillock`
profile and checks that the generated common auth and account stacks use it.
Setup fails if the lockout profile cannot be activated, so a matching settings
file alone is not reported as active protection.

Security setup package installs (UFW, Fail2ban, AppArmor, and auditd) use the
shared 300-second APT/dpkg lock wait, allowing normal overlap with distro APT
jobs. Lock-wait exhaustion or another install failure still stops that setup
step before configuration continues; setup does not replay the failed install.

Server and workstation plans activate Fail2ban after optional desktop/XRDP
installation and hardening, so its file-backed XRDP jail has real log sources
on a fresh machine. Firewall and SSH hardening still precede desktop setup.
Headless setups retain the SSH jail. Custom step plans must install and start
XRDP before running `configure_fail2ban` with RDP enabled.
Setup runs `fail2ban-client -t` before enabling or restarting the service.
Missing log sources or invalid jail configuration stop that step; a successful
systemd restart request alone does not establish that asynchronous jail loading
succeeded. Repair the reported configuration before rerunning setup.

UFW setup skips machines that cannot enforce firewall policy, including
unprivileged LXC, OCI and WSL. These targets need reachability controls on their
host or upstream network; skipped UFW setup does not establish source filtering.

The security monitor reads `ausearch` and SSH journal results as a stream, with
bounded audit and SSH record and summary sizes. It counts all fail2ban bans and
unbans in the interval, but includes at most 50 examples of each in a
notification. It scans one complete 15-minute interval per run when catching
up after an outage, then saves the next interval's cursor.
A source timeout, oversized record, or failed query leaves the cursor at the
start of that interval and reports a collection failure. A high-cardinality SSH
attack still contributes to the failure count even after the source breakdown
reaches its limit.
SSH failures require an explicit failed-authentication, invalid-user, PAM-failure
or excessive-attempt message. Successful, partial and postponed authentication
records and method references are excluded; an ordinary public-key login must
not create a failed-login alert.

Fail2ban collection includes the active log and consecutive numeric rotations,
including gzip archives, using the packaged rename/create rotation policy.
It counts each file identity once; identical records in separate files remain
separate events. A changed file set discards the attempt and retries once.
Unreadable, corrupt, incomplete or persistently changing logs retain the cursor
and report a collection failure. Collection is bounded to 64 files, 256 MiB of
decompressed data across both attempts, 64 KiB per line and a 30-second work
budget. Large histories or busy logs can require investigation rather than a
successful scan. Unsupported rotation names, compression and missing numeric
generations also fail visibly. Custom `copytruncate` policies are outside this
support boundary; a stable copied archive cannot reliably be distinguished from
separate legitimate records.

Before and after collection, the monitor probes the oldest retained global
system-journal entry and unfiltered audit event. It uses the same system journal
and configured audit logs as the event queries, including audit rotation.
Empty, unreadable or malformed retention evidence leaves the cursor in place;
an empty event query alone cannot establish coverage. Audit queries use a fixed
C locale and its date format, and distinguish genuine no-match results from
argument or file-read errors that also return exit status 1.

When a known source boundary is later than the cursor, the monitor reports the
unobserved prefix as a `source_retention_gap`, with an exclusive `until` time.
It still collects available events from every source over the original window,
then advances only that window's normal cursor after all queries and probes
succeed. A gap produces error status and a nonzero service result even though
this explicit policy advances the cursor. It never jumps the shared cursor to
one source's boundary or labels the missing interval clean. This also handles
the first observation of newly created logs without permanently stalling.

The cursor and latest contiguous gap per source are saved atomically in
`/opt/basaltwater/state/security_monitor_state.json` with mode `0600`. Gap
intervals are also logged locally and included in notifications; their state
survives failed delivery, restarts and later clean windows. These three bounded
records are not a complete gap archive. Review unobserved intervals against
archived logs and increase source retention when needed. Later readable windows
do not recover missing history. An oldest entry before the cursor also does
not prove continuous logging, absence of interior deletions, or kernel audit
health. Fail2ban's oldest timestamped record across the stable retained files
establishes its leading boundary, including routine records on quiet services.
Empty files or files without timestamped records leave that boundary unknown.
Native reader and rotation checks do not qualify continuous daemon operation,
custom rotation policies or power-loss recovery.

When source filters are configured, setup checks the active UFW rules after
reconciliation. It fails if SSH, RDP, or a managed web port still has an inbound
allow or limit rule from anywhere, or if UFW status cannot be verified. Inspect
`sudo ufw status numbered` and remove the broad rule before retrying setup.

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
