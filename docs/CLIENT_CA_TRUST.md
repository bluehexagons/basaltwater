# Trust the infra-tools CA on a client

An infra-tools internal web gateway can use a VM-local certificate authority
(CA) when a publicly trusted certificate is not available for its private IP
address or internal hostname. The VM enrolls that CA for its own tools, but a
T3 Code preview or browser running on another device uses that device's trust
store.

Install the CA only on devices that should access this VM. A trusted root CA
can authenticate any HTTPS name, so confirm the source and fingerprint before
enrolling it and remove it when the VM is retired or no longer trusted.

Client enrollment is optional. If a T3 Code preview reports a certificate
error, use managed Playwright on the VM when it is installed, because setup
already enrolls the managed browser there. Otherwise continue server-side and
non-browser checks, skip the client-origin browser coverage, and report that
gap. Follow the enrollment steps below only when the user wants the client to
open the private HTTPS origin. Never use an insecure TLS bypass as a
workaround.

## Confirm that trust is the problem

On the VM, run:

```bash
infra-web ca
```

If the command says that the endpoint uses a publicly trusted certificate,
client enrollment is not required. Otherwise it prints three values:

```text
/srv/infra-tools/web/infra-tools-ca.crt
https://VM:8443/infra-tools-ca.crt
SHA-256 EXPECTED_FILE_SHA256
```

Use this process only for an explicit certificate-authority error such as
`ERR_CERT_AUTHORITY_INVALID`. A timeout, refused connection, DNS failure, or
unreachable private address is a routing or access-policy problem that CA
installation will not fix.

For a T3 Code collaborative preview, a failed navigation call does not
necessarily mean that the preview detached. If preview status still works,
inspect a snapshot and its network error. A `chrome-error://chromewebdata/`
document with `net::ERR_CERT_AUTHORITY_INVALID` is the certificate case.

## Obtain and verify the public certificate

The URL printed by `infra-web ca` is convenient after at least one client
already trusts the VM. A new client may block that download for the same reason
it blocks the application. In that case, copy the public certificate over the
existing SSH trust path instead of bypassing TLS:

```bash
scp USER@VM:/srv/infra-tools/web/infra-tools-ca.crt .
```

Another trusted transfer channel is also acceptable. Transfer only
`infra-tools-ca.crt`; never request or copy the CA private key.

The web panel's download scripts require a client that already trusts the
HTTPS endpoint. They retain TLS verification and stop on a certificate error;
they cannot bootstrap an untrusted connection. Obtain first-enrollment
instructions and the fingerprint through SSH or the VM console. Do not trust
a script or checksum from a page opened past a certificate warning.

Compare the downloaded file with the `SHA-256` value printed on the VM. On
Linux:

```bash
sha256sum infra-tools-ca.crt
```

On Windows PowerShell:

```powershell
(Get-FileHash .\infra-tools-ca.crt -Algorithm SHA256).Hash
```

On macOS:

```bash
shasum -a 256 infra-tools-ca.crt
```

Ignore letter case and spacing when comparing the hexadecimal values. On
Android or iPhone/iPad, use a trusted checksum utility or transfer the
already-verified file from a trusted computer. Do not install a CA whose
fingerprint you cannot verify through a path independent of the untrusted HTTPS
connection.

## Linux

The commands below install the CA system-wide. Run them from the directory
containing the verified `infra-tools-ca.crt` file.

### Debian, Ubuntu, and derivatives

```bash
sudo install -m 0644 infra-tools-ca.crt \
  /usr/local/share/ca-certificates/infra-tools-ca.crt
sudo update-ca-certificates
```

`update-ca-certificates` requires a PEM certificate with a `.crt` extension;
the file emitted by infra-tools has that format.

### Arch Linux, Manjaro, and derivatives

```bash
sudo trust anchor infra-tools-ca.crt
sudo update-ca-trust
```

If `trust anchor` reports that there is no writable location, use Arch's local
anchor directory explicitly:

```bash
sudo install -Dm0644 infra-tools-ca.crt \
  /etc/ca-certificates/trust-source/anchors/infra-tools-ca.crt
sudo update-ca-trust
```

### Fedora, RHEL, and derivatives

```bash
sudo install -m 0644 infra-tools-ca.crt \
  /etc/pki/ca-trust/source/anchors/infra-tools-ca.crt
sudo update-ca-trust extract
```

Fully exit and restart T3 Code or the browser after changing Linux trust.
Some sandboxed applications and browsers use a separate certificate store. If
normal system tools trust the URL but the application still reports an unknown
authority, import the same verified file through that application's
certificate settings as a trusted website authority. Do not import it as a
personal/client certificate, and do not guess or modify an application's
private profile database.

The platform procedures follow the Debian
[`update-ca-certificates`](https://manpages.debian.org/unstable/ca-certificates/update-ca-certificates.8.en.html),
Arch Linux [TLS trust-management](https://wiki.archlinux.org/title/Transport_Layer_Security#Trust_management),
and Red Hat [shared system certificate](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/securing_networks/using-shared-system-certificates)
guidance.

## macOS

Import the verified `infra-tools-ca.crt` into the **System** keychain with
Keychain Access. Double-click the imported certificate, expand **Trust**, and
set certificate trust to **Always Trust** only after confirming its SHA-256
fingerprint. Authenticate the change when macOS requests administrator
approval, then fully exit and reopen T3 Code or the browser.

Apple documents importing certificate files through Keychain Access and
[changing certificate trust settings](https://support.apple.com/guide/keychain-access/change-the-trust-settings-of-a-certificate-kyca11871/mac).

## Windows

For one Windows account, open PowerShell or Command Prompt as that user and
run:

```powershell
certutil -user -addstore -f Root .\infra-tools-ca.crt
```

For every account on a managed computer, an administrator can omit `-user` to
install into the local-machine `Root` store instead. Prefer the current-user
store unless machine-wide trust is intentional.

The graphical equivalent is:

1. Open the verified certificate file and select **Install Certificate**.
2. Select **Current User**. Select **Local Machine** only when an administrator
   intends to trust the CA for all users.
3. Choose **Place all certificates in the following store**, then select
   **Trusted Root Certification Authorities**.
4. Finish the import and acknowledge the trust warning only after checking the
   SHA-256 file fingerprint.

Fully exit and reopen T3 Code or the browser afterward. Microsoft documents
the [`certutil -addstore`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/certutil#-addstore)
command and the distinction between
[current-user and local-machine stores](https://learn.microsoft.com/en-us/windows/win32/seccrypto/system-store-locations).

## ChromeOS

On a personally managed Chromebook:

1. Transfer the verified `infra-tools-ca.crt` file to the Chromebook.
2. Open `chrome://certificate-manager` in Chrome.
3. Open **Authorities**, select **Import**, and choose the certificate.
4. Enable trust for identifying websites, then finish the import.
5. Fully close and reopen the T3 Code tab or browser window before retrying
   the infra-tools URL.

If certificate import controls are unavailable, check whether the Chromebook
is managed. A school administrator can disable user CA management, and users
should not try to evade that policy. For a managed school fleet, an authorized
Google Workspace administrator should instead:

1. Verify the certificate file against the SHA-256 value from `infra-web ca`.
2. In the Google Admin console, open **Devices > Networks > Certificates**.
3. Select the appropriate organizational unit, create a certificate, upload
   the single PEM/CRT file, and mark it as a CA for **Chromebook**.
4. Let policy synchronize, sign in with an account in the device's enrollment
   domain, and verify the CA at `chrome://settings` under **Privacy and
   security > Security > Advanced > Manage certificates**.

For educational deployments, distribute the CA only to the organizational
units that need the internal site, and remove it when that access is retired.
The client must also have a route to the VM's private address and be allowed by
the gateway's source policy; certificate deployment does not provide network
access. See Google's guidance for
[manual ChromeOS certificate import](https://support.google.com/chrome/a/answer/7014689#certificates)
and [managed ChromeOS CA deployment](https://support.google.com/chrome/a/answer/3505249).

## iPhone and iPad

Transfer the already-verified public certificate to the device through a
trusted channel and install the downloaded certificate profile. A manually
installed root is not automatically trusted for websites: open **Settings >
General > About > Certificate Trust Settings**, enable full trust for that
root, and confirm the warning only after checking the fingerprint through the
independent path above. Then fully close and reopen T3 Code or the browser.

On managed devices, prefer the administrator's approved Apple Configurator or
mobile-device-management policy rather than asking the user to override
organizational controls. Apple documents the additional
[manual root trust step](https://support.apple.com/en-ie/102390) for iOS and
iPadOS.

## Android

Android menu names vary by manufacturer and release. On current Pixel devices,
download or transfer the verified file, then open:

**Settings > Security & privacy > More security settings > Encryption &
credentials > Install a certificate > CA certificate**

Select `infra-tools-ca.crt`, confirm the device warning, and authenticate with
the device screen lock when prompted. Samsung and other devices commonly put
the same action under **Security and privacy > More security settings >
Install from device storage > CA certificate**.

Close and reopen T3 Code or the browser before retrying the URL. Android 7 and
newer do not make user-installed CAs available to every app by default; the app
must opt into user trust or provide its own CA configuration. If a normal
browser trusts the URL but a specific app or embedded preview does not, report
that as a client-app trust limitation. Do not root the device or disable
certificate validation to work around it. See Android's
[network security configuration](https://developer.android.com/privacy-and-security/security-config)
documentation for the application-side behavior.

## Verify and troubleshoot

After restarting the client, reopen the exact URL printed by `infra-web` and
confirm that it renders without a certificate warning. On Linux, macOS, or
Windows, this system-trust check must succeed without `-k` or another insecure
option:

```bash
curl https://VM:8443/
```

Then retry the T3 preview and confirm rendered content or a snapshot. If the
certificate error is gone but the client times out, continue with client/VM
route and gateway source-policy diagnosis. If `curl` succeeds but only one
application fails, use that application's documented certificate store or
report its trust limitation.

To remove trust later, delete the installed anchor and refresh the Linux trust
store, delete it from the macOS System keychain, remove it from **Trusted Root
Certification Authorities** on Windows, remove the imported authority or its
managed policy on ChromeOS, disable full trust and remove its profile on
iPhone/iPad, or remove it from **Trusted credentials > User** on Android.
Restart affected applications after removal.
