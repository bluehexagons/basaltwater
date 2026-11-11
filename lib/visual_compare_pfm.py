"""Bounded linear PFM decoding and observational HDR comparisons."""

from __future__ import annotations

import argparse
from array import array
import base64
import hashlib
from io import BytesIO
import json
import math
import os
import stat
import struct
import sys
import uuid

from lib.atomic_io import write_json_atomic
from lib.validation import validate_filesystem_path


MAX_PFM_BYTES = 16 * 1024 * 1024
MAX_PFM_PIXELS = 16 * 1024 * 1024
MAX_HEADER_BYTES = 4096


def read_pfm(path: str, settings: dict) -> tuple[dict, array]:
    """Read PF/Pf float32, applying scale and bottom-to-top row order."""
    resolved = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(resolved, must_exist=True)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("PFM capture must be a regular file")
        with os.fdopen(descriptor, "rb") as file:
            descriptor = -1
            content = file.read(MAX_PFM_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > MAX_PFM_BYTES:
        raise ValueError("PFM capture exceeds 16 MiB")
    stream = BytesIO(content)

    def line() -> bytes:
        while True:
            value = stream.readline(MAX_HEADER_BYTES + 1)
            if not value.endswith(b"\n") or stream.tell() > MAX_HEADER_BYTES:
                raise ValueError("Invalid or oversized PFM header")
            value = value.split(b"#", 1)[0].strip()
            if value:
                return value

    magic = line()
    if magic not in (b"PF", b"Pf"):
        raise ValueError("Capture must have a PF (RGB) or Pf (grayscale) PFM header")
    dimensions = line().split()
    if len(dimensions) != 2 or any(not item.isdigit() for item in dimensions):
        raise ValueError("Invalid PFM dimensions")
    width, height = map(int, dimensions)
    if not width or not height or max(width, height) > 16384 or width * height > MAX_PFM_PIXELS:
        raise ValueError("PFM dimensions must be positive, at most 16384 per side and 16 megapixels")
    try:
        scale = float(line())
    except ValueError as exc:
        raise ValueError("Invalid PFM scale") from exc
    if not math.isfinite(scale) or scale == 0:
        raise ValueError("PFM scale must be finite and nonzero")
    channels = 3 if magic == b"PF" else 1
    payload = content[stream.tell():]
    if len(payload) != width * height * channels * 4:
        raise ValueError("PFM payload length does not match dimensions/channels")
    values = array("d")
    row_bytes = width * channels * 4
    sample_format = "<f" if scale < 0 else ">f"
    for row in range(height - 1, -1, -1):
        for (sample,) in struct.iter_unpack(sample_format, payload[row * row_bytes:(row + 1) * row_bytes]):
            value = sample * abs(scale)
            if not math.isfinite(value):
                raise ValueError("PFM contains a non-finite sample (NaN/Infinity or scale overflow)")
            values.append(value)
    metadata = {**settings, "image_path": resolved, "width": width, "height": height,
                "channels": channels, "format": "pfm", "color_space": "linear",
                "scale": scale, "finite_samples": True, "sample_count": len(values),
                "minimum": min(values), "maximum": max(values),
                "sha256": hashlib.sha256(content).hexdigest()}
    return metadata, values


def error_metrics(before: array, after: array, relative_floor: float) -> dict:
    """Measure all linear channels, relative to the before/reference sample."""
    if not math.isfinite(relative_floor) or relative_floor <= 0:
        raise ValueError("Relative floor must be finite and positive")
    if len(before) != len(after) or not before:
        raise ValueError("PFM metrics require equal nonempty sample counts")
    count = len(before)
    absolute_sum = relative_sum = rms = absolute_max = relative_max = 0.0
    root_count = math.sqrt(count)
    changed = 0
    for reference, observed in zip(before, after):
        absolute = abs(observed - reference)
        relative = absolute / max(abs(reference), relative_floor)
        changed += absolute != 0
        # Divide before summing to reduce overflow on large finite inputs.
        absolute_sum += absolute / count
        relative_sum += relative / count
        rms = math.hypot(rms, absolute / root_count)
        absolute_max = max(absolute_max, absolute)
        relative_max = max(relative_max, relative)
    metrics = {"sample_count": count, "changed_samples": changed,
               "mean_absolute_error": absolute_sum, "max_absolute_error": absolute_max,
               "root_mean_square_error": rms,
               "mean_relative_error": relative_sum, "max_relative_error": relative_max,
               "relative_floor": relative_floor,
               "relative_definition": "abs(after-before) / max(abs(before), relative_floor)"}
    if any(not math.isfinite(value) for value in metrics.values() if isinstance(value, float)):
        raise ValueError("PFM error metrics overflow; choose a representable scale/relative floor")
    return metrics


def compare_pfm(args: argparse.Namespace) -> dict:
    """Write private evidence; thresholds and acceptance remain project-owned."""
    from lib.agent_visuals import _directory, _settings

    if not all(os.path.splitext(path)[1].lower() == ".pfm" for path in (args.before, args.after)):
        raise ValueError("Compare PFM with PFM; PNG and linear PFM have different sample domains")
    exposure = getattr(args, "exposure", 0.0)
    floor = getattr(args, "relative_floor", 1e-6)
    if not math.isfinite(exposure) or not -32 <= exposure <= 32:
        raise ValueError("Exposure must be finite and from -32 through 32 stops")
    if not math.isfinite(floor) or floor <= 0:
        raise ValueError("Relative floor must be finite and positive")
    before, first = read_pfm(args.before, _settings(args.before_settings))
    after, second = read_pfm(args.after, _settings(args.after_settings))
    compatible = all(before[key] == after[key] for key in ("width", "height", "channels"))
    metrics = error_metrics(first, second, floor) if compatible else None
    result = {"before": before, "after": after, "metrics": metrics,
              "comparison_status": "measured" if compatible else "incompatible-dimensions-or-channels",
              "acceptance": "project-owned", "view": {"exposure_stops": exposure, "transfer": "clamp-linear-then-sRGB"}}
    images = []
    for values in (first, second):
        if sys.byteorder != "little":
            values.byteswap()
        images.append(base64.b64encode(values.tobytes()).decode("ascii"))
    payload = json.dumps({**result, "images": images}, allow_nan=False, ensure_ascii=True).replace("<", "\\u003c")
    directory = _directory(args.output, "compare-" + uuid.uuid4().hex[:12])
    result["viewer"] = os.path.join(directory, "index.html")
    descriptor = os.open(result["viewer"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(_HTML.replace("__PFM_DATA__", payload))
    write_json_atomic(os.path.join(directory, "comparison.json"), result)
    return result


_HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Linear HDR comparison</title>
<style>
body{background:#18181b;color:#e4e4e7;font:14px system-ui;margin:20px}header{display:flex;gap:16px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}.viewport{overflow:auto;height:60vh;background:#27272a}
canvas{display:block;image-rendering:pixelated}pre{white-space:pre-wrap;overflow-wrap:anywhere}input,select{accent-color:#a78bfa}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style>
<h1>Linear HDR comparison</h1>
<header>
<label>Exposure (stops) <input id="exposure" type="range" min="-32" max="32" step="any"><output id="ev"></output></label>
<label>Zoom <input id="zoom" type="range" min="1" max="400" value="100"><output id="zoomValue">100%</output></label>
<label>Compare <select id="mode"><option value="after">After</option><option value="overlay">Overlay 50%</option><option value="difference">Absolute linear difference</option></select></label>
</header>
<p>Display: multiply by 2^exposure, clamp to [0,1], then encode sRGB. Negative values clip to black. Metrics use original linear samples.</p>
<p id="status" role="status"></p><details><summary>Linear error metrics (project owns acceptance)</summary><pre id="metrics"></pre></details>
<div class="grid"><section><h2>Before / reference</h2><div class="viewport" id="left"><canvas id="before"></canvas></div><details><summary>Before metadata</summary><pre id="beforeMetadata"></pre></details></section>
<section><h2>After</h2><div class="viewport" id="right"><canvas id="after"></canvas></div><details><summary>After metadata</summary><pre id="afterMetadata"></pre></details></section></div>
<script type="application/json" id="data">__PFM_DATA__</script>
<script>
'use strict';
const get = id => document.getElementById(id), data = JSON.parse(get('data').textContent);
const captures = [data.before, data.after];
const samples = data.images.map(encoded => {
  const raw = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
  const view = new DataView(raw.buffer), values = new Float64Array(raw.length / 8);
  for (let i=0;i<values.length;i++) values[i] = view.getFloat64(i*8, true);
  return values;
});
const compatible = data.metrics !== null;
get('exposure').value = data.view.exposure_stops;
get('metrics').textContent = JSON.stringify(data.metrics, null, 2);
get('status').textContent = compatible ? 'Linear sample metrics measured; no acceptance threshold applied.' : 'Dimensions or channels differ: side-by-side inspection only; metrics unavailable.';
for (const option of get('mode').options) option.disabled = !compatible && option.value !== 'after';
function display(value, exposure) {
  const linear = Math.min(1, Math.max(0, value * Math.pow(2, exposure)));
  return 255 * (linear <= 0.0031308 ? 12.92 * linear : 1.055 * Math.pow(linear, 1/2.4) - 0.055);
}
function paint() {
  const exposure = Number(get('exposure').value), mode = get('mode').value;
  get('ev').textContent = `${exposure} EV`;
  for (let side=0;side<2;side++) {
    const metadata = captures[side], canvas = get(side ? 'after' : 'before');
    canvas.width = metadata.width; canvas.height = metadata.height;
    const ctx = canvas.getContext('2d'), output = ctx.createImageData(canvas.width, canvas.height);
    for (let pixel=0;pixel<canvas.width*canvas.height;pixel++) {
      for (let channel=0;channel<3;channel++) {
        const index = pixel*metadata.channels + (metadata.channels === 1 ? 0 : channel);
        let value = samples[side][index];
        if (side && compatible && mode === 'difference') value = Math.abs(value - samples[0][index]);
        if (side && compatible && mode === 'overlay') value = value/2 + samples[0][index]/2;
        output.data[pixel*4+channel] = display(value, exposure);
      }
      output.data[pixel*4+3] = 255;
    }
    ctx.putImageData(output,0,0);
    canvas.style.width = `${canvas.width*Number(get('zoom').value)/100}px`;
    canvas.style.height = `${canvas.height*Number(get('zoom').value)/100}px`;
  }
  get('zoomValue').textContent = `${get('zoom').value}%`;
}
for (const id of ['exposure','mode','zoom']) get(id).addEventListener('input',paint);
for (const side of ['before','after']) get(side+'Metadata').textContent = JSON.stringify(data[side],null,2);
for (const [source,target] of [[get('left'),get('right')],[get('right'),get('left')]]) source.addEventListener('scroll',()=>{
  if(target.scrollLeft!==source.scrollLeft) target.scrollLeft=source.scrollLeft;
  if(target.scrollTop!==source.scrollTop) target.scrollTop=source.scrollTop;
});
paint();
</script></html>'''
