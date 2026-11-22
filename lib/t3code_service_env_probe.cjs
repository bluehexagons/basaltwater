// Use the same parser as T3's service launcher. Never emit file contents,
// configured values, or exception text, which can contain credentials.
const fs = require("node:fs");
const { parseEnv } = require("node:util");
let descriptor;
try {
  descriptor = fs.openSync(
    process.env.BASALTW_T3_ENV_FILE,
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK,
  );
  const limit = 1024 * 1024;
  const info = fs.fstatSync(descriptor);
  if (!info.isFile() || info.size > limit) process.exit(1);
  const buffer = Buffer.alloc(limit + 1);
  let length = 0;
  while (length < buffer.length) {
    const count = fs.readSync(descriptor, buffer, length, buffer.length - length, null);
    if (count === 0) break;
    length += count;
  }
  if (length > limit) process.exit(1);
  const values = parseEnv(buffer.subarray(0, length).toString("utf8"));
  const expected = {
    T3CODE_HOST: process.env.BASALTW_T3_EXPECTED_HOST,
    T3CODE_PORT: process.env.BASALTW_T3_EXPECTED_PORT,
  };
  const conflicts = Object.keys(expected).filter(
    (key) => Object.hasOwn(values, key) && values[key] !== expected[key],
  );
  fs.writeSync(1, JSON.stringify({ conflicts }));
} catch {
  process.exitCode = 1;
} finally {
  if (descriptor !== undefined) fs.closeSync(descriptor);
}
// A standalone executable must never proceed into T3's embedded CLI.
process.exit(process.exitCode || 0);
