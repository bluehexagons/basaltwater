'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync, spawn} = require('node:child_process');
const modulePath = process.env.BASALTW_PLAYWRIGHT_MODULE;
const {test: base, expect} = require(require(path.join(modulePath, 'package.json')).name === '@playwright/test' ? modulePath : path.join(modulePath, 'test'));
const fixture = JSON.parse(fs.readFileSync(process.env.BASALTW_BROWSER_FIXTURE, 'utf8'));
const origin = fixture.urls.panel;
const api = origin + '/api/panel/v1/';

const test = base.extend({
  scenario: [null, {option: true}],
  development: async ({scenario}, use) => {
    if (!scenario) { await use(null); return; }
    const server = spawn(process.env.BASALTW_BROWSER_PYTHON, [path.join(__dirname, '../../scripts/panel_dev.py'),
      '--port', '0', '--https', '--no-refresh', '--no-reload', '--scenario', scenario],
      {cwd: path.join(__dirname, '../..'), stdio: ['ignore', 'pipe', 'pipe']});
    let stdout = '', stderr = '', directory;
    server.stdout.on('data', chunk => { stdout += chunk; });
    server.stderr.on('data', chunk => { stderr += chunk; });
    const exited = new Promise(resolve => server.once('exit', resolve));
    try {
      await expect.poll(() => {
        directory = stdout.match(/Temporary state: ([^\r\n]+)/)?.[1];
        return !!directory && fs.existsSync(path.join(directory, 'ready.json'));
      }, {timeout: 30000}).toBe(true);
      await use({...JSON.parse(fs.readFileSync(path.join(directory, 'settings.json'))),
        ca: path.join(directory, 'certificates/ca.crt')});
    } finally {
      server.kill('SIGTERM');
      const stopped = await Promise.race([exited.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 10000))]);
      if (!stopped) server.kill('SIGKILL');
      expect(stopped, 'Development supervisor exits').toBe(true);
      if (directory) expect(fs.existsSync(directory), 'Disposable state removed').toBe(false);
      expect(stderr, 'No development worker failures').toBe('');
    }
  },
  context: async ({playwright, browserName, javaScriptEnabled, development}, use, info) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'basaltwater-status-profile-'));
    let context;
    try {
      const profile = path.join(directory, 'profile');
      const trust = browserName === 'firefox' ? profile : path.join(directory, '.pki/nssdb');
      fs.mkdirSync(trust, {recursive: true, mode: 0o700});
      execFileSync('certutil', ['-N', '--empty-password', '-d', `sql:${trust}`]);
      execFileSync('certutil', ['-A', '-d', `sql:${trust}`, '-n', 'Disposable fixture CA', '-t', 'C,,', '-i', fixture.ca]);
      if (development) execFileSync('certutil', ['-A', '-d', `sql:${trust}`, '-n', 'Disposable development CA', '-t', 'C,,', '-i', development.ca]);
      context = await playwright[browserName].launchPersistentContext(profile, {
        headless: true, env: {...process.env, HOME: directory}, chromiumSandbox: true,
        viewport: {width: 1440, height: 900}, javaScriptEnabled,
        httpCredentials: {username: development?.username || fixture.username, password: development?.password || fixture.password},
        ignoreHTTPSErrors: false, firefoxUserPrefs: {'security.enterprise_roots.enabled': false},
      });
      await context.tracing.start({screenshots: true, snapshots: true});
      await info.attach('browser-context', {body: JSON.stringify({engine: browserName,
        browser_version: context.browser().version(), playwright_version: require(path.join(modulePath, 'package.json')).version}), contentType: 'application/json'});
      await use(context);
    } finally {
      if (context) {
        if (info.status !== info.expectedStatus) {
          for (const [index, page] of context.pages().entries()) await page.screenshot({path: info.outputPath(`failure-${index}.png`), fullPage: true}).catch(() => {});
          await context.tracing.stop({path: info.outputPath('trace.zip')});
        } else await context.tracing.stop();
        await context.close();
      }
      fs.rmSync(directory, {recursive: true, force: true});
    }
  },
  errors: [async ({page}, use) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      // Deliberately failed API responses are asserted by the status tests.
      if (message.type() === 'error' && !/Failed to load resource|the server responded with a status of|NS_ERROR_NET/.test(message.text())) errors.push(message.text());
    });
    await use(errors);
    expect(errors, 'No script or CSP failures').toEqual([]);
  }, {auto: true}],
});

test.describe('status agent review', () => {
  test.use({scenario: 'full'});
  test('status summaries preserve an open follow-up review when activity changes', async ({page, development: dev}) => {
    await page.goto(dev.url + '/agents?view=tasks');
    const sample = page.getByRole('article', {name: 'Example inspection', exact: true});
    await sample.getByRole('button', {name: 'Run once', exact: true}).click();
    await expect.poll(async () => {
      await page.reload();
      return sample.getByRole('link', {name: 'View latest result', exact: true}).count();
    }).toBe(1);
    await sample.getByRole('link', {name: 'View latest result', exact: true}).click();
    await page.getByRole('link', {name: 'Review follow-up task', exact: true}).click();
    const url = page.url();
    const form = page.locator('.agent-form');
    const ticket = await form.locator('input[name="source_run_id"]').inputValue();
    const prompt = form.locator('textarea[name="prompt"]');
    await prompt.fill('Unsaved reviewed prompt');
    await prompt.focus();
    const activity = page.locator('[data-panel-resource="agents"]');
    await page.route(dev.url + '/api/panel/v1/agents', route => route.fulfill({json: observation('agents', {
      running: 1, queued: 2, saved: 3, paused: 0, latest: 'completed', started_at: Date.now() / 1000 - 15, scheduler_error: false,
    }, {interval_seconds: 5})}));
    const posts = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    // Refresh with the keyboard without changing focus in the editable form.
    await activity.locator('[data-panel-refresh]').evaluate(element => element.click());
    await expect(activity.locator('[data-panel-summary]')).toContainText('1 running · 2 queued');
    await expect(activity.locator('[data-panel-elapsed]')).toHaveText(/\d+s elapsed/);
    await expect(prompt).toHaveValue('Unsaved reviewed prompt');
    await expect(prompt).toBeFocused();
    await expect(form.locator('input[name="source_run_id"]')).toHaveValue(ticket);
    await expect(page.getByText(/Inspect changed runs or review requests again/)).toBeVisible();
    expect(page.url()).toBe(url);
    expect(posts).toEqual([]);
  });

  test('status web summaries retain their service links and fit narrow light and dark layouts', async ({page, development: dev}, info) => {
    await page.goto(dev.url);
    const services = page.locator('[data-panel-resource="web"]');
    const card = services.locator('[data-panel-service]').first();
    const id = await card.getAttribute('data-panel-service');
    const link = await card.getAttribute('href');
    await page.route(dev.url + '/api/panel/v1/web', route => route.fulfill({json: observation('web', {
      total: 1, counts: {ready: 0, attention: 0, unavailable: 1, unchecked: 0},
      services: [{id, status: 'unavailable', text: 'Not responding'}],
    })}));
    await services.locator('[data-panel-refresh]').click();
    await expect(services.locator('[data-web-summary]')).toContainText('1 need attention');
    await expect(card).toHaveAttribute('href', link);
    for (const colorScheme of ['light', 'dark']) for (const width of [1440, 390]) {
      await page.emulateMedia({colorScheme});
      await page.setViewportSize({width, height: 900});
      expect(await page.locator('html').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.screenshot({path: info.outputPath(`status-${colorScheme}-${width}.png`), fullPage: true});
    }
  });
});

test.describe('status Proxmox snapshot', () => {
  test.use({scenario: 'proxmox'});
  test('status inventory summaries preserve filters and the detailed page-load snapshot', async ({page, development: dev}) => {
    await page.goto(dev.url + '/?pve_q=build');
    const resource = page.locator('[data-panel-resource="proxmox"]');
    const details = await resource.locator('.proxmox-panel').innerHTML();
    await page.getByLabel('Search name or VMID', {exact: true}).fill('Unsaved filter');
    await page.getByLabel('Search name or VMID', {exact: true}).focus();
    await page.route(dev.url + '/api/panel/v1/proxmox', route => route.fulfill({json: observation('proxmox', {guests: 10, running: 7})}));
    await resource.locator('[data-panel-refresh]').evaluate(element => element.click());
    await expect(resource.locator('[data-panel-summary]')).toHaveText('7 running · 10 reported guests and templates');
    await expect(page.getByLabel('Search name or VMID', {exact: true})).toHaveValue('Unsaved filter');
    await expect(page.getByLabel('Search name or VMID', {exact: true})).toBeFocused();
    expect(await resource.locator('.proxmox-panel').innerHTML()).toBe(details);
    expect(page.url()).toContain('pve_q=build');
  });
});

const host = page => page.locator('[data-panel-resource="host"]');
async function controlled(page) {
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto(origin);
  await expect(host(page).locator('[data-update-controls]')).toBeVisible();
}
function observation(resource, data, overrides = {}) {
  return {schema: 1, resource, observed_at: new Date().toISOString(), freshness: 'fresh',
    interval_seconds: resource === 'storage' ? 5 : 30, stale_after_seconds: 90,
    warnings: [], data, revision: 'b'.repeat(64), ...overrides};
}
const memory = value => ({metrics: [{label: 'Memory', value, description: 'Synthetic host data', status: ''}]});

test('status polling updates values without navigation, focus or disclosure loss, and skips unchanged bodies', async ({page}) => {
  await controlled(page);
  const initial = JSON.parse(await host(page).getAttribute('data-panel-snapshot'));
  const sourceStamp = initial.observed_at;
  const requests = [];
  await page.route(api + 'host', async route => {
    requests.push(route.request().headers()['if-none-match']);
    if (requests.length === 1) return route.fulfill({json: observation('host', memory('83% used'), {observed_at: sourceStamp})});
    return route.fulfill({status: 304, headers: {'ETag': '"' + 'b'.repeat(64) + '"'}});
  });
  await page.locator('.trust-disclosure > summary').click();
  await page.locator('.trust-disclosure > summary').focus();
  const url = page.url();
  await page.clock.runFor(3001);
  await expect(host(page).locator('[data-panel-metric="Memory"] .metric-value')).toHaveText('83% used');
  await expect(host(page).locator('[data-panel-metric="Uptime"] .metric-value')).toHaveText('Unavailable');
  await expect(page.locator('.trust-disclosure')).toHaveAttribute('open', '');
  await expect(page.locator('.trust-disclosure > summary')).toBeFocused();
  await expect(host(page).locator('[data-panel-announcement]')).toContainText('status changed');
  const announcement = await host(page).locator('[data-panel-announcement]').textContent();
  await host(page).locator('[data-panel-refresh]').click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toBe('"' + 'b'.repeat(64) + '"');
  await expect(host(page).locator('[data-panel-announcement]')).toHaveText(announcement);
  expect(page.url()).toBe(url);
  await page.clock.runFor(15000);
  expect(requests).toHaveLength(2);
  await expect(host(page).locator('[data-source-time]')).toContainText('ago');
  await expect(host(page).locator('[data-source-time]')).toHaveAttribute('datetime', sourceStamp);
});

test('status polling pause persists per tab, manual reads work paused, hidden tabs suspend and resume', async ({page, context}) => {
  await controlled(page);
  let reads = 0;
  await page.route(api + 'host', route => { reads++; return route.fulfill({json: observation('host', memory('50% used'))}); });
  await host(page).locator('[data-panel-pause]').focus();
  await page.keyboard.press('Enter');
  await expect(host(page)).toHaveAttribute('data-transport', 'paused');
  await page.clock.runFor(120000);
  expect(reads).toBe(0);
  await host(page).locator('[data-panel-refresh]').click();
  await expect.poll(() => reads).toBe(1);
  await expect(host(page)).toHaveAttribute('data-transport', 'paused');
  await page.goto(origin + '/storage?view=projects');
  await expect(page.locator('[data-panel-pause]')).toHaveText('Resume updates');
  await page.goto(origin);
  await expect(host(page).locator('[data-panel-pause]')).toHaveText('Resume updates');
  await host(page).locator('[data-panel-pause]').click();
  await page.clock.runFor(1001);
  await expect.poll(() => reads).toBe(2);
  // Deterministic document visibility also exercises Firefox's headless path.
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange')); });
  await page.clock.runFor(300000);
  expect(reads).toBe(2);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange')); });
  await page.clock.runFor(1001);
  await expect.poll(() => reads).toBe(3);
  const other = await context.newPage();
  await other.goto(origin);
  await expect(host(other).locator('[data-panel-pause]')).toHaveText('Pause updates');
  await other.close();
  // A Back/Forward cache restoration keeps the old script closure, but must
  // honor pause changes made on another page in the same tab.
  await page.evaluate(() => {
    sessionStorage.setItem('basaltwater.panel.updates-paused', '1');
    window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
  });
  await expect(host(page)).toHaveAttribute('data-transport', 'paused');
  await expect(host(page).locator('[data-panel-pause]')).toHaveText('Resume updates');
  await page.clock.runFor(120000);
  expect(reads).toBe(3);
  await page.evaluate(() => {
    sessionStorage.setItem('basaltwater.panel.updates-paused', '0');
    window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
  });
  await expect(host(page).locator('[data-panel-pause]')).toHaveText('Pause updates');
  await page.clock.runFor(1001);
  await expect.poll(() => reads).toBe(4);
});

test('status polling bounds overlap and timeout, backs off on overload, honors retry hints and recovers', async ({page}) => {
  await controlled(page);
  let reads = 0, release;
  const held = new Promise(resolve => { release = resolve; });
  await page.route(api + 'host', async route => {
    reads++;
    if (reads === 1) { await held; return route.fulfill({json: observation('host', memory('60% used'))}).catch(() => {}); }
    if (reads === 2) return route.fulfill({status: 503, headers: {'Retry-After': '120'}});
    return route.fulfill({json: observation('host', memory('61% used'))});
  });
  await page.clock.runFor(3001);
  await expect.poll(() => reads).toBe(1);
  await host(page).locator('[data-panel-refresh]').click();
  expect(reads).toBe(1);
  await page.clock.runFor(30001);
  await expect(host(page)).toHaveAttribute('data-transport', 'connection-error');
  await expect(host(page).locator('[data-panel-metric="Memory"] .metric-value')).toHaveText('42% used');
  release();
  await host(page).locator('[data-panel-refresh]').click();
  await expect.poll(() => reads).toBe(2);
  await expect(host(page)).toHaveAttribute('data-transport', 'connection-error');
  await host(page).locator('[data-panel-refresh]').click();
  await page.clock.runFor(119000);
  expect(reads).toBe(2);
  await page.clock.runFor(30000);
  await expect.poll(() => reads).toBe(3);
  await expect(host(page).locator('[data-panel-metric="Memory"] .metric-value')).toHaveText('61% used');
  await expect(host(page)).toHaveAttribute('data-transport', 'current');
});

test('status polling separates missing, stale, future, partial and authorization state without erasing values', async ({page}) => {
  await controlled(page);
  // The page clock is paused; runner timestamps can advance into its future.
  const observed = await page.evaluate(() => Date.now());
  let next = observation('host', memory('70% used'), {observed_at: null, freshness: 'unknown'});
  let reads = 0;
  await page.route(api + 'host', route => { reads++; return next === null ? route.fulfill({status: 403}) : route.fulfill({json: next}); });
  async function update() {
    const before = reads;
    await host(page).locator('[data-panel-refresh]').click();
    await expect.poll(() => reads).toBe(before + 1);
    await expect(host(page)).not.toHaveAttribute('data-transport', 'updating');
  }
  await update();
  await expect(host(page)).toHaveAttribute('data-source-health', 'unknown');
  next = observation('host', memory('71% used'), {observed_at: new Date(observed - 1000000).toISOString(), freshness: 'stale'});
  await update();
  await expect(host(page)).toHaveAttribute('data-source-health', 'stale');
  next = observation('host', memory('72% used'), {observed_at: new Date(observed + 600000).toISOString()});
  await update();
  await expect(host(page)).toHaveAttribute('data-source-health', 'unknown');
  next = observation('host', memory('73% used'), {observed_at: new Date(observed).toISOString(), freshness: 'incomplete', warnings: ['Collection is incomplete.']});
  await update();
  await expect(host(page)).toHaveAttribute('data-source-health', 'incomplete');
  await expect(host(page).locator('[data-source-warnings]')).toHaveText('Collection is incomplete.');
  next = observation('host', {metrics: []}, {freshness: 'unavailable'});
  await update();
  await expect(host(page)).toHaveAttribute('data-source-health', 'unavailable');
  await expect(host(page).locator('[data-panel-metric="Memory"] .metric-value')).toHaveText('73% used');
  next = null;
  await update();
  await expect(host(page)).toHaveAttribute('data-transport', 'authorization-error');
  const stopped = reads;
  await host(page).locator('[data-panel-pause]').click();
  await host(page).locator('[data-panel-pause]').click();
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange')); });
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange')); });
  await expect(host(page)).toHaveAttribute('data-transport', 'authorization-error');
  await expect(host(page).locator('[data-panel-transport]')).toContainText('Sign in again');
  await page.clock.runFor(900000);
  expect(reads).toBe(stopped);
  next = observation('host', memory('74% used'));
  await update();
  await expect(host(page)).toHaveAttribute('data-transport', 'current');
});

test('status polling preserves scan forms and filters and updates only explicitly loaded service state', async ({page}) => {
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto(origin + '/storage?view=projects');
  const scan = page.locator('[data-panel-resource="storage"]');
  const started = new Date(Date.now() - 30000).toISOString();
  let scanData = {state: 'running', started_at: started, finished_at: null, report_generated_at: null};
  await page.route(api + 'storage', route => route.fulfill({json: observation('storage', scanData)}));
  await page.getByLabel('Scan depth').selectOption('extended');
  await page.getByLabel('Scan depth').focus();
  await page.clock.runFor(3001);
  await expect(scan.locator('[data-panel-summary]')).toHaveText('Diagnostic running');
  await expect(scan.locator('[data-panel-elapsed]')).toHaveText(/\d+s elapsed/);
  scanData = {...scanData, state: 'complete', finished_at: new Date().toISOString()};
  await scan.locator('[data-panel-refresh]').evaluate(element => element.click());
  await expect(scan.locator('[data-panel-summary]')).toHaveText('Diagnostic complete');
  await expect(scan.locator('[data-panel-elapsed]')).toBeHidden();
  await expect(page.getByLabel('Scan depth')).toHaveValue('extended');
  await expect(page.getByLabel('Scan depth')).toBeFocused();
  expect(page.url()).toContain('view=projects');
  await expect(scan.getByRole('link', {name: 'Load latest scan results'})).toHaveAttribute('href', '/storage?view=projects');
  let services = 0;
  page.on('request', request => { if (request.url() === api + 'services') services++; });
  await page.goto(origin + '/services');
  await page.clock.runFor(60000);
  expect(services).toBe(0);
  await page.getByRole('button', {name: 'Load local service status'}).click();
  await expect(page.locator('[data-panel-resource="services"] [data-update-controls]')).toBeVisible();
  await page.clock.runFor(3001);
  await expect.poll(() => services).toBe(1);
  await expect(page.locator('[data-panel-resource="services"]')).toHaveAttribute('data-transport', 'current');
  const localServices = page.locator('[data-panel-resource="services"]');
  await page.route(api + 'services', route => route.fulfill({json: observation('services', {metrics: [
    {label: 'Synthetic service', value: 'failed', description: 'Failed since page load'},
  ]})}));
  await localServices.locator('[data-panel-refresh]').click();
  await expect(localServices.locator('.metric-value')).toHaveText('failed');
  await expect(localServices.locator('[data-service-summary]')).toHaveText('1 records · 1 inactive or unavailable · cached up to 30 seconds');
  await page.route(api + 'services', route => route.fulfill({json: observation('services', {metrics: []})}));
  await localServices.locator('[data-panel-refresh]').click();
  await expect(localServices.locator('.metric-value')).toHaveText('Unavailable');
  await expect(localServices.locator('.metric-description')).toContainText('Not reported in the latest snapshot');
  await expect(localServices.locator('[data-service-summary]')).toHaveText('0 records · cached up to 30 seconds');
});

test.describe('status no JavaScript fallback', () => {
  test.use({javaScriptEnabled: false});
  test('status snapshots, navigation and explicit forms remain useful across layouts and themes', async ({page}) => {
    let requests = 0;
    page.on('request', request => { if (request.url().startsWith(api)) requests++; });
    await page.goto(origin);
    await expect(host(page).locator('[data-update-controls]')).toBeHidden();
    await expect(host(page).locator('[data-panel-metric="Memory"] .metric-value')).toHaveText('42% used');
    await expect(host(page).locator('[data-source-time]')).toContainText('Observed');
    for (const colorScheme of ['light', 'dark']) for (const width of [1440, 390]) {
      await page.emulateMedia({colorScheme});
      await page.setViewportSize({width, height: 900});
      expect(await page.locator('html').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    }
    await page.goto(origin + '/services');
    await page.getByRole('button', {name: 'Load local service status'}).click();
    await expect(page.getByText('Synthetic service', {exact: true})).toBeVisible();
    expect(requests).toBe(0);
  });
});
