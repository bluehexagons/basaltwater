"""Self-contained image comparison UI, with no runtime or network dependencies."""

from __future__ import annotations

import json


def render_viewer(before: dict, after: dict) -> str:
    payload = json.dumps({"before": before, "after": after}, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
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
  get('status').textContent = equalSize
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
Promise.all([load(data.before), load(data.after)]).then(([before, after]) => {
  beforeImage = before; afterImage = after;
  for (const [canvas, image, capture, side] of [[beforeCanvas, before, data.before, 'before'], [afterCanvas, after, data.after, 'after']]) {
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    canvas.getContext('2d').drawImage(image, 0, 0);
    get(`${side}Title`).textContent = `${side === 'before' ? 'Before' : 'After'} · ${capture.label}`;
    get(`${side}Settings`).textContent = JSON.stringify(capture.metadata, null, 2);
  }
  equalSize = beforeCanvas.width === afterCanvas.width && beforeCanvas.height === afterCanvas.height;
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
  } else {
    for (const option of get('mode').options) option.disabled = option.value !== 'after';
  }
  scale(100); paint();
}).catch(error => { get('status').textContent = error.message; });
</script>
</html>
'''
