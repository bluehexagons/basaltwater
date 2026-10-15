"""Progressive enhancement for the public CA enrollment instructions."""

from __future__ import annotations

import base64
import hashlib


STYLE = """
.trust-picker { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px;
  padding: 14px; margin: 16px 0; border: 1px solid var(--line); border-radius: 10px; }
.trust-picker label { min-width: 0; font-weight: 650; font-size: .85rem; }
.trust-picker small { display: block; margin-top: 6px; color: var(--muted); font-weight: 400; }
.trust-script-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.trust-script-head h3 { margin: 0; font-size: 1rem; }
.trust-script-head button { min-height: 44px; }
.trust-script-output { display: block; width: 100%; height: 24rem; margin: 12px 0;
  padding: 14px; border: 1px solid var(--line); border-radius: 8px; resize: vertical;
  background: var(--bg); color: var(--text); font: .8rem/1.6 "DejaVu Sans Mono", monospace;
  white-space: pre; overflow: auto; }
.trust-copy-status { min-height: 1.5em; color: var(--muted); font-size: .85rem; margin: 0; }
.trust-next { padding: 12px 14px; border-left: 3px solid var(--sea); background: var(--sea-soft); }
[hidden] { display: none !important; }
@media (max-width: 650px) { .trust-picker { grid-template-columns: 1fr; gap: 12px; }
  .trust-script-head { align-items: stretch; flex-direction: column; }
  .trust-script-output { height: 22rem; } }
""".strip()

SCRIPT = r"""(() => {
  const panel = document.querySelector('[data-trust-installer]');
  if (!panel) return;
  const platform = panel.querySelector('[data-trust-platform]');
  const shell = panel.querySelector('[data-trust-shell]');
  const method = panel.querySelector('[data-trust-method]');
  const output = panel.querySelector('[data-trust-output]');
  const status = panel.querySelector('[data-trust-status]');
  const templates = [...panel.querySelectorAll('template[data-platform]')];
  const os = navigator.userAgentData?.platform || navigator.platform || '';
  if (/Mac/i.test(os)) platform.value = 'macos';
  if (/Win/i.test(os)) platform.value = 'windows';
  function update() {
    const windows = platform.value === 'windows';
    shell.closest('label').hidden = windows;
    const selected = templates.find(item => item.dataset.platform === platform.value &&
      item.dataset.method === method.value && item.dataset.shell === (windows ? 'powershell' : shell.value));
    output.value = selected.content.textContent;
    panel.querySelector('[data-trust-title]').textContent =
      platform.selectedOptions[0].textContent + ' · ' + (windows ? 'PowerShell' : shell.selectedOptions[0].textContent);
    panel.querySelector('[data-trust-method-note]').textContent = {
      http: 'First enrollment: HTTP downloads only the public certificate. The script verifies its SHA-256 before installation. Rerun updated server setup to enable port 8442; upstream firewalls must also allow it.',
      https: 'Use this when your terminal already trusts the HTTPS endpoint. Certificate errors stop the script.',
      file: 'Run from the folder containing basaltwater-ca.crt, obtained through SSH or another trusted transfer. No download is needed.'
    }[method.value];
    status.textContent = '';
  }
  platform.addEventListener('change', () => {
    shell.value = platform.value === 'arch' ? 'fish' : 'posix';
    update();
  });
  shell.addEventListener('change', update);
  method.addEventListener('change', update);
  panel.querySelector('[data-trust-copy]').addEventListener('click', async () => {
    const copied = output.value;
    try {
      await navigator.clipboard.writeText(copied);
      status.textContent = output.value === copied ? 'Script copied. Paste it into your local terminal.' : 'Selection changed; copy the current script again.';
    } catch {
      output.focus();
      output.select();
      status.textContent = 'Script selected. Use your device’s Copy command (Ctrl+C or ⌘C).';
    }
  });
  panel.querySelector('[data-trust-controls]').hidden = false;
  panel.querySelector('[data-trust-selected]').hidden = false;
  panel.querySelector('[data-trust-fallback]').hidden = true;
  update();
  function openLinkedHelp() {
    if (location.hash === '#trust') panel.closest('details').open = true;
  }
  window.addEventListener('hashchange', openLinkedHelp);
  openLinkedHelp();
})();""".strip()

SCRIPT_CSP = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode() + "'"
