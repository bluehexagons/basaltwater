"""Certificate enrollment verifies pinned downloads before trust changes."""

from __future__ import annotations

import os
import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from unittest.mock import patch
from types import SimpleNamespace

from common import godot_web_steps

from common.service_tools.basaltwater_web import _certificate_trust
from common.service_tools.web_panel_service import (
    _render_certificate_trust, discover_certificate_trust,
)
from common.service_tools.web_panel_ca_install import (
    _linux_trust_script, _macos_trust_script, _certificate_scripts,
)
from common.service_tools.web_panel_ca_ui import PRESET_BUTTONS, SCRIPT, SCRIPT_CSP
from common.web_panel_steps import render_web_panel_nginx
from lib.config import SetupConfig, WEB_PANEL_CA_HTTP_PORT


class ScriptTemplates(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.scripts = {}
        self.browser_scripts = {}
        self.presets = []
        self.current = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "button" and "data-trust-preset" in attrs:
            self.presets.append(attrs["data-trust-preset"])
        if tag == "template":
            self.current = tuple(attrs[name] for name in ("data-platform", "data-method", "data-shell"))
            self.browser = attrs.get("data-browser", "system")
            self.scripts[self.current] = ""
            self.browser_scripts[(*self.current, self.browser)] = ""

    def handle_data(self, data):
        if self.current is not None:
            self.scripts[self.current] += data
            self.browser_scripts[(*self.current, self.browser)] += data

    def handle_endtag(self, tag):
        if tag == "template":
            self.current = None


class TestCertificateDownloadTrust(unittest.TestCase):
    def test_local_leaf_reuse_requires_current_ca_chain(self):
        for valid_chain in (True, False):
            with (
                self.subTest(valid_chain=valid_chain),
                patch.object(godot_web_steps, "_certificate_is_usable", return_value=True),
                patch.object(godot_web_steps, "run", return_value=SimpleNamespace(returncode=0 if valid_chain else 1)) as run,
                patch.object(godot_web_steps, "_managed_pair_is_partial", return_value=False),
                patch.object(godot_web_steps, "_ensure_managed_directory", side_effect=RuntimeError("renew leaf")) as issue,
            ):
                if valid_chain:
                    self.assertFalse(godot_web_steps._ensure_local_server_certificate(["example.test"]))
                    issue.assert_not_called()
                else:
                    with self.assertRaisesRegex(RuntimeError, "renew leaf"):
                        godot_web_steps._ensure_local_server_certificate(["example.test"])
                self.assertIn("openssl verify -purpose sslserver -no-CApath -no-CAstore -CAfile", run.call_args.args[0])

    def test_chromium_installers_enroll_distinct_roots_in_user_database(self):
        payloads = ("first CA", "second CA")
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                db = os.path.join(directory, ".pki/nssdb" if legacy else ".local/share/pki/nssdb")
                if legacy:
                    os.makedirs(db)
                for name, body in (
                    ("id", "echo 1000"),
                    ("certutil", '''printf '%s\\n' "$@" >> "$HOME/nss-args"
while [ "$#" -gt 0 ]; do
  if [ "$1" = -d ]; then shift; db=${1#sql:}; fi
  shift
done
touch "$db/cert9.db"'''),
                ):
                    path = os.path.join(directory, name)
                    with open(path, "w") as file_obj:
                        file_obj.write("#!/bin/sh\n" + body + "\n")
                    os.chmod(path, 0o700)
                installers = []
                hashes = [hashlib.sha256(payload.encode()).hexdigest() for payload in payloads]
                for payload, digest in zip(payloads, hashes):
                    script = ScriptTemplates(_certificate_scripts(digest, "https://example.test/ca")).browser_scripts[
                        "arch", "http", "posix", "chromium"]
                    installers.append("export TRUST_TEST_PAYLOAD='" + payload + "'\n" + script)
                result = self._run_installer(directory, "\n".join(installers), payloads[0])
                self.assertEqual(result.returncode, 0, result.stderr)
                with open(os.path.join(directory, "nss-args")) as file_obj:
                    args = file_obj.read()
                self.assertEqual(args.splitlines().count("-N"), 1)
                self.assertEqual(args.splitlines().count("-A"), 2)
                self.assertIn("sql:" + db, args)
                with open(os.path.join(directory, "installed")) as file_obj:
                    installed = file_obj.read()
                for digest in hashes:
                    self.assertIn("basaltwater CA " + digest, args)
                    self.assertIn("basaltwater-ca-" + digest + ".crt", installed)

    def test_browser_scripts_parse_and_firefox_explains_required_import(self):
        scripts = ScriptTemplates(_certificate_scripts("a" * 64, "https://example.test/ca")).browser_scripts
        for (platform, method, shell, browser), script in scripts.items():
            if shell == "powershell":
                continue
            result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            if platform == "arch" and browser == "firefox":
                self.assertIn("Authorities > Import", script)

    def test_chromium_failure_stops_before_system_trust_and_cleans_download(self):
        payload = "public certificate fixture"
        script = ScriptTemplates(_certificate_scripts(hashlib.sha256(payload.encode()).hexdigest(),
            "https://example.test/ca")).browser_scripts["arch", "http", "posix", "chromium"]
        for root_user in (False, True):
            with self.subTest(root_user=root_user), tempfile.TemporaryDirectory() as directory:
                for name, body in (("id", "echo " + ("0" if root_user else "1000")), ("certutil", "exit 1")):
                    path = os.path.join(directory, name)
                    with open(path, "w") as file_obj:
                        file_obj.write("#!/bin/sh\n" + body + "\n")
                    os.chmod(path, 0o700)
                result = self._run_installer(directory, script, payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Certificate verified and installed", result.stdout)
                self.assertFalse(os.path.exists(os.path.join(directory, "installed")))
                self.assertFalse(any(name.startswith("tmp.") for name in os.listdir(directory)))

    def test_missing_ca_never_means_public_trust(self):
        with patch("common.service_tools.basaltwater_web.os.path.isfile") as exists:
            exists.side_effect = lambda path: path == "/tmp/server.crt"
            result = _certificate_trust({
                "certificate": "/tmp/server.crt",
                "ca_certificate": "/tmp/missing-ca.crt",
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("publicly_trusted", result)

    def test_self_signed_policy_without_ca_never_means_public_trust(self):
        with patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=True):
            result = _certificate_trust({
                "certificate": "/tmp/self-signed.crt",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")

    def test_public_trust_requires_live_tls_verification(self):
        with (
            patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=False),
            patch("common.service_tools.basaltwater_web._verify_live_tls") as verify,
        ):
            policy = {
                "certificate": "/etc/letsencrypt/live/example.test/fullchain.pem",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            }
            result = _certificate_trust(policy)
            verify.assert_called_once_with("example.test", 8443, None)
            verify.side_effect = OSError("endpoint unavailable")
            failed = _certificate_trust(policy)
        self.assertEqual(result["status"], "public")
        self.assertEqual(failed["status"], "unknown")

    def test_live_vm_ca_cannot_be_reported_as_public(self):
        with (
            patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=True),
            patch("common.service_tools.basaltwater_web._verify_live_tls") as verify,
        ):
            result = _certificate_trust({
                "certificate": "/etc/letsencrypt/live/example.test/fullchain.pem",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(verify.call_count, 2)

    def test_local_ca_is_offered_after_live_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            ca = os.path.join(temporary, "ca.crt")
            with open(ca, "wb") as file_obj:
                file_obj.write(b"test CA")
            with patch("common.service_tools.basaltwater_web._verify_live_tls") as verify:
                result = _certificate_trust({
                    "certificate": os.path.join(temporary, "server.crt"),
                    "ca_certificate": ca,
                    "base_url": "https://example.test:8443",
                })
            verify.assert_called_once_with("example.test", 8443, ca)
        self.assertEqual(result["status"], "local_ca")
        self.assertEqual(len(result["sha256"]), 64)

    def test_panel_shows_unknown_trust_without_installation_instructions(self):
        with (
            patch("common.service_tools.web_panel_service.shutil.which", return_value="/usr/bin/basaltwater-web"),
            patch("common.service_tools.web_panel_service._run_json", return_value={"status": "unknown"}),
        ):
            trust = discover_certificate_trust()
        rendered = _render_certificate_trust(trust)
        self.assertIn("could not be verified", rendered)
        self.assertNotIn("No certificate installation required", rendered)
        self.assertNotIn("sudo install", rendered)

    def test_panel_preserves_missing_ca_diagnostic_and_explains_vm_repair(self):
        with (
            patch("common.service_tools.web_panel_service.shutil.which", return_value="/usr/bin/basaltwater-web"),
            patch("common.service_tools.web_panel_service._run_json", return_value={
                "status": "unknown", "reason": "configured CA certificate is missing",
            }),
        ):
            trust = discover_certificate_trust()
        self.assertEqual(trust["reason"], "configured CA certificate is missing")
        rendered = _render_certificate_trust(trust)
        self.assertIn("configured CA certificate is missing", rendered)
        self.assertIn("Repair an older VM", rendered)
        self.assertIn("sudo basaltw refresh --dry-run", rendered)
        self.assertIn("without replacing its private key", rendered)
        self.assertNotIn("sudo install", rendered)
        self.assertNotIn("trust-download", rendered)

    def test_unknown_diagnostic_is_bounded_and_escaped(self):
        for reason in (None, 42, "", " \n ", '<script>alert("test")</script>' + "x" * 1000):
            with (
                self.subTest(reason=reason),
                patch("common.service_tools.web_panel_service.shutil.which", return_value="/usr/bin/basaltwater-web"),
                patch("common.service_tools.web_panel_service._run_json", return_value={"status": "unknown", "reason": reason}),
            ):
                trust = discover_certificate_trust()
                rendered = _render_certificate_trust(trust)
                self.assertLessEqual(len(trust.get("reason", "")), 512)
                self.assertNotIn('<script>alert("test")</script>', rendered)
                self.assertNotIn("Repair an older VM", rendered)
                self.assertNotIn("sudo install", rendered)
                if isinstance(reason, str) and reason.startswith("<script>"):
                    self.assertIn("&lt;script&gt;", rendered)

    def test_tls_failure_cannot_reach_installation(self):
        for script in (
            _linux_trust_script("a" * 64, "https://example.invalid/ca", "  sudo install"),
            _macos_trust_script("a" * 64, "https://example.invalid/ca"),
        ):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                for name, body in (
                    ("curl", "exit 60"),
                    ("sudo", "touch installed; exit 0"),
                ):
                    path = os.path.join(directory, name)
                    with open(path, "w") as file_obj:
                        file_obj.write("#!/bin/sh\n" + body + "\n")
                    os.chmod(path, 0o700)
                result = subprocess.run(
                    ["/bin/bash", "-c", script], cwd=directory,
                    env={"PATH": directory + ":/usr/bin:/bin", "TMPDIR": directory},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sorted(os.listdir(directory)), ["curl", "sudo"])

    def test_all_platforms_retain_tls_verification_and_explain_independent_trust(self):
        rendered = _render_certificate_trust({
            "url": "https://example.invalid/basaltwater-ca.crt", "sha256": "a" * 64,
        })
        for bypass in ("--insecure", "--no-check-certificate", "SkipCertificateCheck", "ServerCertificateValidationCallback"):
            self.assertNotIn(bypass, rendered)
        self.assertIn("independently obtained fingerprint", rendered)
        self.assertIn("already trusts", rendered)

    def test_http_enrollment_is_certificate_only_and_managed_by_panel_setup(self):
        plain = render_web_panel_nginx(["example.test"], 80)
        tls = render_web_panel_nginx(["example.test"], 443, cert_path="/cert", key_path="/key")
        self.assertNotIn(f"listen {WEB_PANEL_CA_HTTP_PORT};", plain)
        download = tls[tls.index("# Public certificate only"):tls.index("listen 443 ssl")]
        self.assertIn(f"listen [::]:{WEB_PANEL_CA_HTTP_PORT};", download)
        self.assertIn("location = /basaltwater-ca.crt", download)
        self.assertIn("alias /srv/basaltwater/web/basaltwater-ca.crt;", download)
        self.assertIn("location / { return 404; }", download)
        self.assertIn("limit_except GET { deny all; }", download)
        self.assertNotIn("proxy_pass", download)
        self.assertNotIn("private", download)
        config = SetupConfig("192.0.2.1", "agent", "server_lite", web_panel_port=443, enable_ssl=True)
        self.assertIn(WEB_PANEL_CA_HTTP_PORT, config.effective_web_ports())
        config.enable_ssl = False
        self.assertNotIn(WEB_PANEL_CA_HTTP_PORT, config.effective_web_ports())

    def test_panel_ports_preserve_profile_specific_tls_origins(self):
        for profile in ("server_proxmox", "server_lite", "server_dev", "server_web"):
            for tls in (False, True):
                with self.subTest(profile=profile, tls=tls):
                    config = SetupConfig("192.0.2.1", "root", profile, web_ports=[9444])
                    # Exercise port derivation independently of profile option
                    # validation, which has its own setup boundary tests.
                    config.web_panel_port = 8444
                    config.enable_ssl = tls
                    expected = [8444, 9444]
                    if tls:
                        expected.append(WEB_PANEL_CA_HTTP_PORT)
                        if profile != "server_proxmox":
                            expected.append(8443)
                    self.assertEqual(config.effective_web_ports(), sorted(expected))

    def test_proxmox_panel_preserves_explicit_web_ports(self):
        config = SetupConfig("192.0.2.1", "root", "server_proxmox", web_ports=[8443])
        config.web_panel_port = 8444
        config.enable_ssl = True
        self.assertEqual(config.effective_web_ports(), [WEB_PANEL_CA_HTTP_PORT, 8443, 8444])

    def test_http_certificate_port_conflicts_are_rejected(self):
        for fields in (
            {"web_panel_port": 8442}, {"privilege_broker_port": 8442},
            {"web_interfaces": ["t3code"], "agent_tools": ["codex"], "web_interface_port": 8442},
            {"device_pairing_providers": ["t3code"], "device_pairing_port": 8442},
            {"gogs": ["8442"]}, {"homebox_port": 8442},
            {"antistatic_server": "example.test:8442"},
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "8442.*reserved"):
                SetupConfig("192.0.2.1", "agent", "server_lite", enable_ssl=True,
                            **({"web_panel_port": 443} | fields))

    def test_all_generated_unix_scripts_parse_and_fish_is_explicit(self):
        scripts = ScriptTemplates(_certificate_scripts("a" * 64, "https://example.test:8443/basaltwater-ca.crt")).scripts
        self.assertEqual(len(scripts), 27)
        for (platform, method, shell), script in scripts.items():
            with self.subTest(platform=platform, method=method, shell=shell):
                if shell == "powershell":
                    self.assertIn("finally", script)
                    self.assertLess(script.index("Get-FileHash"), script.index("& certutil.exe"))
                    continue
                result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                if shell == "fish":
                    self.assertTrue(script.startswith("bash -c "))
                if method == "http":
                    self.assertIn("http://example.test:8442/basaltwater-ca.crt", script)
                if method == "file":
                    self.assertNotIn("curl ", script)
                    self.assertIn("cp ./basaltwater-ca.crt", script)

    def test_ipv6_http_download_uses_a_bracketed_host(self):
        rendered = _certificate_scripts("a" * 64, "https://[fd00::1]:8443/basaltwater-ca.crt")
        self.assertIn("http://[fd00::1]:8442/basaltwater-ca.crt", rendered)

    def _run_installer(self, directory, script, payload, *, download_exit=0, install_exit=0):
        # CachyOS keeps shasum in /usr/bin/core_perl, outside the fixture PATH.
        # Expose the real checksum tool without inheriting other user commands.
        shasum = shutil.which("shasum")
        if shasum:
            os.symlink(shasum, os.path.join(directory, "shasum"))
        for name, body in (
            ("curl", '''printf '%s\\n' "$@" > curl-args
while [ "$1" != --output ]; do shift; done
printf '%s' "$TRUST_TEST_PAYLOAD" > "$2"
exit "$TRUST_TEST_DOWNLOAD_EXIT"'''),
            ("sudo", '''printf '%s\\n' "$@" >> installed
exit "$TRUST_TEST_INSTALL_EXIT"'''),
        ):
            path = os.path.join(directory, name)
            with open(path, "w") as file_obj:
                file_obj.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o700)
        return subprocess.run(
            ["bash", "-c", script], cwd=directory, capture_output=True, text=True, timeout=10,
            env={"PATH": directory + ":/usr/bin:/bin", "TMPDIR": directory,
                 "HOME": directory,
                 "TRUST_TEST_PAYLOAD": payload, "TRUST_TEST_DOWNLOAD_EXIT": str(download_exit),
                 "TRUST_TEST_INSTALL_EXIT": str(install_exit)},
        )

    def test_verified_http_and_local_file_installers_preserve_files_and_clean_temporary_data(self):
        payload = "public certificate fixture\n"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        scripts = ScriptTemplates(_certificate_scripts(digest, "https://example.test:8443/basaltwater-ca.crt")).scripts
        for key, script in scripts.items():
            if key[2] == "powershell":
                continue
            for outcome in ("success", "wrong-hash", "download-failure", "install-failure"):
                if outcome == "download-failure" and key[1] == "file":
                    continue
                with self.subTest(key=key, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                    contents = "tampered certificate" if outcome == "wrong-hash" else payload
                    source = os.path.join(directory, "basaltwater-ca.crt")
                    with open(source, "w") as file_obj:
                        file_obj.write(contents)
                    result = self._run_installer(directory, script, contents,
                        download_exit=60 if outcome == "download-failure" else 0,
                        install_exit=1 if outcome == "install-failure" else 0)
                    self.assertEqual(result.returncode == 0, outcome == "success", result.stderr)
                    self.assertEqual(os.path.exists(os.path.join(directory, "installed")), outcome in {"success", "install-failure"})
                    with open(source) as file_obj:
                        self.assertEqual(file_obj.read(), contents)
                    self.assertFalse(any(name.startswith("tmp.") for name in os.listdir(directory)))

    @unittest.skipUnless(shutil.which("fish"), "fish is optional on the Debian test host")
    def test_fish_can_execute_the_copied_arch_script(self):
        payload = "public certificate fixture\n"
        script = ScriptTemplates(_certificate_scripts(hashlib.sha256(payload.encode()).hexdigest(),
            "https://example.test:8443/basaltwater-ca.crt")).scripts["arch", "http", "fish"]
        with tempfile.TemporaryDirectory() as directory:
            self._run_installer(directory, "true", payload)
            result = subprocess.run(["fish", "-c", script], cwd=directory, capture_output=True, text=True, timeout=10,
                env={"PATH": directory + ":/usr/bin:/bin", "TMPDIR": directory, "TRUST_TEST_PAYLOAD": payload,
                     "TRUST_TEST_DOWNLOAD_EXIT": "0", "TRUST_TEST_INSTALL_EXIT": "0"})
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(os.path.join(directory, "installed")) as file_obj:
                self.assertIn("/etc/ca-certificates/trust-source/anchors/", file_obj.read())

    def test_installer_enhancement_is_allowed_by_an_exact_script_hash(self):
        expected = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode() + "'"
        self.assertEqual(SCRIPT_CSP, expected)
        rendered = _render_certificate_trust({"url": "https://example.test:8443/basaltwater-ca.crt", "sha256": "a" * 64})
        self.assertIn(f"<script>{SCRIPT}</script>", rendered)
        self.assertIn("data-trust-fallback", rendered)
        self.assertIn("fish (CachyOS default)", rendered)
        self.assertNotIn("<summary>Download, verify", rendered)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for UI behavior checks")
    def test_picker_shell_switching_copy_and_clipboard_failure(self):
        scripts = ScriptTemplates(_certificate_scripts("a" * 64, "https://example.test:8443/basaltwater-ca.crt")).browser_scripts
        templates = [{"dataset": {"platform": key[0], "method": key[1], "shell": key[2], "browser": key[3]},
                      "content": {"textContent": script}} for key, script in scripts.items()]
        harness = r'''
const assert = require('node:assert/strict');
function element(value = '') {
  return {value, hidden: true, textContent: '', listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    focus() { this.focused = true; }, select() { this.selected = true; },
    get selectedOptions() { return [{textContent: this.value}]; }};
}
const names = ['platform','shell','browser','method','output','status','title','method-note','copy','controls','selected','fallback'];
const elements = Object.fromEntries(names.map(name => ['[data-trust-' + name + ']', element()]));
const platform = elements['[data-trust-platform]']; platform.value = 'debian';
const shell = elements['[data-trust-shell]']; shell.value = 'posix';
const method = elements['[data-trust-method]']; method.value = 'http';
const browser = elements['[data-trust-browser]']; browser.value = 'system';
const shellLabel = element(); shell.closest = () => shellLabel;
const details = {open: false};
const panel = {querySelector: name => elements[name], querySelectorAll: () => TEMPLATES, closest: () => details};
const presets = PRESETS.map(value => {
  const button = element(); button.dataset = {trustPreset: value}; return button;
});
const presetGroup = element();
globalThis.document = {querySelector: () => panel, querySelectorAll: name => name === '[data-trust-preset]' ? presets : [presetGroup]};
const listeners = {};
globalThis.window = {addEventListener: (name, callback) => listeners[name] = callback};
globalThis.location = {hash: '#trust'};
let clipboardText = '';
Object.defineProperty(globalThis, 'navigator', {value: {platform: 'Linux', clipboard: {
  writeText: async text => { clipboardText = text; }
}}, configurable: true});
UI_SCRIPT
(async () => {
  const output = elements['[data-trust-output]'];
  const status = elements['[data-trust-status]'];
  const copy = elements['[data-trust-copy]'];
  assert.equal(details.open, true);
  assert.equal(elements['[data-trust-controls]'].hidden, false);
  assert.equal(elements['[data-trust-fallback]'].hidden, true);
  platform.value = 'arch'; platform.listeners.change();
  assert.equal(shell.value, 'fish'); assert.equal(shellLabel.hidden, false);
  assert.ok(output.value.startsWith('bash -c '));
  assert.ok(output.value.includes('/etc/ca-certificates/trust-source/anchors/'));
  assert.ok(output.value.includes('http://example.test:8442/'));
  await copy.listeners.click(); assert.equal(clipboardText, output.value);
  assert.match(status.textContent, /copied/);
  method.value = 'https'; method.listeners.change();
  assert.ok(output.value.includes('https://example.test:8443/'));
  assert.ok(!output.value.includes('http://example.test:8442/'));
  shell.value = 'posix'; shell.listeners.change();
  assert.ok(output.value.startsWith('('));
  method.value = 'file'; method.listeners.change();
  assert.ok(output.value.includes('cp ./basaltwater-ca.crt'));
  assert.ok(!output.value.includes('curl '));
  presets[0].listeners.click();
  assert.equal(platform.value, 'arch'); assert.equal(shell.value, 'fish');
  assert.equal(method.value, 'file'); assert.equal(output.focused, true);
  assert.equal(presetGroup.hidden, false);
  assert.equal(browser.value, 'chromium'); assert.match(output.value, /certutil -A/);
  presets[1].listeners.click();
  assert.equal(browser.value, 'firefox'); assert.match(output.value, /Authorities > Import/);
  presets[2].listeners.click(); assert.equal(platform.value, 'windows'); assert.equal(shellLabel.hidden, true);
  presets[3].listeners.click(); assert.equal(platform.value, 'macos'); assert.equal(shellLabel.hidden, false);
  navigator.clipboard.writeText = async () => { throw Error('clipboard blocked'); };
  await copy.listeners.click(); assert.equal(output.selected, true);
  assert.match(status.textContent, /selected/);
  platform.value = 'windows'; platform.listeners.change();
  assert.equal(shellLabel.hidden, true);
  assert.ok(output.value.includes('certutil.exe -user -addstore'));
  platform.value = 'macos'; platform.listeners.change();
  assert.equal(shellLabel.hidden, false);
  assert.ok(output.value.includes('shasum -a 256'));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(["node", "-"], input=harness.replace("TEMPLATES", json.dumps(templates)).replace("PRESETS", json.dumps(ScriptTemplates(PRESET_BUTTONS).presets)).replace("UI_SCRIPT", SCRIPT),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
