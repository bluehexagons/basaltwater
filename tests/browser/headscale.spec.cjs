'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const modulePath = process.env.BASALTW_PLAYWRIGHT_MODULE;
const { test: base, expect } = require(require(path.join(modulePath, 'package.json')).name === '@playwright/test' ? modulePath : path.join(modulePath, 'test'));
const fixture = JSON.parse(fs.readFileSync(process.env.BASALTW_BROWSER_FIXTURE, 'utf8'));
const origin = fixture.urls.headscale;
const secret = 'synthetic_exact_AuthID_123456';

const test = base.extend({
  context: async ({ playwright, browserName }, use, info) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'basaltwater-headscale-profile-'));
    const profile = path.join(directory, 'profile');
    const trust = browserName === 'firefox' ? profile : path.join(directory, '.pki/nssdb');
    fs.mkdirSync(trust, { recursive: true, mode: 0o700 });
    execFileSync('certutil', ['-N', '--empty-password', '-d', `sql:${trust}`]);
    execFileSync('certutil', ['-A', '-d', `sql:${trust}`, '-n', 'Disposable fixture CA', '-t', 'C,,', '-i', fixture.ca]);
    let context;
    try {
      context = await playwright[browserName].launchPersistentContext(profile, {
        headless: process.env.BASALTW_BROWSER_HEADED !== '1', chromiumSandbox: true,
        env: { ...process.env, HOME: directory }, viewport: { width: 1440, height: 900 },
        httpCredentials: { username: fixture.username, password: fixture.password },
        ignoreHTTPSErrors: false, firefoxUserPrefs: { 'security.enterprise_roots.enabled': false },
      });
      await context.tracing.start({ screenshots: true, snapshots: true });
      await info.attach('browser-context', { body: JSON.stringify({ engine: browserName,
        browser_version: context.browser().version(), playwright_version: require(path.join(modulePath, 'package.json')).version,
        boundary: 'Synthetic admission and broker; production renderer/forms; host loopback' }), contentType: 'application/json' });
      const errors = [];
      await context.route('**/*', route => {
        if (!['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)) {
          errors.push(`External request: ${route.request().url()}`);
          return route.abort();
        }
        return route.continue();
      });
      context.on('page', page => {
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        page.on('requestfailed', request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
        page.on('response', response => { if (response.status() >= 400 && response.status() !== 401) errors.push(`HTTP ${response.status()} ${response.url()}`); });
        page.on('download', download => errors.push(`Unexpected download: ${download.url()}`));
      });
      await use(context);
      await info.attach('headscale-diagnostics', { body: JSON.stringify(errors), contentType: 'application/json' });
      expect(errors).toEqual([]);
    } finally {
      try {
        if (context) {
          if (info.status !== info.expectedStatus) {
            for (const [index, page] of context.pages().entries())
              await page.screenshot({ path: info.outputPath(`failure-${index}.png`), fullPage: true }).catch(() => {});
          }
          await context.tracing.stop(info.status !== info.expectedStatus || process.env.BASALTW_BROWSER_TRACE === '1'
            ? { path: info.outputPath('trace.zip') } : {});
          await context.close();
        }
      } finally { fs.rmSync(directory, { recursive: true, force: true }); }
    }
  },
});

const form = (page, action) => page.locator('form').filter({ has: page.locator(`input[name="action"][value="${action}"]`) });
const state = async context => (await context.request.get(origin + '/__fixture__/headscale')).json();

test('Headscale inventory escapes native values and describes expiry and trust', async ({ page }) => {
  await page.goto(origin + '/headscale');
  await expect(page.getByText('alice <script>bad()</script>', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'laptop <img src=x onerror=bad()>' })).toBeVisible();
  expect(await page.locator('script, img[src="x"]').count()).toBe(0);
  for (const value of ['100.64.0.7', 'tag:sample', 'native-7', '2030-01-01T00:00:00Z'])
    await expect(page.getByText(value, { exact: true })).toBeVisible();
  await expect(page.getByText('Remove this person’s nodes before deleting their user.')).toBeVisible();
  await expect(form(page, 'users_delete')).toHaveCount(1);
  await expect(page.getByText(/Every change requires a separate privilege review/)).toBeVisible();
});

for (const [action, parameters] of [
  ['users_create', { name: 'browser-person' }], ['users_delete', { id: '13' }],
  ['node_expire', { id: '7' }], ['node_delete', { id: '7' }], ['register', { user: '12' }],
]) {
  test(`Headscale ${action} submits only a separate pending review`, async ({ page, context, browserName }) => {
    const secret = `synthetic_exact_AuthID_123456_${browserName}`;
    const before = await state(context);
    await page.goto(origin + '/headscale');
    const target = form(page, action);
    if (action === 'users_create') await target.getByLabel('Person name').fill(parameters.name);
    if (action === 'register') {
      await expect(target.getByLabel('Exact pending Auth ID')).toHaveAttribute('type', 'password');
      await target.getByLabel('Exact pending Auth ID').fill(secret);
      await target.getByRole('button').click();
      expect((await state(context)).requests.length).toBe(before.requests.length);
      await expect(target.getByRole('checkbox')).toHaveAttribute('required', '');
      await target.getByRole('checkbox').check();
    }
    await target.getByRole('button').click();
    await expect(page).toHaveURL(origin + '/admin?view=requests');
    const after = await state(context);
    expect(after.requests.length).toBe(before.requests.length + 1);
    expect(after.inventory).toEqual(before.inventory);
    const request = after.requests.at(-1);
    expect(request.state).toBe('pending');
    expect(request.plan.parameters).toMatchObject({ action, ...parameters });
    expect(JSON.stringify(after)).not.toContain(secret);
    expect(page.url()).not.toContain(secret);
    expect(await page.content()).not.toContain(secret);
    const review = await context.newPage();
    await review.goto(request.review_url);
    await expect(review.getByRole('status')).toContainText('Status: pending');
    await expect(review.getByRole('button', { name: 'Approve once' })).toBeVisible();
    expect(await review.content()).not.toContain(secret);
    expect(review.url()).not.toContain(secret);
    await review.reload();
    await expect(review.getByRole('status')).toContainText('Status: pending');
    await review.close();
  });
}

test('Headscale rejects forged forms and simulated unauthenticated or insecure admission', async ({ page, context, playwright }) => {
  await page.goto(origin + '/headscale');
  const csrf = await page.locator('input[name=csrf]').first().inputValue();
  const before = await state(context);
  for (const [body, status] of [
    [`csrf=bad&action=users_create&name=alice`, 403],
    [`csrf=${csrf}&action=users_create&name=alice&name=bob`, 400],
    [`csrf=${csrf}&action=unknown`, 409],
    [`csrf=${csrf}&action=register&user=12&auth_id=${secret}`, 409],
  ]) {
    const response = await context.request.post(origin + '/actions/headscale', {
      data: body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, maxRedirects: 0,
    });
    expect(response.status()).toBe(status);
    expect(await response.text()).not.toContain(secret);
  }
  for (const admission of ['anonymous', 'http']) {
    const headers = { 'X-Fixture-Admission': admission };
    expect((await context.request.get(origin + '/headscale', { headers })).status()).toBe(403);
    expect((await context.request.post(origin + '/actions/headscale', { headers, form: { csrf, action: 'node_delete', id: '7' } })).status()).toBe(403);
  }
  const anonymous = await playwright.request.newContext();
  try { expect((await anonymous.get(origin + '/headscale')).status()).toBe(401); }
  finally { await anonymous.dispose(); }
  expect(await state(context)).toEqual(before);
});

async function layout(page) {
  for (const [width, scale] of [[1440, 1], [320, 1], [320, 2]]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(scale => {
      if (scale === 1) return;
      const sizes = [...document.querySelectorAll('body, body *')].map(element =>
        [element, parseFloat(getComputedStyle(element).fontSize)]);
      for (const [element, size] of sizes) element.style.fontSize = `${size * scale}px`;
    }, scale);
    const overflow = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth,
      elements: [...document.querySelectorAll('body *')].filter(element => element.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 8).map(element => ({ tag: element.tagName, class: element.className, text: element.textContent.slice(0, 80) })) }));
    expect(overflow.width, JSON.stringify({ width, scale, ...overflow })).toBeLessThanOrEqual(width + 1);
    const first = page.locator('a, input:not([type=hidden]), select, button').first();
    await first.focus();
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(() => {
      const element = document.activeElement, style = getComputedStyle(element), box = element.getBoundingClientRect();
      return { interactive: element.matches('a,input,select,button'), outline: style.outlineStyle,
        width: parseFloat(style.outlineWidth), visible: box.width > 0 && box.left >= 0 && box.right <= innerWidth + 1 };
    });
    expect(focus.interactive && focus.visible && focus.outline !== 'none' && focus.width > 0).toBe(true);
  }
}

test('Headscale desktop and 320px with 200% text retain layout and keyboard focus', async ({ page }) => {
  await page.goto(origin + '/headscale');
  await layout(page);
});

test('Headscale static onboarding uses official instructions and local assets without issuing keys', async ({ page, context }) => {
  const before = await state(context);
  const responses = [];
  page.on('response', response => responses.push(response.url()));
  await page.goto(fixture.urls.onboarding + '/');
  for (const platform of ['Windows', 'macOS', 'Linux', 'iOS', 'Android'])
    await expect(page.getByRole('heading', { name: platform, exact: true })).toBeVisible();
  const links = await page.locator('a').evaluateAll(elements => elements.map(element => element.href));
  expect(links).toEqual([
    'https://tailscale.com/download/windows', 'https://tailscale.com/download/mac',
    'https://tailscale.com/docs/install/linux', 'https://tailscale.com/download/ios',
    'https://tailscale.com/download/android', 'https://tailscale.com/docs/features/logging', fixture.headscale.page_url,
  ]);
  expect(await page.locator('link[rel=stylesheet]').getAttribute('href')).toBe('/style.css');
  const image = page.getByRole('img', { name: 'QR code linking to this onboarding page' });
  await expect(image).toHaveAttribute('src', '/onboarding-qr.png');
  expect(await image.evaluate(element => element.complete && element.naturalWidth > 0)).toBe(true);
  expect(fixture.headscale.qr_payload).toBe(fixture.headscale.page_url);
  expect(fixture.headscale.qr_payload).not.toBe(fixture.headscale.control_url);
  expect(responses).toContain(fixture.urls.onboarding + '/style.css');
  expect(responses).toContain(fixture.urls.onboarding + '/onboarding-qr.png');
  await expect(page.getByText(/This page does not issue keys or approve devices/)).toBeVisible();
  expect(await page.locator('form').count()).toBe(0);
  await layout(page);
  expect(await state(context)).toEqual(before);
});
