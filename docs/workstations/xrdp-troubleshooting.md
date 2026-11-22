# Troubleshoot an XRDP session

[Topic index](xrdp.md)

## Troubleshooting

### Check the service and configuration

```bash
sudo systemctl status xrdp xrdp-sesman --no-pager
sudo xrdp --version
sudo grep -A 5 '\[Xorg\]' /etc/xrdp/sesman.ini
sudo cat /etc/X11/Xwrapper.config
```

The Xwrapper output should contain `allowed_users=anybody` and
`needs_root_rights=no`. Confirm that the Xorg backend uses `xrdpdev`, not
Xvnc.

### Inspect logs

```bash
sudo journalctl -u xrdp -u xrdp-sesman -n 100 --no-pager
sudo tail -100 /var/log/xrdp.log
sudo tail -100 /var/log/xrdp-sesman.log
tail -100 ~/.xsession-errors
tail -100 ~/.local/share/xorg/Xorg.*.log
```

### Black screen or immediate disconnect

Check the Xwrapper settings, then confirm that the desktop and D-Bus startup
commands are installed. Look for permission errors in `xrdp-sesman.log` and
startup failures in `~/.xsession-errors`. A stale desktop autostart entry or
fixed XFCE display profile can also prevent a session from starting; rerun
`patch` to restore the managed desktop settings.

If the connection log reports that `ip` is not needed, reapply the current
configuration. The warning itself is harmless. An absolute Xorg `-config` path
in the local session entry prevents a non-root X server from starting.

If `xrdp-sesman.log` waits ten seconds and then reports `Unable to open display`
without creating an Xorg log, check whether AppArmor enforces the `Xorg`
profile. Reapply the current configuration to move per-session logs from the
denied home-directory path into `~/.local/share/xorg`.

### Check GPU acceleration

When glamor is selected, the Xorg log contains `rdpPreInit` lines showing the
render node, a DRM driver name, and `glamor init ok`. Check the decision and
the effective permissions with:

```bash
grep -E 'rdpPreInit:|glamor init|unsupported render node' \
  ~/.local/share/xorg/Xorg.*.log
ls -l /dev/dri/renderD* 2>/dev/null
id
```

`unsupported render node`, `open failed`, or an absent `/dev/dri/renderD*`
means that the managed software fallback is in use. For Proxmox, a
VirtIO-GPU recovery console alone is expected to produce this result; a
guest-configured VirGL/3D render node must also expose a supported driver and
usable permissions before glamor is enabled.

If Xorg logs `rdpClientConInit: g_tcp_local_bind failed` and then repeats
`g_sck_accept failed`, inspect the per-user socket directory under
`/run/xrdp/sockdir`. Reapply setup to configure
`SessionSockdirGroup=xrdp`; xorgxrdp must create `xrdp_display_<display>` in
the user-specific directory (for example, `/run/xrdp/sockdir/1000`).

### Freeze while resizing

Distribution-packaged Remmina clients are the compatibility target; an older
client comparison is diagnostic, not a deployment requirement. Record the
running FreeRDP version as well as Remmina's version.

XRDP 0.10.6.1 does not recognize `enable_gfx=false`; `max_bpp=32` permits
GFX/H.264 negotiation. `use_fastpath=both` and `new_cursors=true` match
upstream defaults. This configuration does not resolve the cached-pointer
disconnect described above.

To check the classic path, compare the same installed client with a 24-bit
color-depth profile, reconnect, and repeat dynamic resizing. XRDP rejects GFX
negotiation below 32 bpp. Verify the negotiated pipeline in the server log;
this client-specific check does not justify disabling GFX by default. Relevant
upstream sources are the
[XRDP negotiation code](https://github.com/neutrinolabs/xrdp/blob/v0.10.6.1/libxrdp/xrdp_sec.c)
and [FreeRDP cache recreation change](https://github.com/FreeRDP/FreeRDP/pull/13196).

Try a fixed client resolution first. If that works, resize more slowly and
test another RDP client. Confirm that the session uses `xrdpdev`, that
`xfsettingsd` is running, and that no stale XFCE display profile is overriding
RANDR. If the freeze persists, capture the XRDP, Xorg, and session logs while
reproducing it.

Use `basaltw desktop exec -- xterm` in a running session to isolate an
application problem. Do not bypass the managed startup wrapper or restart
sesman while applications are running.

### Verify a live session

```bash
ps aux | grep -E 'xrdp|Xorg|xfce4-session|dbus-daemon'
xrandr
```

An active session should include an Xorg process using `xrdpdev`,
`xrdp-sesman`, and the selected desktop session. When reporting a problem,
include the XRDP and xorgxrdp versions, client and desktop versions, the
relevant log excerpts, and whether reconnecting changes the result.
