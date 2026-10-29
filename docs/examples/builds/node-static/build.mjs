import { mkdir, writeFile } from "node:fs/promises";

const output = new URL("./dist/", import.meta.url);
await mkdir(output, { recursive: true });
await writeFile(new URL("index.html", output), `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Node static example</title>
<main>
<h1>Node static example</h1>
<p>Generated on the builder; served as static files on the target.</p>
</main>
</html>
`, "utf8");
