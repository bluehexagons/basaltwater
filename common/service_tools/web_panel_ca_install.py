"""Pinned client CA installers and browser-specific trust enrollment."""

from __future__ import annotations

import html
import shlex
import urllib.parse

from lib.config import WEB_PANEL_CA_HTTP_PORT
from lib.validation import validate_network_ip
from lib.validators import validate_host
from common.service_tools.web_panel_ca_ui import SCRIPT as CA_SCRIPT


def _linux_trust_script(
    fingerprint: str,
    download_url: str,
    install_commands: str,
    *,
    local_file: bool = False,
    macos: bool = False,
) -> str:
    protocol = "https" if urllib.parse.urlsplit(download_url).scheme == "https" else "http"
    download = '''  cp ./basaltwater-ca.crt "$temporary"''' if local_file else f'''  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --max-redirs 0 --proto '={protocol}' --show-error \\
      --connect-timeout 10 --max-time 60 --max-filesize 1048576 --output "$temporary" "$download_url"
  elif command -v wget >/dev/null 2>&1; then
    wget {'--https-only ' if protocol == 'https' else ''}--max-redirect=0 --timeout=60 --tries=1 --output-document="$temporary" "$download_url"
  else
    echo 'Install curl or wget, then run this script again.'
    exit 1
  fi'''
    checksum = "shasum -a 256" if macos else "sha256sum"
    return f"""(
  set -eu
  # Confirm this expected file hash independently over SSH or the host console.
  download_url={shlex.quote(download_url)}
  expected_sha256='{fingerprint}'
  temporary=$(mktemp)
  trap 'rm -f "$temporary"' EXIT
{download}
  actual_sha256=$({checksum} "$temporary")
  actual_sha256=${{actual_sha256%% *}}
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo 'SHA-256 mismatch; certificate was not installed.'
    exit 1
  fi
  certificate="$temporary"
{install_commands}
  echo 'Certificate verified and installed. Fully restart your browser.'
)"""


def _macos_trust_script(fingerprint: str, download_url: str, *, local_file: bool = False) -> str:
    return _linux_trust_script(
        fingerprint, download_url,
        '  sudo security add-trusted-cert -d -r trustRoot \\\n    -k /Library/Keychains/System.keychain "$certificate"',
        local_file=local_file, macos=True,
    )


def _windows_trust_script(fingerprint: str, download_url: str, *, local_file: bool = False) -> str:
    powershell_url = download_url.replace("'", "''")
    download = ('  Copy-Item -LiteralPath .\\basaltwater-ca.crt -Destination $Temporary -ErrorAction Stop'
                if local_file else '  Invoke-WebRequest -Uri $DownloadUrl -OutFile $Temporary -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 60 -ErrorAction Stop')
    return f'''& {{
$DownloadUrl = '{powershell_url}'
# Confirm this expected file hash independently over SSH or the host console.
$ExpectedSha256 = "{fingerprint}"
$Temporary = [System.IO.Path]::GetTempFileName()
try {{
{download}
  $ActualSha256 = (Get-FileHash -LiteralPath $Temporary -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
  if ($ActualSha256 -ne $ExpectedSha256) {{ throw "SHA-256 mismatch; certificate was not installed." }}
  & certutil.exe -user -addstore -f Root $Temporary
  if ($LASTEXITCODE -ne 0) {{ throw "certutil failed with exit code $LASTEXITCODE" }}
  Write-Host "Certificate verified and installed. Fully restart your browser."
}} finally {{
  Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
}}
}}'''


def _certificate_scripts(fingerprint: str, download_url: str) -> str:
    """Render reviewed scripts as inert templates, with a usable no-JS fallback."""
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise ValueError("Expected a lowercase SHA-256 file hash")
    parsed = urllib.parse.urlsplit(download_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Expected a valid HTTPS certificate download host")
    if not validate_host(parsed.hostname):
        validate_network_ip(parsed.hostname, "certificate download host")
    host = f"[{parsed.hostname}]" if ":" in str(parsed.hostname) else parsed.hostname
    http_url = f"http://{host}:{WEB_PANEL_CA_HTTP_PORT}/basaltwater-ca.crt"
    platforms = (
        ("debian", "Debian / Ubuntu", '  sudo install -Dm0644 "$certificate" /usr/local/share/ca-certificates/basaltwater-ca.crt\n  sudo update-ca-certificates'),
        ("arch", "Arch Linux / CachyOS", '  sudo install -Dm0644 "$certificate" /etc/ca-certificates/trust-source/anchors/basaltwater-ca.crt\n  sudo update-ca-trust extract'),
        ("fedora", "Fedora / RHEL", '  sudo install -Dm0644 "$certificate" /etc/pki/ca-trust/source/anchors/basaltwater-ca.crt\n  sudo update-ca-trust extract'),
        ("macos", "macOS", ""), ("windows", "Windows PowerShell", ""),
    )
    templates: list[str] = []
    fallback: list[str] = []
    for key, label, commands in platforms:
        # A client can enroll several independent VM roots without replacing one.
        commands = commands.replace("basaltwater-ca.crt", f"basaltwater-ca-{fingerprint}.crt")
        for method in ("http", "https", "file"):
            url = http_url if method == "http" else download_url
            local_file = method == "file"
            script = (
                _windows_trust_script(fingerprint, url, local_file=local_file) if key == "windows" else
                _macos_trust_script(fingerprint, url, local_file=local_file) if key == "macos" else
                _linux_trust_script(fingerprint, url, commands, local_file=local_file)
            )
            variants = {"powershell": script} if key == "windows" else {
                "posix": script,
                # Explicitly invoke Bash so fish never interprets POSIX syntax.
                "fish": "bash -c " + shlex.quote(script),
            }
            for shell in variants:
                for browser in ("chromium", "firefox", "system"):
                    browser_script = script
                    if key in {"debian", "arch", "fedora"}:
                        extra = _browser_commands(browser, fingerprint, commands)
                        browser_script = script.replace(commands, extra)
                    if shell == "fish":
                        browser_script = "bash -c " + shlex.quote(browser_script)
                    templates.append(f'<template data-platform="{key}" data-method="{method}" data-shell="{shell}" data-browser="{browser}">{html.escape(browser_script)}</template>')
            if method == "http":
                portable = script if key == "windows" else variants["fish"]
                fallback.append(f'<div class="platform-script"><h3>{label}</h3><pre><code>{html.escape(portable)}</code></pre></div>')
    return f'''<div data-trust-installer>
<div class="trust-picker" data-trust-controls hidden>
<label>Operating system<select data-trust-platform>
{''.join(f'<option value="{key}">{label}</option>' for key, label, _ in platforms)}
</select></label>
<label>Shell<select data-trust-shell><option value="posix">Bash / zsh</option><option value="fish">fish (CachyOS default)</option></select>
<small>fish examples run the installer in Bash.</small></label>
<label>Browser<select data-trust-browser><option value="system">System trust / other app</option>
<option value="chromium">Chrome / Chromium</option><option value="firefox">Firefox</option></select>
<small>Linux browsers may use a separate certificate store.</small></label>
<label>Certificate source<select data-trust-method>
<option value="http">First enrollment · HTTP + SHA-256</option>
<option value="https">Already trusted · HTTPS</option><option value="file">Local certificate file</option>
</select></label></div>
<div data-trust-selected hidden><div class="trust-script-head"><h3 data-trust-title></h3>
<button type="button" data-trust-copy>Copy script</button></div>
<p class="trust-intro" data-trust-method-note></p>
<textarea class="trust-script-output" aria-label="Certificate installation script" data-trust-output readonly spellcheck="false"></textarea>
<p class="trust-copy-status" role="status" data-trust-status></p></div>
<div data-trust-fallback><p>First enrollment scripts for your local computer. Linux and macOS examples work in Bash, zsh, and fish by invoking Bash explicitly.</p>{''.join(fallback)}</div>
{''.join(templates)}</div><script>{CA_SCRIPT}</script>'''


def _browser_commands(browser: str, fingerprint: str, system_commands: str) -> str:
    if browser == "firefox":
        anchor = system_commands.splitlines()[0].split()[-1]
        return system_commands + "\n  printf '%s\\n' " + shlex.quote(
            "Firefox on Linux: if the site still fails, open Settings > Privacy & Security > "
            "Certificates > View Certificates > Authorities > Import, select " + anchor +
            ", and enable trust for identifying websites. System installation alone may not enable Firefox trust."
        )
    if browser != "chromium":
        return system_commands
    return '''  if [ "$(id -u)" = 0 ]; then
    echo 'Run as the desktop browser user, without sudo; individual system commands request sudo.'
    exit 1
  fi
  if ! command -v certutil >/dev/null 2>&1; then
    echo 'Install NSS tools first: Arch/CachyOS: sudo pacman -S nss; Debian: sudo apt install libnss3-tools; Fedora: sudo dnf install nss-tools.'
    exit 1
  fi
  database_dir="$HOME/.local/share/pki/nssdb"
  if [ -d "$HOME/.pki/nssdb" ]; then database_dir="$HOME/.pki/nssdb"; fi
  mkdir -p "$database_dir"
  if [ ! -f "$database_dir/cert9.db" ]; then
    certutil -N --empty-password -d "sql:$database_dir"
  fi
  certutil -A -d "sql:$database_dir" -t 'C,,' \\
    -n "basaltwater CA ''' + fingerprint + '''" -i "$certificate"
''' + system_commands
