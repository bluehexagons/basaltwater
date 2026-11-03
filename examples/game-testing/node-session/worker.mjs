// Disposable synthetic fixture. It starts no descendants and never finds a user profile.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { basename, isAbsolute, join } from 'node:path';

const runtime = process.env.BASALTWATER_TEST_PROFILE;
const evidence = process.env.BASALTWATER_TEST_EVIDENCE;
assert.ok(runtime && isAbsolute(runtime) && basename(runtime) === 'runtime');
assert.ok(evidence && isAbsolute(evidence));
assert.equal(globalThis.syntheticSessionPreloaded, true);
assert.ok(process.send, 'The synthetic worker requires its project adapter IPC');
// Refuse reuse: the adapter must explicitly provide a fresh disposable runtime.
mkdirSync(runtime);
const state = { fixture: 'synthetic', profile: runtime, preloaded: true };
writeFileSync(join(runtime, 'state.json'), JSON.stringify(state), { flag: 'wx' });
writeFileSync(join(evidence, 'worker.json'), JSON.stringify(state));
const mode = process.argv[2];
if (mode === 'exit7') process.exit(7);
if (mode === 'exit0') process.exit(0);

let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  writeFileSync(join(evidence, 'worker-stopped.json'), JSON.stringify({ cooperative: true }));
  process.disconnect();
}
process.on('message', (message) => {
  if (message?.type === 'stop') stop();
  if (message?.type === 'exercise-failure' && mode === 'fail-after-ready') process.exit(7);
});
// If the adapter disappears, the owned fixture has no reason to stay alive.
process.on('disconnect', () => process.exit(0));
if (mode !== 'absent') {
  process.send({ type: 'asserted', ...state, fixture: mode === 'wrong' ? 'wrong' : state.fixture });
}
