'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync, spawn } = require('node:child_process');
const modulePath = process.env.BASALTW_PLAYWRIGHT_MODULE;
const { test: base, expect } = require(require(path.join(modulePath, 'package.json')).name === '@playwright/test' ? modulePath : path.join(modulePath, 'test'));
const fixture = JSON.parse(fs.readFileSync(process.env.BASALTW_BROWSER_FIXTURE, 'utf8'));
const urls = fixture.urls;

function trust(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  execFileSync('certutil', ['-N', '--empty-password', '-d', `sql:${directory}`]);
  execFileSync('certutil', ['-A', '-d', `sql:${directory}`, '-n', 'Disposable fixture CA', '-t', 'C,,', '-i', fixture.ca]);
}

const test = base.extend({
  context: async ({ playwright, browserName }, use, info) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'basaltwater-browser-profile-'));
    const profile = path.join(directory, 'profile');
    let context;
    try {
      trust(browserName === 'firefox' ? profile : path.join(directory, '.pki/nssdb'));
      const type = playwright[browserName];
      context = await type.launchPersistentContext(profile, {
        headless: process.env.BASALTW_BROWSER_HEADED !== '1', executablePath: type.executablePath(),
        chromiumSandbox: true,
        env: { ...process.env, HOME: directory },
        args: browserName === 'chromium' ? ['--disable-features=NativeNotifications'] : [],
        viewport: { width: 1440, height: 900 },
        httpCredentials: { username: fixture.username, password: fixture.password },
        extraHTTPHeaders: { 'X-Fixture-Engine': browserName },
        ignoreHTTPSErrors: false,
        firefoxUserPrefs: { 'security.enterprise_roots.enabled': false },
      });
      await context.tracing.start({ screenshots: true, snapshots: true });
      await info.attach('browser-context', { body: JSON.stringify({ engine: browserName,
        browser_version: context.browser().version(), playwright_version: require(path.join(process.env.BASALTW_PLAYWRIGHT_MODULE, 'package.json')).version,
        context: 'disposable persistent profile', network_origin: 'host', boundary: fixture.boundary }), contentType: 'application/json' });
      await use(context);
    } finally {
      try {
        if (context) {
          try {
            if (info.status !== info.expectedStatus) {
              for (const [index, page] of context.pages().entries())
                await page.screenshot({ path: info.outputPath(`failure-${index}.png`), fullPage: true }).catch(() => {});
            }
            if (info.status !== info.expectedStatus || process.env.BASALTW_BROWSER_TRACE === '1')
              await context.tracing.stop({ path: info.outputPath('trace.zip') });
            else await context.tracing.stop();
          } finally { await context.close(); }
        }
      } finally {
        fs.rmSync(directory, { recursive: true, force: true });
      }
    }
  },
  diagnostics: [async ({ page, context }, use, info) => {
    const allowed = new Set();
    const errors = [];
    const failures = [];
    const downloads = new Set();
    const optional = [];
    const authChallenges = new Set();
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
        errors.push(`Unexpected external request: ${url.origin}`);
        return route.abort();
      }
      return route.continue();
    });
    page.on('pageerror', error => errors.push(`Page error: ${error.message}`));
    page.on('console', message => {
      if (message.type() === 'error' && !allowed.has(message.location().url)) errors.push(`Console: ${message.text()}`);
    });
    page.on('requestfailed', request => {
      failures.push({ url: request.url(), error: request.failure()?.errorText });
    });
    page.on('download', download => downloads.add(download.url()));
    page.on('response', response => {
      if (response.status() === 401 && /^Basic /i.test(response.headers()['www-authenticate'] || '')) {
        authChallenges.add(response.url());
        return;
      }
      if (response.ok()) authChallenges.delete(response.url());
      if (response.status() >= 400 && !allowed.has(response.url())) errors.push(`HTTP ${response.status()}: ${response.url()}`);
    });
    await use({ allowed });
    for (const failure of failures) {
      // Browsers abort the document navigation when handing it to the
      // download manager. The download test independently verifies completion.
      if (new URL(failure.url).pathname === '/favicon.svg' && /ERR_ABORTED|NS_BINDING_ABORTED/.test(failure.error)) {
        optional.push(failure);
        continue;
      }
      if (!allowed.has(failure.url) && !(downloads.has(failure.url) && /ERR_ABORTED|NS_BINDING_ABORTED/.test(failure.error)))
        errors.push(`Failed request: ${failure.url} ${failure.error}`);
    }
    for (const url of authChallenges) {
      if (new URL(url).pathname === '/favicon.svg') optional.push({ url, issue: 'optional favicon authentication challenge' });
      else errors.push(`Unresolved authentication challenge: ${url}`);
    }
    await info.attach('optional-favicon-diagnostics', { body: JSON.stringify(optional), contentType: 'application/json' });
    await info.attach('required-request-and-console-errors', { body: JSON.stringify(errors), contentType: 'application/json' });
    expect(errors, 'No unexpected console errors or failed required requests').toEqual([]);
  }, { auto: true }],
});

async function postForm(page, destination, fields) {
  const url = new URL(destination, page.url()).href;
  const response = page.waitForResponse(response => response.url() === url && response.request().method() === 'POST');
  const navigation = page.waitForNavigation({ waitUntil: 'domcontentloaded' });
  await page.evaluate(({ destination, fields }) => {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = destination;
    for (const [name, value] of Object.entries(fields)) {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = name; input.value = value;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.requestSubmit();
  }, { destination, fields });
  const result = await response;
  await navigation;
  return { status: result.status() };
}

test('panel navigation, keyboard focus, layout, theme and disclosures', async ({ page }) => {
  await page.goto(urls.panel);
  await expect(page.getByRole('heading', { name: 'Browser fixture', exact: true })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
  await expect(page.getByRole('meter', { name: 'Memory used' })).toHaveAttribute('value', '42');
  expect((await page.locator('.host-overview').evaluate(element => getComputedStyle(element).gridTemplateColumns)).split(' ')).toHaveLength(3);
  const summary = page.locator('.trust-disclosure > summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.trust-disclosure')).toHaveAttribute('open', '');
  const backgrounds = [];
  for (const colorScheme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme });
    backgrounds.push(await page.locator('body').evaluate(element => getComputedStyle(element).backgroundColor));
    for (const width of [1440, 780, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${colorScheme}, ${width}px`).toBe(true);
      await expect(page.getByRole('heading', { name: 'Browser fixture', exact: true })).toBeVisible();
    }
  }
  expect(backgrounds[0]).not.toEqual(backgrounds[1]);
  await page.getByRole('navigation', { name: 'Panel sections' }).getByRole('link', { name: 'Local service status' }).click();
  await page.getByRole('button', { name: 'Load local service status' }).click();
  await expect(page.getByText('Synthetic service', { exact: true })).toBeVisible();
});

test('panel form redirect, forged CSRF rejection and CA download', async ({ page, diagnostics }, info) => {
  await page.goto(urls.panel);
  const before = await (await page.request.get(urls.panel + '/__fixture__/state')).json();
  const response = page.waitForResponse(response => response.url() === urls.panel + '/actions/t3-update');
  await page.getByRole('button', { name: 'Update to latest' }).click();
  expect((await response).status()).toBe(303);
  await expect(page).toHaveURL(urls.panel + '/');
  const after = await (await page.request.get(urls.panel + '/__fixture__/state')).json();
  expect(after.updates).toBe(before.updates + 1);
  diagnostics.allowed.add(urls.panel + '/actions/t3-update');
  expect((await postForm(page, '/actions/t3-update', { csrf: 'forged' })).status).toBe(403);
  await page.goto(urls.panel);
  await page.locator('.trust-disclosure > summary').click();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download VM CA certificate' }).click();
  const download = await downloadEvent;
  expect(await download.failure()).toBeNull();
  const filename = info.outputPath('downloaded-fixture-ca.crt');
  await download.saveAs(filename);
  expect(fs.readFileSync(filename).equals(fs.readFileSync(fixture.ca))).toBe(true);
  const digest = crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
  await expect(page.locator('.fingerprint')).toContainText(digest);
});

test('pairing Secure cookie, nonce reuse rejection and fragment redirect', async ({ page, context, diagnostics }) => {
  await page.goto(urls.pairing + '/devices');
  const nonce = await page.locator('input[name=nonce]').first().inputValue();
  const cookie = (await context.cookies(urls.pairing)).find(cookie => cookie.name === 'basaltwater_pairing_nonce');
  expect(cookie).toMatchObject({ value: nonce, httpOnly: true, secure: true, sameSite: 'Strict' });
  await page.getByRole('button', { name: 'Pair this browser', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Continue in T3 Code' })).toBeVisible();
  const link = await page.getByRole('link', { name: 'Pair this browser with T3 Code' }).getAttribute('href');
  await page.getByRole('link', { name: 'Pair this browser with T3 Code' }).click();
  await expect(page).toHaveURL(urls.pairing + '/pair#token=fixture-token');
  await expect(page.getByRole('heading', { name: 'Synthetic destination' })).toBeVisible();
  diagnostics.allowed.add(urls.pairing + '/pair/t3code');
  expect((await postForm(page, '/pair/t3code', { nonce, intent: 'current' })).status).toBe(403);
  expect(link).toBe(urls.pairing + '/pair#token=fixture-token');
  // The production open-browser page submits a real form automatically.
  await page.goto(urls.pairing);
  await expect(page).toHaveURL(urls.pairing + '/pair#token=fixture-token');
});

test('approval real form origin, forged token, decision and replay rejection', async ({ page, browserName, diagnostics }) => {
  const identifier = (browserName === 'chromium' ? 'a' : 'b').repeat(32);
  const url = urls.approval + '/requests/' + identifier;
  await page.goto(url);
  await expect(page.getByText('Synthetic browser test <untrusted>', { exact: false })).toBeVisible();
  const digest = await page.locator('input[name=digest]').inputValue();
  const csrf = await page.locator('input[name=csrf]').inputValue();
  diagnostics.allowed.add(url);
  expect((await postForm(page, url, { csrf: 'forged', digest, decision: 'approve' })).status).toBe(409);
  await page.goto(urls.panel + '/project/');
  expect((await postForm(page, url, { csrf, digest, decision: 'approve' })).status).toBe(409);
  await page.goto(url);
  const submitted = page.waitForResponse(response => response.url() === url && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Approve once', exact: true }).click();
  expect((await submitted).status()).toBe(303);
  await expect(page.getByText('This request cannot be approved again.', { exact: false })).toBeVisible();
  expect((await postForm(page, url, { csrf, digest, decision: 'approve' })).status).toBe(409);
});

test('approval denial and expiry reject subsequent decisions', async ({ page, browserName, diagnostics }) => {
  const denial = (browserName === 'chromium' ? 'c' : 'd').repeat(32);
  const expiry = (browserName === 'chromium' ? 'e' : 'f').repeat(32);
  for (const [identifier, decision] of [[denial, 'deny'], [expiry, 'expire']]) {
    const url = urls.approval + '/requests/' + identifier;
    await page.goto(url);
    const csrf = await page.locator('input[name=csrf]').inputValue();
    const digest = await page.locator('input[name=digest]').inputValue();
    if (decision === 'deny') {
      const response = page.waitForResponse(response => response.url() === url && response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Deny', exact: true }).click();
      expect((await response).status()).toBe(303);
      await expect(page.locator('dd').filter({ hasText: /^denied$/ })).toBeVisible();
    } else {
      // Change only this synthetic broker's clock boundary after rendering a
      // valid form. The production handler must reject its now-expired request.
      expect((await page.request.post(urls.approval + '/__fixture__/expire/' + identifier)).status()).toBe(200);
    }
    diagnostics.allowed.add(url);
    expect((await postForm(page, url, { csrf, digest, decision: 'approve' })).status).toBe(409);
    await page.goto(url);
    await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toHaveCount(0);
    await expect(page.locator('dd').filter({ hasText: new RegExp(`^${decision === 'deny' ? 'denied' : 'expired'}$`) })).toBeVisible();
  }
});

test('authentication and invalid HTTPS remain rejected', async ({ page, playwright, diagnostics }) => {
  // Python 3.13 and other strict clients require CA/leaf key-usage extensions.
  execFileSync('openssl', ['verify', '-x509_strict', '-CAfile', fixture.ca,
    path.join(path.dirname(fixture.ca), 'server.crt')]);
  const anonymous = await playwright.request.newContext({ ignoreHTTPSErrors: false });
  try {
    for (const origin of [urls.panel, urls.pairing, urls.approval]) expect((await anonymous.get(origin + '/')).status()).toBe(401);
  } finally { await anonymous.dispose(); }
  diagnostics.allowed.add(urls.invalid + '/');
  await expect(page.goto(urls.invalid + '/')).rejects.toThrow(/CERT|SSL|certificate|SEC_ERROR/i);
});

test('pairing and approval narrow layout with enlarged text', async ({ page, browserName }) => {
  const identifier = (browserName === 'chromium' ? '1' : '2').repeat(32);
  for (const url of [urls.pairing + '/devices', urls.approval + '/requests/' + identifier]) {
    await page.goto(url);
    await page.setViewportSize({ width: 390, height: 900 });
    await page.addStyleTag({ content: 'html { font-size: 200%; }' });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), url).toBe(true);
    if (url.startsWith(urls.approval)) await expect(page.getByRole('button', { name: 'Approve once', exact: true })).toBeVisible();
  }
});

test('Guacamole embed/session boundary with disposable protocol service', async ({ page, diagnostics }) => {
  test.skip(!urls.guacamole, 'Pinned Guacamole assets missing; integration coverage remains unqualified');
  await page.goto(urls.guacamole + '/desktop/');
  await page.getByRole('button', { name: 'Check desktop status' }).click();
  await expect(page.locator('#desktop-status')).toContainText('running');
  await page.getByLabel('Unix account password').fill('synthetic-no-real-login');
  await page.getByRole('button', { name: 'Connect to desktop', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Take control', exact: true })).toBeEnabled();
  expect(await page.locator('#password').inputValue()).toBe('');
  await expect(page.locator('#screen canvas').first()).toBeVisible();
  await expect.poll(() => page.locator('#screen canvas').first().evaluate(canvas =>
    Array.from(canvas.getContext('2d').getImageData(10, 10, 1, 1).data))).toEqual([64, 128, 192, 255]);
  await page.getByRole('button', { name: 'Take control', exact: true }).click();
  await expect(page.getByRole('button', { name: 'You have control' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Observe only', exact: true }).click();
  await expect(page.locator('#status')).toContainText('Observing only');
  await page.getByRole('button', { name: 'Disconnect browser session' }).click();
  await expect(page.getByRole('button', { name: 'Connect to desktop', exact: true })).toBeEnabled();
  await expect(page.locator('#screen')).toBeEmpty();
  diagnostics.allowed.add(urls.guacamole + '/desktop/session');
  const rejected = await page.evaluate(async () => (await fetch('/desktop/session', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csrf: 'forged', password: 'fixture' }) })).status);
  expect(rejected).toBe(403);
});

test('panel development forms, scenarios, reload, reset and cleanup', async ({ page }) => {
  test.setTimeout(90000);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'basaltwater-panel-watch-'));
  const watched = path.join(temporary, 'extra.css');
  fs.writeFileSync(watched, 'initial');
  const server = spawn(process.env.BASALTW_BROWSER_PYTHON,
    [path.join(__dirname, '../../scripts/panel_dev.py'), '--port', '0', '--watch', watched],
    { cwd: path.join(__dirname, '../..'), stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '';
  let stderr = '';
  let stateDirectory;
  server.stdout.on('data', chunk => { stdout += chunk; });
  server.stderr.on('data', chunk => { stderr += chunk; });
  const exited = new Promise(resolve => server.once('exit', resolve));
  try {
    await expect.poll(() => {
      stateDirectory = stdout.match(/Temporary state: ([^\r\n]+)/)?.[1];
      return stateDirectory && fs.existsSync(path.join(stateDirectory, 'ready.json'));
    }, { timeout: 30000, message: 'Development server starts' }).toBe(true);
    const settings = JSON.parse(fs.readFileSync(path.join(stateDirectory, 'settings.json')));
    const authorization = 'Basic ' + Buffer.from(`${settings.username}:${settings.password}`).toString('base64');
    // Use this disposable server's credentials without modifying the fixture login.
    await page.route(settings.url + '/**', route => route.continue({ headers: { ...route.request().headers(), authorization } }));
    const status = async () => (await page.request.get(settings.url + '/__dev__/status', { headers: { authorization } })).json();
    const generation = async () => (await status()).generation;
    const waitForRestart = async previous => {
      await expect.poll(generation, { timeout: 20000 }).not.toBe(previous);
    };
    await page.goto(settings.url);
    await page.goto(settings.url + '/agents');
    await page.getByRole('button', { name: 'Run now', exact: true }).first().click();
    await expect.poll(async () => {
      await page.reload();
      return page.getByText('Synthetic execution; no provider was contacted', { exact: true }).count();
    }).toBe(1);
    for (const route of ['/agents', '/agent-tools', '/agent-inbox', '/maintenance-agent', '/admin', '/publishing',
                         '/services?load=1', '/jobs?load=1', '/logs?load=1', '/credentials']) {
      const response = await page.goto(settings.url + route);
      expect(response.status(), route).toBe(200);
      await expect(page.locator('#panel-development')).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByLabel('Author name', { exact: true }).fill('Browser Developer');
    await page.getByLabel('Author email', { exact: true }).fill('browser@example.test');
    await page.getByRole('button', { name: 'Save commit identity' }).click();
    await expect(page.getByLabel('Author name', { exact: true })).toHaveValue('Browser Developer');
    let previous = await generation();
    fs.writeFileSync(watched, 'reload once');
    await waitForRestart(previous);
    await expect.poll(() => page.locator('#panel-development').getAttribute('data-generation')).toBe(await generation());
    await expect(page.getByLabel('Author name', { exact: true })).toHaveValue('Browser Developer');

    // A code reload preserves unsaved edits and offers a deliberate refresh.
    await page.getByLabel('Author name', { exact: true }).fill('Unsaved developer');
    previous = await generation();
    fs.writeFileSync(watched, 'reload twice');
    await waitForRestart(previous);
    await expect(page.locator('#panel-reload')).toBeVisible();
    await expect(page.getByLabel('Author name', { exact: true })).toHaveValue('Unsaved developer');
    await page.locator('#panel-reload').click();
    await expect(page.getByLabel('Author name', { exact: true })).toHaveValue('Browser Developer');

    const toolbar = page.locator('#panel-development');
    previous = await generation();
    await toolbar.getByRole('button', { name: 'Reset sample data' }).click();
    await waitForRestart(previous);
    await page.goto(settings.url + '/credentials');
    await expect(page.getByLabel('Author name', { exact: true })).toHaveValue('Development Operator');
    for (const scenario of ['minimal', 'empty', 'degraded']) {
      previous = await generation();
      await toolbar.getByLabel('Scenario').selectOption(scenario);
      await toolbar.getByRole('button', { name: 'Switch scenario' }).click();
      await waitForRestart(previous);
      await page.goto(settings.url);
      await expect(toolbar.getByLabel('Scenario')).toHaveValue(scenario);
      if (scenario === 'minimal') await expect(page.locator('.sidebar a[href="/agents"]')).toHaveCount(0);
      if (scenario === 'empty') {
        await page.goto(settings.url + '/agent-inbox');
        await expect(page.getByText('No unread messages.', { exact: true })).toBeVisible();
      }
      if (scenario === 'degraded') await expect(page.getByRole('meter', { name: 'Memory used' })).toHaveAttribute('value', '94');
    }
    expect(stderr, 'No development worker exceptions').toBe('');
    // Leave the page before shutdown so background reload polling stops cleanly.
    await page.goto(urls.panel);
  } finally {
    server.kill('SIGTERM');
    const stopped = await Promise.race([exited.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 15000))]);
    if (!stopped) server.kill('SIGKILL');
    fs.rmSync(temporary, { recursive: true, force: true });
    expect(stopped, 'Supervisor and worker stop gracefully').toBe(true);
    if (stateDirectory) expect(fs.existsSync(stateDirectory), 'Disposable state removed').toBe(false);
  }
});
