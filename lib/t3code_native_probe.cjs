// Loaded through NODE_OPTIONS by a standalone T3 executable. Resolve the
// addon beside that executable so the host's Node installation is irrelevant.
const path = require("node:path");
require(path.join(path.dirname(process.execPath), "node_modules", "node-pty"));
// Do not enter T3's CLI, start a server, or open application state.
process.exit(0);
