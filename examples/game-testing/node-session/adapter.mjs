import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { lstat, mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const modes = new Set(['success', 'exit7', 'exit0', 'wrong', 'absent', 'fail-after-ready']);
const workerPath = fileURLToPath(new URL('./worker.mjs', import.meta.url));
const defaultPreload = fileURLToPath(new URL('./preload.mjs', import.meta.url));
const delay = (ms) => new Promise((done) => setTimeout(done, ms));

function inside(parent, child) {
  const tail = relative(parent, child);
  return tail === '' || (!isAbsolute(tail) && tail !== '..' && !tail.startsWith(`..${sep}`));
}

function absolute(value, name) {
  assert.ok(typeof value === 'string' && isAbsolute(value), `${name} must be an explicit absolute path`);
  return resolve(value);
}

function bound(value, name) {
  assert.ok(Number.isInteger(value) && value >= 50 && value <= 300_000, `${name} must be 50..300000 ms`);
}

export class SessionError extends Error {
  constructor(message, exitCode = 1) {
    super(message);
    this.exitCode = exitCode;
  }
}

// This is one project's adapter, not a command discovery system or tree supervisor.
export async function runSession({
  env = process.env, mode = 'success', preloadPath = defaultPreload,
  readyTimeoutMs = 3000, lifetimeMs = 30_000, cleanupMs = 1000,
  signal, onReady = () => {},
} = {}) {
  assert.ok(modes.has(mode), 'Unknown synthetic fixture mode');
  for (const [name, value] of Object.entries({ readyTimeoutMs, lifetimeMs, cleanupMs })) bound(value, name);
  const profile = absolute(env.BASALTWATER_TEST_PROFILE, 'BASALTWATER_TEST_PROFILE');
  const evidence = absolute(env.BASALTWATER_TEST_EVIDENCE, 'BASALTWATER_TEST_EVIDENCE');
  const runtime = join(profile, 'runtime');
  assert.ok(!inside(profile, evidence) && !inside(evidence, profile), 'Profile and evidence must be disjoint');
  const readyValue = env.BASALTWATER_TEST_READY_FILE;
  const token = env.BASALTWATER_TEST_READY_TOKEN;
  assert.equal(Boolean(readyValue), Boolean(token), 'Readiness file and token must be supplied together');
  let readyFile;
  if (readyValue) {
    readyFile = absolute(readyValue, 'BASALTWATER_TEST_READY_FILE');
    assert.ok(inside(profile, readyFile) && readyFile !== profile && !inside(runtime, readyFile),
      'Readiness control file must be in profile, outside runtime');
    assert.ok(typeof token === 'string' && Buffer.byteLength(JSON.stringify({ Token: token })) <= 1024,
      'Readiness token exceeds marker budget');
    try {
      await lstat(readyFile);
      throw new SessionError('Refusing existing readiness marker');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  await mkdir(profile, { recursive: true });
  await mkdir(evidence, { recursive: true });
  if (signal?.aborted) return;
  // Explicitly pass the private runtime; never inherit an ordinary profile choice.
  // pathToFileURL is required for Windows drive letters, spaces and Unicode.
  const child = spawn(process.execPath, ['--import', pathToFileURL(absolute(preloadPath, 'preloadPath')).href,
    workerPath, mode], {
    env: { ...env, BASALTWATER_TEST_PROFILE: runtime, BASALTWATER_TEST_EVIDENCE: evidence },
    stdio: ['ignore', 'inherit', 'inherit', 'ipc'], windowsHide: true,
  });
  let ended = false;
  let outcome;
  let assertion;
  child.on('message', (message) => { assertion = message; });
  const closed = new Promise((done) => {
    child.once('error', (error) => { outcome = { error: error.message }; });
    child.once('close', (code, exitSignal) => {
      ended = true;
      outcome = { ...outcome, code, signal: exitSignal };
      done();
    });
  });
  const alive = () => !ended && child.exitCode === null && child.signalCode === null;
  function checkAlive() {
    if (!alive()) throw new SessionError(`Synthetic worker exited: ${JSON.stringify(outcome ?? { code: child.exitCode })}`,
      outcome?.code || child.exitCode || 1);
  }
  let published = false;
  let temporary;
  let failure;
  const started = performance.now();
  const readyBudgetMs = Math.min(readyTimeoutMs, lifetimeMs);
  try {
    while (!assertion) {
      checkAlive();
      if (signal?.aborted) return;
      if (performance.now() - started >= readyBudgetMs) throw new SessionError('Synthetic readiness deadline exceeded');
      await delay(10);
    }
    checkAlive();
    // Only synthetic state is asserted here. Real projects supply their own checks.
    assert.equal(assertion.type, 'asserted');
    assert.equal(assertion.fixture, 'synthetic');
    assert.equal(assertion.profile, runtime);
    assert.equal(assertion.preloaded, true);
    const state = JSON.parse(await readFile(join(runtime, 'state.json'), 'utf8'));
    assert.deepEqual(state, { fixture: 'synthetic', profile: runtime, preloaded: true });
    await writeFile(join(evidence, 'assertions.json'), JSON.stringify({ syntheticAssertionsPassed: true }));
    checkAlive();
    if (performance.now() - started >= readyBudgetMs) throw new SessionError('Synthetic readiness deadline exceeded');
    if (readyFile) {
      temporary = `${readyFile}.${process.pid}.tmp`;
      await writeFile(temporary, JSON.stringify({ Token: token }), { flag: 'wx' });
      checkAlive();
      if (performance.now() - started >= readyBudgetMs) throw new SessionError('Synthetic readiness deadline exceeded');
      await rename(temporary, readyFile);
      published = true;
      checkAlive();
    }
    onReady({ pid: child.pid, runtime, readyFile });
    if (mode === 'fail-after-ready') child.send({ type: 'exercise-failure' }, () => {});
    while (!signal?.aborted && performance.now() - started < lifetimeMs) {
      checkAlive();
      await delay(10);
    }
    checkAlive();
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    // One known direct child, no descendants. OS containment remains Basaltwater's job.
    if (alive() && child.connected) child.send({ type: 'stop' }, () => {});
    const settledWithin = async () => {
      let timer;
      await Promise.race([closed, new Promise((done) => { timer = setTimeout(done, cleanupMs); })]);
      clearTimeout(timer);
    };
    await settledWithin();
    if (!ended) {
      child.kill('SIGKILL');
      await settledWithin();
    }
    const cleanupFailure = !ended || outcome?.code !== 0;
    const result = { cleanupConfirmed: ended, worker: outcome, failed: Boolean(failure) || cleanupFailure };
    if (published) await unlink(readyFile).catch((error) => { if (error.code !== 'ENOENT') throw error; });
    if (temporary) await unlink(temporary).catch((error) => { if (error.code !== 'ENOENT') throw error; });
    await writeFile(join(evidence, 'adapter-result.json'), JSON.stringify(result));
    if (!ended) throw new SessionError('Owned child cleanup unconfirmed; inspect containment before retrying');
    if (!failure && cleanupFailure) throw new SessionError('Worker failed during cleanup', outcome?.code || 1);
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  try {
    const args = process.argv.slice(2);
    assert.ok(args.length === 0 || (args.length === 2 && args[0] === '--fixture-mode'),
      'Usage: node adapter.mjs [--fixture-mode MODE]');
    await runSession({ signal: controller.signal, mode: args[1] ?? 'success' });
  } catch (error) {
    console.error(error.message);
    process.exitCode = error.exitCode || 1;
  } finally {
    process.off('SIGINT', stop);
    process.off('SIGTERM', stop);
  }
}
