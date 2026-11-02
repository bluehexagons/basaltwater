// Offline UI contract: switching pairs/crops, incompatible modes and manual records.
'use strict';
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(0, 'utf8');
const payload = html.match(/id="captureData">([\s\S]*?)<\/script>/)[1];
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
class Element {
  constructor(id) {
    this.id = id; this.value = ''; this.children = []; this.handlers = {}; this.checked = false; this.style = {};
    this.clientWidth = 600; this.clientHeight = 300; this.scrollLeft = this.scrollTop = 0;
    this.width = this.height = 4;
  }
  get options() { return this.children; }
  get selectedIndex() { return this.children.findIndex(child => child.value === this.value); }
  set selectedIndex(index) { this.value = this.children[index].value; }
  append(child) { this.children.push(child); if (this.children.length === 1) this.value = child.value; }
  replaceChildren() { this.children = []; this.value = ''; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  click() { if (this.id === 'a') downloaded = this; else this.handlers.click?.(); }
  getContext() {
    return {clearRect() {}, drawImage() {}, putImageData() {}, createImageData: (w,h) => ({width:w,height:h,data:new Uint8ClampedArray(w*h*4)}),
      getImageData: (x,y,w,h) => ({width:w,height:h,data:new Uint8ClampedArray(w*h*4)})};
  }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
get('captureData').textContent = payload;
for (const value of ['after','overlay','difference']) { const el = new Element(); el.value = value; get('mode').append(el); }
get('opacity').value = '50'; get('gain').value = '1';
let downloaded, blob, timerId = 0;
const timers = new Map(), documentHandlers = {};
const context = {document:{getElementById:get, querySelector:get, createElement:tag => new Element(tag),
  addEventListener:(event, handler) => { documentHandlers[event] = handler; }},
  Image:class { constructor() { this.naturalWidth = this.naturalHeight = 4; } set src(value) { Promise.resolve().then(() => this.onload()); } },
  Blob, URL:{createObjectURL(value) { blob = value; return 'blob:test'; }, revokeObjectURL() {}},
  requestAnimationFrame:fn => fn(), setTimeout:fn => { timers.set(++timerId, fn); return timerId; },
  clearTimeout:id => timers.delete(id), Uint8ClampedArray};
vm.runInNewContext(script, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await flush();
  assert.equal(get('inspected').disabled, false, get('status').textContent);
  assert.equal(get('inspected').checked, false);
  get('inspected').checked = true; get('inspected').handlers.change();
  get('notes').value = 'junction protrudes'; get('notes').handlers.input();
  get('crop').selectedIndex = 1; get('crop').handlers.change(); await flush();
  assert.equal(get('inspected').checked, false);
  get('next').click(); await flush();
  assert.match(get('status').textContent, /Incompatible/);
  assert.equal(get('mode').options[1].disabled, true);
  get('previous').click(); await flush();
  assert.equal(get('mode').options[1].disabled, false);
  assert.equal(get('inspected').checked, true);
  assert.equal(get('notes').value, 'junction protrudes');
  get('export').click();
  const record = JSON.parse(await blob.text());
  assert.equal(downloaded.download, 'inspection.json');
  assert.ok(record.pairs[0].views['Full image'].inspected_at);
  assert.equal(record.pairs[0].views.attachment.inspected_at, null);
  assert.equal(record.visual_correctness, 'unverified');
  if (JSON.parse(payload).sequence) {
    assert.equal(get('timeline').hidden, false);
    get('zoom').value = '150'; get('zoom').handlers.input();
    get('mode').value = 'overlay'; get('mode').handlers.input();
    get('play').click();
    assert.equal(get('play').textContent, 'Pause');
    // Ignore the export's URL cleanup timer and advance playback once.
    const frameTimer = Math.max(...timers.keys());
    const tick = timers.get(frameTimer); timers.delete(frameTimer); await tick(); await flush();
    assert.equal(get('pair').selectedIndex, 1);
    assert.equal(get('scrub').value, '1');
    assert.equal(get('zoom').value, '150');
    assert.equal(get('mode').value, 'after'); // incompatible frame disables overlay
    assert.equal(get('inspected').checked, false); // playback never marks inspection
    get('scrub').value = '0'; get('scrub').handlers.input(); await flush();
    assert.equal(get('play').textContent, 'Play');
    assert.equal(get('pair').selectedIndex, 0);
    assert.equal(get('zoom').value, '150');
    get('play').click(); context.document.hidden = true; documentHandlers.visibilitychange();
    assert.equal(get('play').textContent, 'Play');
  }
  process.stdout.write('Batch UI contract passed\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
