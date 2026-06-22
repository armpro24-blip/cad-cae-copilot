import { readFile } from "node:fs/promises";
import { readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import JSZip from "jszip";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const files = await readdir(root);
const vsix = files
  .filter((name) => name.endsWith(".vsix"))
  .sort()
  .at(-1);

if (!vsix) {
  console.error("No .vsix artifact found. Run npm run vsix first.");
  process.exit(1);
}

console.log(`Smoke-testing ${vsix}...`);

const buffer = await readFile(resolve(root, vsix));
const zip = await JSZip.loadAsync(buffer);
const names = Object.keys(zip.files);

const required = [
  "extension/out/extension.js",
  "extension/package.json",
  "extension/media/viewer.js",
  "extension/media/home.js",
];

const missing = required.filter((path) => !names.includes(path));
if (missing.length > 0) {
  console.error(`Missing required files in ${vsix}: ${missing.join(", ")}`);
  process.exit(1);
}

const forbidden = [
  "extension/out/test/",
  "extension/src/",
  "extension/scripts/",
  "extension/webview-src/",
];
const foundForbidden = forbidden.filter((prefix) => names.some((name) => name.startsWith(prefix)));
if (foundForbidden.length > 0) {
  console.error(`Forbidden paths found in ${vsix}: ${foundForbidden.join(", ")}`);
  process.exit(1);
}

console.log(`All required files present and no forbidden paths in ${vsix}.`);
