import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { runSession } from './adapter.mjs';

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'node-session-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const profile = join(root, 'private-profile');
  const evidence = join(root, 'evidence');
  return { root, profile, evidence, env: {
    ...process.env,
    BASALTWATER_TEST_PROFILE: profile,
    BASALTWATER_TEST_EVIDENCE: evidence,
    BASALTWATER_TEST_READY_FILE: join(profile, 'ready.json'),
    BASALTWATER_TEST_READY_TOKEN: 'synthetic-invocation-token',
  } };
}
const json = async (path) => JSON.parse(await readFile(path, 'utf8'));
const missing = async (path) => assert.rejects(readFile(path), { code: 'ENOENT' });

async function cleaned(f, cooperative = true) {
  const result = await json(join(f.evidence, 'adapter-result.json'));
  assert.equal(result.cleanupConfirmed, true);
  if (cooperative) assert.deepEqual(await json(join(f.evidence, 'worker-stopped.json')), { cooperative: true });
  await missing(f.env.BASALTWATER_TEST_READY_FILE);
  assert.ok(!(await readdir(f.profile)).some((name) => name.endsWith('.tmp')));
  return result;
}

test('live synthetic assertions precede atomic Token; private runtime and retained evidence survive independently', async (t) => {
  const f = await fixture(t);
  let observed = false;
  await runSession({ env: f.env, onReady: ({ pid, runtime, readyFile }) => {
    process.kill(pid, 0); // Publication is observed while the actual worker is alive.
    assert.equal(runtime, join(f.profile, 'runtime'));
    assert.equal(readyFile, f.env.BASALTWATER_TEST_READY_FILE);
    observed = true;
  }, lifetimeMs: 1000 });
  assert.equal(observed, true);
  assert.equal((await cleaned(f)).worker.code, 0);
  assert.deepEqual(await json(join(f.evidence, 'assertions.json')), { syntheticAssertionsPassed: true });
  assert.equal((await json(join(f.evidence, 'worker.json'))).profile, join(f.profile, 'runtime'));
  // A project reset of runtime cannot delete sibling control/evidence paths.
  // This only removes this test's owned temporary directory after child cleanup.
  await rm(join(f.profile, 'runtime'), { recursive: true });
  assert.equal((await json(join(f.evidence, 'worker.json'))).fixture, 'synthetic');
});

test('marker is complete JSON with exact Token, and cancellation cooperatively closes the live worker', async (t) => {
  const f = await fixture(t);
  const controller = new AbortController();
  let ready;
  const observed = new Promise((done) => { ready = done; });
  const session = runSession({ env: f.env, signal: controller.signal, onReady: ready });
  try {
    const { pid } = await Promise.race([observed, session.then(() => { throw new Error('Session ended before ready'); })]);
    process.kill(pid, 0);
    assert.deepEqual(await json(f.env.BASALTWATER_TEST_READY_FILE), { Token: f.env.BASALTWATER_TEST_READY_TOKEN });
    assert.deepEqual(await json(join(f.evidence, 'assertions.json')), { syntheticAssertionsPassed: true });
    assert.ok(!(await readdir(f.profile)).some((name) => name.endsWith('.tmp')));
    // A synthetic runtime reset while alive leaves the control marker and evidence intact.
    await rm(join(f.profile, 'runtime'), { recursive: true });
    assert.deepEqual(await json(f.env.BASALTWATER_TEST_READY_FILE), { Token: f.env.BASALTWATER_TEST_READY_TOKEN });
    assert.equal((await json(join(f.evidence, 'worker.json'))).fixture, 'synthetic');
  } finally {
    controller.abort();
    await session;
  }
  await cleaned(f);
});

for (const [mode, code] of [['exit7', 7], ['exit0', 1], ['fail-after-ready', 7]]) {
  test(`${mode}: worker exit is propagated and child cleanup confirmed`, async (t) => {
    const f = await fixture(t);
    await assert.rejects(runSession({ env: f.env, mode }), (error) => error.exitCode === code);
    const result = await cleaned(f, false);
    assert.equal(result.worker.code, mode === 'exit0' ? 0 : 7);
    assert.equal(result.failed, true);
    if (mode !== 'fail-after-ready') await missing(join(f.evidence, 'assertions.json'));
  });
}

for (const mode of ['wrong', 'absent']) {
  test(`${mode} assertion never publishes readiness and is cleaned up`, async (t) => {
    const f = await fixture(t);
    let ready = false;
    await assert.rejects(runSession({ env: f.env, mode, readyTimeoutMs: 500,
      onReady: () => { ready = true; } }), mode === 'absent' ? /deadline/ : /synthetic/);
    assert.equal(ready, false);
    await missing(join(f.evidence, 'assertions.json'));
    await cleaned(f);
  });
}

test('preload file URL supports spaces, Unicode and URL metacharacters on native Windows', async (t) => {
  const f = await fixture(t);
  const preloadPath = join(f.root, 'préload space # %.mjs');
  await copyFile(new URL('./preload.mjs', import.meta.url), preloadPath);
  const controller = new AbortController();
  await runSession({ env: f.env, preloadPath, signal: controller.signal, onReady: () => controller.abort() });
  assert.equal((await json(join(f.evidence, 'worker.json'))).preloaded, true);
  await cleaned(f);
});

test('native launch needs only explicit private profile/evidence, without Basaltwater readiness', async (t) => {
  const f = await fixture(t);
  delete f.env.BASALTWATER_TEST_READY_FILE;
  delete f.env.BASALTWATER_TEST_READY_TOKEN;
  const controller = new AbortController();
  await runSession({ env: f.env, signal: controller.signal, onReady: ({ readyFile }) => {
    assert.equal(readyFile, undefined);
    controller.abort();
  } });
  assert.equal((await json(join(f.evidence, 'adapter-result.json'))).cleanupConfirmed, true);
});

test('missing profile, overlapping evidence, runtime marker and stale marker fail before worker launch', async (t) => {
  const f = await fixture(t);
  for (const overrides of [
    { BASALTWATER_TEST_PROFILE: undefined },
    { BASALTWATER_TEST_EVIDENCE: join(f.profile, 'evidence') },
    { BASALTWATER_TEST_READY_FILE: join(f.profile, 'runtime', 'ready.json') },
    { BASALTWATER_TEST_READY_TOKEN: undefined },
  ]) await assert.rejects(runSession({ env: { ...f.env, ...overrides } }));
  await mkdir(f.profile);
  await writeFile(f.env.BASALTWATER_TEST_READY_FILE, '{"Token":"stale"}');
  await assert.rejects(runSession({ env: f.env }), /existing readiness/);
  await missing(join(f.profile, 'runtime', 'state.json'));
});

test('actual native CLI returns worker exit seven with retained result', async (t) => {
  const f = await fixture(t);
  const child = spawn(process.execPath, [fileURLToPath(new URL('./adapter.mjs', import.meta.url)),
    '--fixture-mode', 'exit7'], { env: f.env, stdio: 'ignore', windowsHide: true });
  const closed = new Promise((done, reject) => {
    child.once('error', reject);
    child.once('close', (code) => done(code));
  });
  // Adapter itself is bounded; an emergency harness timeout must also reap it.
  const timer = setTimeout(() => child.kill('SIGKILL'), 10_000);
  try { assert.equal(await closed, 7); } finally { clearTimeout(timer); }
  await cleaned(f, false);
});
