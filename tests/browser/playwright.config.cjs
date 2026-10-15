'use strict';

const path = require('node:path');
const modulePath = process.env.BASALTW_PLAYWRIGHT_MODULE;
const { defineConfig } = require(require(path.join(modulePath, 'package.json')).name === '@playwright/test' ? modulePath : path.join(modulePath, 'test'));

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'surfaces.spec.cjs',
  // One browser at a time bounds memory even on small or ballooned VMs and
  // keeps the single disposable desktop's session ownership deterministic.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45000,
  expect: { timeout: 10000 },
  outputDir: path.join(process.env.BASALTW_BROWSER_OUTPUT, 'artifacts'),
  reporter: [['line'], ['json', { outputFile: path.join(process.env.BASALTW_BROWSER_OUTPUT, 'results.json') }]],
  projects: ['chromium', 'firefox'].map(browserName => ({ name: browserName, use: { browserName } })),
});
