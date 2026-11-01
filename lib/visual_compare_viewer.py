"""Self-contained image comparison UI, with no runtime or network dependencies."""

from __future__ import annotations

import json


def render_viewer(before: dict, after: dict) -> str:
    payload = json.dumps({"before": before, "after": after}, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    return _HTML.replace("__CAPTURE_DATA__", payload)


def render_batch_viewer(batch: dict) -> str:
    payload = json.dumps(batch, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    return _HTML.replace("__CAPTURE_DATA__", payload)


_HTML = r'''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capture comparison</title>
<style>
:root { color-scheme: dark; font: 15px system-ui,sans-serif; background:#10161e; color:#e5edf5; }
body { margin:0; padding:24px; }
h1 { font-size:24px; margin:0 0 16px; }
header { display:flex; gap:20px; align-items:center; flex-wrap:wrap; margin-bottom:18px; }
label { display:flex; gap:8px; align-items:center; }
select,button { font:inherit; padding:6px 10px; background:#263445; color:inherit; border:1px solid #576a80; border-radius:5px; }
input[type=range] { accent-color:#58c5ce; width:120px; }
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible { outline:2px solid #58c5ce; outline-offset:3px; }
#batch { margin:0 0 20px; padding:16px; background:#17212d; border:1px solid #405166; border-radius:8px; }
#batch[hidden] { display:none; }
.toolbar { display:flex; gap:14px; flex-wrap:wrap; align-items:center; }
#pair { max-width:100%; }
#coverage { color:#ffce85; }
#notes { display:block; width:100%; box-sizing:border-box; margin:12px 0; background:#10161e; color:inherit; border:1px solid #576a80; padding:10px; font:inherit; }
.grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:16px; }
.pane { border:1px solid #405166; border-radius:8px; overflow:hidden; background:#17212d; }
h2 { font-size:16px; padding:12px 16px; margin:0; border-bottom:1px solid #405166; }
.viewport { height:55vh; min-height:220px; overflow:auto; background:repeating-conic-gradient(#24303d 0% 25%,#1b2631 0% 50%) 50%/20px 20px; }
canvas { display:block; image-rendering:pixelated; }
pre { padding:14px 16px; margin:0; white-space:pre-wrap; overflow-wrap:anywhere; max-height:240px; overflow:auto; font-size:12px; }
#status { color:#b8cbdc; margin:14px 0; }
@media(max-width:700px) { body { padding:12px; } .grid { grid-template-columns:1fr; } .viewport { height:35vh; } }
</style>
<h1>Capture comparison</h1>
<section id="batch" hidden aria-label="Batch inspection">
  <div class="toolbar">
    <label>View <select id="pair"></select></label>
    <label>Detail <select id="crop"></select></label>
    <button id="previous" type="button">Previous</button><button id="next" type="button">Next</button>
  </div>
  <p id="coverage"></p>
  <label><input id="inspected" type="checkbox"> I inspected this view at this crop</label>
  <label for="notes">Observations / unresolved issues</label>
  <textarea id="notes" rows="2"></textarea>
  <div class="toolbar"><button id="export" type="button">Download inspection record</button><span id="progress" role="status"></span></div>
  <p>Inspection records stay in this tab until downloaded. Reloading clears them. Inspection does not establish visual correctness.</p>
</section>
<header>
  <label>Zoom <input id="zoom" type="range" min="1" max="400" step="1" value="100"><output id="zoomValue">100%</output></label>
  <button id="fit" type="button">Fit</button>
  <label>Compare <select id="mode"><option value="after">After</option><option value="overlay">Overlay</option><option value="difference">Difference</option></select></label>
  <label>After opacity <input id="opacity" type="range" min="0" max="100" value="50"><output id="opacityValue">50%</output></label>
  <label>Difference gain <input id="gain" type="range" min="1" max="8" value="1"><output id="gainValue">1×</output></label>
</header>
<p id="status" role="status">Loading captures…</p>
<div class="grid">
  <section class="pane"><h2 id="beforeTitle">Before</h2><div id="beforeViewport" class="viewport"><canvas id="beforeCanvas" aria-label="Before capture"></canvas></div><pre id="beforeSettings" aria-label="Before capture settings"></pre></section>
  <section class="pane"><h2 id="afterTitle">After</h2><div id="afterViewport" class="viewport"><canvas id="afterCanvas" aria-label="After capture"></canvas></div><pre id="afterSettings" aria-label="After capture settings"></pre></section>
</div>
<script type="application/json" id="captureData">__CAPTURE_DATA__</script>
<script>
'use strict';
const data = JSON.parse(document.getElementById('captureData').textContent);
const get = id => document.getElementById(id);
const beforeCanvas = get('beforeCanvas'), afterCanvas = get('afterCanvas');
const beforeViewport = get('beforeViewport'), afterViewport = get('afterViewport');
let beforeImage, afterImage, delta, changed = 0, equalSize = false;
let pairData = data, loadRevision = 0;
const records = Object.create(null);
const load = capture => new Promise((resolve, reject) => {
  const image = new Image();
  image.onload = () => resolve(image);
  image.onerror = () => reject(new Error('Invalid PNG capture'));
  image.src = capture.image;
});
function scale(value) {
  for (const canvas of [beforeCanvas, afterCanvas]) {
    canvas.style.width = `${canvas.width * value / 100}px`;
    canvas.style.height = `${canvas.height * value / 100}px`;
  }
  get('zoomValue').textContent = `${Math.round(value)}%`;
}
function paint() {
  if (!afterImage) return;
  const ctx = afterCanvas.getContext('2d');
  ctx.clearRect(0, 0, afterCanvas.width, afterCanvas.height);
  const mode = get('mode').value;
  if (mode === 'difference' && equalSize) {
    const gain = Number(get('gain').value);
    const output = ctx.createImageData(delta.width, delta.height);
    for (let i = 0; i < delta.data.length; i += 4) {
      output.data[i] = Math.min(255, delta.data[i] * gain);
      output.data[i + 1] = Math.min(255, delta.data[i + 1] * gain);
      output.data[i + 2] = Math.min(255, delta.data[i + 2] * gain);
      output.data[i + 3] = 255;
    }
    ctx.putImageData(output, 0, 0);
  } else if (mode === 'overlay' && equalSize) {
    ctx.drawImage(beforeImage, 0, 0);
    ctx.globalAlpha = Number(get('opacity').value) / 100;
    ctx.drawImage(afterImage, 0, 0);
    ctx.globalAlpha = 1;
  } else {
    ctx.drawImage(afterImage, 0, 0);
  }
  get('opacityValue').textContent = `${get('opacity').value}%`;
  get('gainValue').textContent = `${get('gain').value}×`;
  get('opacity').disabled = mode !== 'overlay';
  get('gain').disabled = mode !== 'difference';
  get('status').textContent = pairData.issues?.length ? `Incompatible observations: ${pairData.issues.join('; ')}. Side-by-side inspection only.` : equalSize
    ? `${beforeCanvas.width} × ${beforeCanvas.height} · ${changed.toLocaleString()} changed pixels (${(100 * changed / (delta.width * delta.height)).toFixed(2)}%) · scroll is synchronized`
    : `Capture sizes differ: ${beforeCanvas.width} × ${beforeCanvas.height} vs ${afterCanvas.width} × ${afterCanvas.height}. Overlay and difference require equal dimensions.`;
}
let syncing = false;
for (const [source, target] of [[beforeViewport, afterViewport], [afterViewport, beforeViewport]]) {
  source.addEventListener('scroll', () => {
    if (syncing) return;
    if (target.scrollLeft === source.scrollLeft && target.scrollTop === source.scrollTop) return;
    syncing = true;
    target.scrollLeft = source.scrollLeft;
    target.scrollTop = source.scrollTop;
    requestAnimationFrame(() => { syncing = false; });
  });
}
get('zoom').addEventListener('input', () => scale(Number(get('zoom').value)));
for (const id of ['mode', 'opacity', 'gain']) get(id).addEventListener('input', paint);
get('fit').addEventListener('click', () => {
  const value = Math.min(400, 100 * Math.min(beforeViewport.clientWidth / beforeCanvas.width, afterViewport.clientWidth / afterCanvas.width));
  get('zoom').value = String(Math.max(1, value));
  scale(Math.max(1, value));
});
async function showPair(pair, rect = null) {
 const revision = ++loadRevision;
 pairData = pair;
 beforeImage = afterImage = null;
 get('status').textContent = 'Loading captures…';
 get('inspected').disabled = true;
 try {
  const [before, after] = await Promise.all([load(pair.before), load(pair.after)]);
  if (revision !== loadRevision) return;
  for (const [image, capture] of [[before, pair.before], [after, pair.after]]) {
    if (image.naturalWidth !== capture.metadata.width || image.naturalHeight !== capture.metadata.height) throw new Error('Decoded image dimensions differ from PNG header');
  }
  beforeImage = before; afterImage = after;
  changed = 0; delta = null;
  get('mode').value = 'after';
  for (const [canvas, image, capture, side] of [[beforeCanvas, before, pair.before, 'before'], [afterCanvas, after, pair.after, 'after']]) {
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    canvas.getContext('2d').drawImage(image, 0, 0);
    get(`${side}Title`).textContent = `${side === 'before' ? 'Before' : 'After'} · ${capture.label}`;
    get(`${side}Settings`).textContent = JSON.stringify(capture.metadata, null, 2);
  }
  equalSize = beforeCanvas.width === afterCanvas.width && beforeCanvas.height === afterCanvas.height && !pair.issues?.length;
  for (const option of get('mode').options) option.disabled = !equalSize && option.value !== 'after';
  if (equalSize) {
    const first = beforeCanvas.getContext('2d').getImageData(0, 0, beforeCanvas.width, beforeCanvas.height);
    const second = afterCanvas.getContext('2d').getImageData(0, 0, afterCanvas.width, afterCanvas.height);
    delta = afterCanvas.getContext('2d').createImageData(first.width, first.height);
    for (let i = 0; i < first.data.length; i += 4) {
      const alphaDifference = Math.abs(first.data[i + 3] - second.data[i + 3]);
      let differs = alphaDifference > 0;
      for (let channel = 0; channel < 3; channel++) {
        const difference = Math.abs(first.data[i + channel] - second.data[i + channel]);
        delta.data[i + channel] = Math.max(difference, alphaDifference);
        differs = differs || difference > 0;
      }
      if (differs) changed++;
      delta.data[i + 3] = 255;
    }
  }
  scale(100); paint();
  if (rect) {
    const [x,y,w,h] = rect;
    const value = Math.max(1, Math.min(400, 100 * Math.min(beforeViewport.clientWidth / w, afterViewport.clientWidth / w, beforeViewport.clientHeight / h, afterViewport.clientHeight / h)));
    get('zoom').value = String(value); scale(value);
    beforeViewport.scrollLeft = afterViewport.scrollLeft = Math.max(0, (x + w / 2) * value / 100 - beforeViewport.clientWidth / 2);
    beforeViewport.scrollTop = afterViewport.scrollTop = Math.max(0, (y + h / 2) * value / 100 - beforeViewport.clientHeight / 2);
  } else {
    get('zoom').value = '100';
    beforeViewport.scrollLeft = afterViewport.scrollLeft = 0;
    beforeViewport.scrollTop = afterViewport.scrollTop = 0;
  }
  get('inspected').disabled = false;
 } catch(error) {
  if (revision === loadRevision) {
    beforeImage = afterImage = null;
    for (const canvas of [beforeCanvas, afterCanvas]) canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);
    get('status').textContent = error.message;
  }
 }
}
if (data.pairs) {
  get('batch').hidden = false;
  document.querySelector('h1').textContent = data.title;
  document.title = data.title;
  get('coverage').textContent = data.missing_categories.length ? `Missing coverage categories: ${data.missing_categories.join(', ')}` : 'Manifest includes silhouettes, undersides, junctions and details. Review each view and crop.';
  for (const pair of data.pairs) {
    const option = document.createElement('option'); option.value = pair.id;
    option.textContent = `${pair.category} · ${pair.label}${pair.issues.length ? ' · incompatible' : ''}`;
    get('pair').append(option);
    records[pair.id] = Object.fromEntries(['Full image', ...pair.crops.map(crop => crop.name)].map(name => [name, {inspected_at:null, notes:''}]));
  }
  const currentPair = () => data.pairs.find(pair => pair.id === get('pair').value);
  const currentRecord = () => records[get('pair').value][get('crop').value];
  function progress() {
    const all = Object.values(records).flatMap(Object.values);
    get('progress').textContent = `${all.filter(record => record.inspected_at).length} / ${all.length} views and crops inspected`;
  }
  function selectCrop() {
    const pair = currentPair(), record = currentRecord();
    get('inspected').checked = Boolean(record.inspected_at); get('notes').value = record.notes;
    showPair(pair, pair.crops.find(crop => crop.name === get('crop').value)?.rect);
  }
  function selectPair() {
    get('crop').replaceChildren();
    for (const name of ['Full image', ...currentPair().crops.map(crop => crop.name)]) {
      const option = document.createElement('option'); option.value = option.textContent = name; get('crop').append(option);
    }
    selectCrop();
  }
  get('pair').addEventListener('change', selectPair); get('crop').addEventListener('change', selectCrop);
  for (const [id, offset] of [['previous', -1], ['next', 1]]) get(id).addEventListener('click', () => {
    get('pair').selectedIndex = (get('pair').selectedIndex + offset + data.pairs.length) % data.pairs.length; selectPair();
  });
  get('inspected').addEventListener('change', () => { currentRecord().inspected_at = get('inspected').checked ? new Date().toISOString() : null; progress(); });
  get('notes').addEventListener('input', () => { currentRecord().notes = get('notes').value; });
  get('export').addEventListener('click', () => {
    const report = {schema_version:1, batch_id:data.batch_id, exported_at:new Date().toISOString(), visual_correctness:'unverified', missing_categories:data.missing_categories,
      pairs:data.pairs.map(pair => ({id:pair.id, before_sha256:pair.before.metadata.sha256, after_sha256:pair.after.metadata.sha256, issues:pair.issues, views:records[pair.id]}))};
    const url = URL.createObjectURL(new Blob([JSON.stringify(report,null,2)], {type:'application/json'}));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'inspection.json'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  selectPair(); progress();
} else showPair(data);
</script>
</html>
'''
