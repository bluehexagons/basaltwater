"""CSP-pinned progressive enhancement for panel timestamps and status regions."""

from __future__ import annotations

import base64
import hashlib
import html
import json
from datetime import datetime, timezone
from typing import Any

SCRIPT = r'''(() => {
  'use strict';
  const registry = {host: 30, web: 60, proxmox: 60, audit: 300, agents: 30, storage: 60, services: 30};
  const key = 'basaltwater.panel.updates-paused';
  let paused = false;
  try { paused = sessionStorage.getItem(key) === '1'; } catch (_) {}
  const regions = [];
  const text = (root, selector, value) => {
    const element = root.querySelector(selector);
    if (element && element.textContent !== String(value)) element.textContent = value;
  };
  const clock = value => value === null || value === '' ? NaN
    : typeof value === 'number' || /^\d{1,12}(\.\d+)?$/.test(value) ? Number(value) * 1000 : Date.parse(value);
  const health = value => {
    const stamp = clock(value.observed_at);
    if (value.freshness === 'unavailable') return 'unavailable';
    if (!Number.isFinite(stamp) || stamp > Date.now()) return 'unknown';
    return Date.now() - stamp > value.stale_after_seconds * 1000 ? 'stale' : value.freshness;
  };
  const age = stamp => {
    if (!Number.isFinite(stamp) || stamp > Date.now()) return 'Observation time unknown';
    const seconds = Math.floor((Date.now() - stamp) / 1000);
    return `Updated ${seconds < 60 ? seconds + 's' : seconds < 3600 ? Math.floor(seconds / 60) + 'm' : Math.floor(seconds / 3600) + 'h'} ago`;
  };
  function valid(value, name) {
    if (!value || value.schema !== 1 || value.resource !== name || !/^[a-f0-9]{64}$/.test(value.revision)
        || !['fresh', 'stale', 'unknown', 'incomplete', 'unavailable'].includes(value.freshness)
        || !(value.observed_at === null || typeof value.observed_at === 'string')
        || !Number.isInteger(value.interval_seconds) || value.interval_seconds < 5 || value.interval_seconds > 300
        || !Number.isInteger(value.stale_after_seconds) || value.stale_after_seconds < 1 || value.stale_after_seconds > 900
        || !Array.isArray(value.warnings) || value.warnings.length > 4
        || value.warnings.some(item => typeof item !== 'string' || item.length > 300)
        || !value.data || typeof value.data !== 'object' || Array.isArray(value.data)) throw Error('Invalid status');
    if (['host', 'services'].includes(name) && (!Array.isArray(value.data.metrics) || value.data.metrics.length > 48
        || value.data.metrics.some(row => !row || ['label', 'value', 'description'].some(field =>
          typeof row[field] !== 'string' || row[field].length > 200)))) throw Error('Invalid metrics');
    return value;
  }
  function metrics(root, rows) {
    for (const element of root.querySelectorAll('[data-panel-metric]')) {
      const row = rows.find(item => item.label === element.dataset.panelMetric) || {
        value: 'Unavailable', description: 'Not reported in the latest snapshot. Reload to inspect the current list.',
      };
      text(element, '.metric-value', row.value);
      text(element, '.metric-description', row.description);
      const value = element.querySelector('.metric-value');
      const match = /^(\d{1,3})% used$/.exec(row.value);
      const percent = match && Number(match[1]) <= 100 ? Number(match[1]) : null;
      value.className = 'metric-value ' + (percent !== null && percent >= 80 || row.status === 'warning' || row.value === 'Reboot required'
        ? 'warning' : row.value === 'Unavailable' ? 'unavailable' : row.value === 'active' ? 'active' : row.value === 'failed' ? 'failed' : '');
      let meter = element.querySelector('meter');
      if (percent !== null && !meter) {
        meter = document.createElement('meter');
        meter.min = 0; meter.max = 100; meter.low = 80; meter.high = 95; meter.optimum = 0;
        meter.setAttribute('aria-label', row.label + ' used');
        value.after(meter);
      }
      if (meter) { meter.hidden = percent === null; if (percent !== null) meter.value = percent; }
    }
  }
  function apply(region, value) {
    const root = region.root, data = value.data;
    if (['host', 'services'].includes(region.name)) metrics(root, data.metrics);
    if (region.name === 'services') {
      const attention = data.metrics.filter(row => row.value !== 'active').length;
      text(root, '[data-service-summary]', `${data.metrics.length} records${attention ? ` · ${attention} inactive or unavailable` : ''} · cached up to 30 seconds`);
    }
    if (region.name === 'web') {
      const counts = data.counts;
      if (!counts || !Array.isArray(data.services) || data.services.length > 64) throw Error('Invalid web summary');
      const keys = ['total', 'ready', 'attention', 'unavailable', 'unchecked'];
      if (keys.some(key => !Number.isInteger(key === 'total' ? data.total : counts[key])
          || (key === 'total' ? data.total : counts[key]) < 0 || (key === 'total' ? data.total : counts[key]) > 64)
          || data.services.some(row => !row || !/^[a-f0-9]{24}$/.test(row.id)
            || !['ready', 'attention', 'unavailable', 'unchecked'].includes(row.status)
            || typeof row.text !== 'string' || row.text.length > 160)) throw Error('Invalid service status');
      text(root, '[data-web-summary]', `${data.total} services · ${counts.ready} responding · ${counts.attention + counts.unavailable} need attention · ${counts.unchecked} not checked`);
      for (const card of root.querySelectorAll('[data-panel-service]')) {
        const row = data.services.find(item => item.id === card.dataset.panelService);
        const element = card.querySelector('.card-status');
        if (element) { element.textContent = row ? row.text : 'No longer reported; reload service links';
          element.className = 'card-status ' + (row ? row.status : 'unchecked'); }
      }
    }
    if (region.name === 'proxmox') {
      if (!['guests', 'running'].every(key => data[key] === null || Number.isInteger(data[key]) && data[key] >= 0 && data[key] <= 512)) throw Error('Invalid inventory summary');
      text(root, '[data-panel-summary]', data.guests === null ? 'Guest inventory unavailable' : `${data.running} running · ${data.guests} reported guests and templates`);
    }
    if (region.name === 'audit') {
      if (typeof data.applicable !== 'boolean' || !(data.events === null || Number.isInteger(data.events) && data.events >= 0 && data.events <= 100)) throw Error('Invalid audit summary');
      text(root, '[data-panel-summary]', data.applicable === false ? 'Kernel audit not applicable' : data.events === null ? 'Audit collection unavailable' : `${data.events} events in the exported snapshot`);
    }
    if (region.name === 'agents') {
      if (!['running', 'queued', 'saved', 'paused'].every(key => Number.isInteger(data[key]) && data[key] >= 0 && data[key] <= 40)
          || !['running', 'completed', 'failed', 'cancelled', 'interrupted', 'not-run'].includes(data.latest)) throw Error('Invalid agent summary');
      text(root, '[data-panel-summary]', `${data.running} running · ${data.queued} queued · ${data.saved} saved · ${data.paused} auto-paused · latest ${data.latest}`
        + (data.scheduler_error ? ' · Scheduler needs attention' : ''));
    }
    if (region.name === 'storage') {
      if (!['idle', 'running', 'complete', 'partial', 'failed'].includes(data.state)) throw Error('Invalid scan status');
      const labels = {idle: 'Ready to inspect', running: 'Diagnostic running', complete: 'Diagnostic complete', partial: 'Partial estimate', failed: 'Diagnostic could not finish'};
      text(root, '[data-panel-summary]', labels[data.state]);
      const link = root.querySelector('[data-panel-results]');
      if (link) link.hidden = data.state === 'running' || data.state === 'idle';
    }
    const elapsed = root.querySelector('[data-panel-elapsed]');
    if (elapsed) { elapsed.dataset.startedAt = data.started_at ?? '';
      elapsed.hidden = !data.started_at || region.name === 'storage' && data.state !== 'running'; }
  }
  function announce(region, message) { text(region.root, '[data-panel-announcement]', message); }
  function renderAge(region) {
    if (!region.value) return;
    const value = region.value, stamp = clock(value.observed_at), source = health(value);
    text(region.root, '[data-source-time]', age(stamp));
    text(region.root, '[data-source-health]', source.charAt(0).toUpperCase() + source.slice(1));
    region.root.dataset.sourceHealth = source;
    const time = region.root.querySelector('[data-source-time]');
    time.title = value.observed_at || 'Observation time unknown';
    if (value.observed_at) time.dateTime = value.observed_at; else time.removeAttribute('datetime');
    text(region.root, '[data-source-warnings]', value.warnings.join(' '));
    const elapsed = region.root.querySelector('[data-panel-elapsed]');
    if (elapsed && !elapsed.hidden) {
      const start = clock(elapsed.dataset.startedAt);
      elapsed.textContent = Number.isFinite(start) && start <= Date.now()
        ? Math.floor((Date.now() - start) / 1000) + 's elapsed' : 'Start time unknown';
    }
    if (region.lastHealth && region.lastHealth !== source) announce(region, `${region.label}: source ${source}.`);
    region.lastHealth = source;
  }
  function transport(region, state, message) {
    region.root.dataset.transport = state;
    text(region.root, '[data-panel-transport]', message);
  }
  function authorizationError(region) {
    transport(region, 'authorization-error', 'Sign in again, then refresh status.');
  }
  function schedule(region, delay) {
    clearTimeout(region.timer);
    if (!paused && !document.hidden && !region.authorization) region.timer = setTimeout(() => refresh(region), delay);
  }
  async function refresh(region, manual = false) {
    if (document.hidden || region.inflight || (!manual && (paused || region.authorization))) return;
    if (Date.now() < region.retryAt) { schedule(region, region.retryAt - Date.now()); return; }
    region.authorization = false;
    region.inflight = true;
    region.cancelled = false;
    region.controller = new AbortController();
    const timeout = setTimeout(() => region.controller.abort(), 30000);
    transport(region, 'updating', 'Updating status…');
    let retry = 0;
    try {
      const response = await fetch('/api/panel/v1/' + region.name, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: region.controller.signal,
        headers: region.value ? {'If-None-Match': '"' + region.value.revision + '"'} : {},
      });
      if ([401, 403].includes(response.status)) {
        region.authorization = true;
        authorizationError(region);
        announce(region, `${region.label}: sign in again to update status.`);
        return;
      }
      if (!response.ok && response.status !== 304) {
        const hint = response.headers.get('Retry-After');
        if (hint) retry = /^\d+$/.test(hint) ? Number(hint) * 1000 : Math.max(0, Date.parse(hint) - Date.now());
        throw Error('Status delayed');
      }
      if (response.status !== 304) {
        if (!(response.headers.get('Content-Type') || '').startsWith('application/json')) throw Error('Invalid response');
        const body = await response.text();
        if (body.length > 24576) throw Error('Oversized response');
        const value = valid(JSON.parse(body), region.name);
        const changed = region.value && (JSON.stringify(value.data) !== JSON.stringify(region.value.data) || health(value) !== health(region.value));
        // An unavailable source has no new values to apply. Keep the last good
        // values visible, alongside the explicit source health and warning.
        if (value.freshness !== 'unavailable') apply(region, value);
        region.value = value;
        if (changed) announce(region, `${region.label}: status changed; source ${health(value)}.`);
      } else if (!region.value) throw Error('Missing snapshot');
      region.failures = 0;
      region.retryAt = 0;
      region.interval = region.value.interval_seconds * 1000;
      renderAge(region);
      transport(region, paused ? 'paused' : 'current', paused ? 'Updates paused' : 'Automatic updates on');
    } catch (_) {
      if (!region.cancelled) {
        region.failures += 1;
        region.retryAt = Date.now() + (Number.isFinite(retry) ? retry : 0);
        transport(region, 'connection-error', 'Status delayed · retrying; last values retained');
        if (region.failures === 1) announce(region, `${region.label}: status delayed; last values retained.`);
      }
    } finally {
      clearTimeout(timeout);
      region.inflight = false;
      region.controller = null;
      const delay = region.failures ? Math.max(region.retryAt - Date.now(), Math.min(300000, region.interval * 2 ** Math.min(region.failures, 5)) * (1 + Math.random() / 5)) : region.interval * (1 + Math.random() / 10);
      schedule(region, region.resume ? Math.random() * 1000 : delay);
      region.resume = false;
    }
  }
  function suspend() {
    for (const region of regions) {
      clearTimeout(region.timer);
      if (region.controller) { region.cancelled = true; region.controller.abort(); }
      if (region.authorization) authorizationError(region);
      else transport(region, paused ? 'paused' : 'suspended', paused ? 'Updates paused' : 'Updates suspended while hidden');
    }
  }
  function resume() {
    for (const region of regions) {
      if (region.authorization) authorizationError(region);
      else if (region.inflight) region.resume = true;
      else schedule(region, Math.random() * 1000);
    }
  }
  const logTimes = document.querySelectorAll('time[data-local-time]');
  if (logTimes.length) {
    const formatTime = new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
    });
    for (const time of logTimes) {
      const stamp = Date.parse(time.dateTime);
      if (Number.isFinite(stamp)) time.textContent = formatTime.format(stamp);
    }
  }
  for (const root of document.querySelectorAll('[data-panel-resource]')) {
    const name = root.dataset.panelResource;
    if (!Object.hasOwn(registry, name)) continue;
    const region = {root, name, label: root.dataset.panelLabel, interval: registry[name] * 1000,
      value: null, failures: 0, retryAt: 0, inflight: false, authorization: false};
    try { region.value = valid(JSON.parse(root.dataset.panelSnapshot), name); } catch (_) {}
    root.querySelector('[data-update-controls]').hidden = false;
    root.querySelector('[data-panel-refresh]').addEventListener('click', () => { clearTimeout(region.timer); refresh(region, true); });
    root.querySelector('[data-panel-pause]').addEventListener('click', () => {
      paused = !paused;
      try { sessionStorage.setItem(key, paused ? '1' : '0'); } catch (_) {}
      for (const item of regions) text(item.root, '[data-panel-pause]', paused ? 'Resume updates' : 'Pause updates');
      if (paused) suspend(); else resume();
    });
    regions.push(region);
    text(root, '[data-panel-pause]', paused ? 'Resume updates' : 'Pause updates');
    transport(region, paused ? 'paused' : 'current', paused ? 'Updates paused' : 'Automatic updates on');
    renderAge(region);
    schedule(region, 500 + Math.random() * 2500);
  }
  for (const access of document.querySelectorAll('[data-route-access]')) {
    const link = access.querySelector('[data-route-url]');
    const button = access.querySelector('[data-route-copy]');
    const status = access.querySelector('[data-route-copy-status]');
    button.hidden = false;
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(link.textContent);
        status.textContent = 'URL copied.';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(link);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        status.textContent = 'URL selected. Use your device’s Copy command.';
      }
    });
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) suspend(); else { for (const region of regions) renderAge(region); resume(); } });
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    // A restored document retains its old closure; another page in this tab
    // may have changed the session pause preference while it was away.
    try { paused = sessionStorage.getItem(key) === '1'; } catch (_) {}
    for (const region of regions) text(region.root, '[data-panel-pause]', paused ? 'Resume updates' : 'Pause updates');
    if (paused) suspend(); else resume();
  });
  setInterval(() => { if (!document.hidden) for (const region of regions) renderAge(region); }, 1000);
})();'''

SCRIPT_HASH = "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
SCRIPT_CSP = "'" + SCRIPT_HASH + "'"
SCRIPT_TAG = f'<script src="/assets/panel-refresh.js" integrity="{SCRIPT_HASH}" defer></script>'


def render_log_time(original: str) -> str:
    """Keep the source timestamp and mark only absolute times for localization."""
    escaped = html.escape(original)
    try:
        stamp = datetime.fromisoformat(original)
        if stamp.utcoffset() is not None:
            utc = stamp.astimezone(timezone.utc).isoformat()
            return f'<time data-local-time datetime="{utc}" title="{escaped}">{escaped}</time>'
    except (ValueError, OverflowError):
        pass
    return escaped


STYLE = """
.panel-status {display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;margin:10px 0 14px;
 padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);font-size:.8rem;color:var(--muted)}
.panel-status [data-source-health] {font-weight:750;color:var(--text)}
[data-source-health=stale] > .panel-status [data-source-health],
[data-source-health=incomplete] > .panel-status [data-source-health] {color:var(--warning)}
[data-source-health=unavailable] > .panel-status [data-source-health] {color:var(--bad)}
.panel-status button {min-height:44px;padding:8px 12px;font-size:.8rem}
.panel-status [data-update-controls] {display:flex;flex-wrap:wrap;gap:8px;margin-left:auto}
.panel-status [hidden],.panel-live [hidden] {display:none}
.panel-status [data-panel-pause] {background:var(--accent-soft);color:var(--accent)}
.panel-status [data-source-warnings]:not(:empty) {flex-basis:100%}
.panel-summary {margin:8px 0;color:var(--text);font-size:.85rem}
.panel-announcement {position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media(max-width:560px){.panel-status [data-update-controls]{margin-left:0;flex-basis:100%}.panel-status button{width:auto}}
"""


def region(resource: str, label: str, content: str, value: dict[str, Any] | None = None) -> str:
    """Retain authoritative HTML and add controls only when JS is available."""
    escaped = html.escape(json.dumps(value, separators=(",", ":")), quote=True)
    stamp = value.get("observed_at") if value else None
    health = str(value["freshness"]) if value else "unknown"
    time_html = (f'<time data-source-time datetime="{html.escape(stamp, quote=True)}" title="{html.escape(stamp, quote=True)}">Observed {html.escape(stamp)}</time>'
                 if stamp else '<time data-source-time>Observation time unknown</time>')
    warnings = " ".join(value.get("warnings", [])) if value else ""
    return f'''<div class="panel-live" data-panel-resource="{html.escape(resource, quote=True)}" data-panel-label="{html.escape(label, quote=True)}" data-panel-snapshot="{escaped}" data-source-health="{health}">
<div class="panel-status"><span data-source-health>{health.capitalize()}</span>{time_html}
<span data-panel-transport>Snapshot on page load</span><span data-update-controls hidden>
<button type="button" data-panel-refresh>Refresh status</button><button type="button" data-panel-pause>Pause updates</button></span>
<span data-source-warnings>{html.escape(warnings)}</span><span class="panel-announcement" data-panel-announcement aria-live="polite" aria-atomic="true"></span></div>{content}</div>'''


def state_region(state: Any, resource: str, label: str, content: str) -> str:
    from common.service_tools.web_panel_status import StatusBusy
    try:
        value = state.status.read(resource)
    except (StatusBusy, OSError, RuntimeError, ValueError):
        value = None
    if value and value["freshness"] != "unavailable":
        data = value["data"]
        summary = None
        if resource == "agents":
            summary = (f'{data["running"]} running · {data["queued"]} queued · {data["saved"]} saved · '
                       f'{data["paused"]} auto-paused · latest {data["latest"]}'
                       + (' · Scheduler needs attention' if data["scheduler_error"] else ''))
        elif resource == "proxmox":
            summary = (f'{data["running"]} running · {data["guests"]} reported guests and templates'
                       if data["guests"] is not None else 'Guest inventory unavailable')
        elif resource == "audit":
            summary = ('Kernel audit not applicable' if not data["applicable"] else
                       f'{data["events"]} events in the exported snapshot' if data["events"] is not None else
                       'Audit collection unavailable')
        if summary is not None:
            start = content.find('data-panel-summary>') + len('data-panel-summary>')
            end = content.find('</p>', start)
            if start >= len('data-panel-summary>') and end >= start:
                content = content[:start] + html.escape(summary) + content[end:]
    return region(resource, label, content, value)


def agent_document(state: Any, document: str) -> str:
    """Add only a safe activity summary alongside existing manual agent views."""
    if not state.features().prompts:
        return document
    content = state_region(state, "agents", "Agent activity",
        '<p class="panel-summary" data-panel-summary>Saved agent activity</p><span data-panel-elapsed hidden></span>'
        '<p class="filesystem-note">Only the activity summary updates. Inspect changed runs or review requests again before deciding; forms and reports remain as loaded.</p>')
    return (document.replace('</header>', '</header>' + content, 1)
            .replace('</head>', '<style>' + STYLE + '</style></head>', 1)
            .replace('</body>', SCRIPT_TAG + '</body>', 1))
