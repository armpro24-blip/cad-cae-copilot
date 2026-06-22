import { build } from "esbuild";
import { mkdir, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const testDir = resolve(root, "src", "test");
const outdir = resolve(root, "out", "test");

await mkdir(outdir, { recursive: true });

// Discover every src/test/*.test.ts so new test files are picked up automatically.
const entryPoints = (await readdir(testDir))
  .filter((name) => name.endsWith(".test.ts"))
  .map((name) => resolve(testDir, name));

await build({
  entryPoints,
  outdir,
  bundle: true,
  platform: "node",
  format: "esm",
  outExtension: { ".js": ".mjs" },
  external: ["vscode"],
  sourcemap: true,
});

console.log("Built tests to out/test");
