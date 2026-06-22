import { build } from "esbuild";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outdir = resolve(root, "out", "test");

await mkdir(outdir, { recursive: true });

await build({
  entryPoints: [resolve(root, "src", "test", "approvalModel.test.ts")],
  outdir,
  bundle: true,
  platform: "node",
  format: "esm",
  outExtension: { ".js": ".mjs" },
  external: ["vscode"],
  sourcemap: true,
});

console.log("Built tests to out/test");
