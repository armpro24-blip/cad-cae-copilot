import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import type { FaceEntity, HostToWebviewMessage, PreviewPayload, WebviewToHostMessage } from "../src/protocol";

declare function acquireVsCodeApi(): { postMessage(message: WebviewToHostMessage): void };
const vscode = acquireVsCodeApi();

const style = document.createElement("style");
style.textContent = `
:root {
  color-scheme: light dark;
  --ink: var(--vscode-foreground);
  --muted: var(--vscode-descriptionForeground);
  --line: var(--vscode-panel-border, rgba(128,128,128,0.28));
  --panel: var(--vscode-editorWidget-background, var(--vscode-sideBar-background));
  --panel-strong: var(--vscode-sideBar-background, var(--vscode-editorWidget-background));
  --accent: var(--vscode-focusBorder, var(--vscode-button-background));
  --hot: var(--vscode-editorWarning-foreground, var(--vscode-charts-orange));
  --button-bg: var(--vscode-button-secondaryBackground, rgba(128,128,128,0.16));
  --button-fg: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
  --button-hover: var(--vscode-button-secondaryHoverBackground, rgba(128,128,128,0.24));
}
* { box-sizing:border-box; }
[hidden] { display:none !important; }
html,body,#app { width:100%; height:100%; margin:0; overflow:hidden; }
body { font-family:var(--vscode-font-family); font-size:var(--vscode-font-size,13px); color:var(--ink); background:var(--vscode-editor-background); }
button { font:inherit; color:inherit; }
.shell { position:relative; width:100%; height:100%; background:var(--vscode-editor-background); }
.canvas { position:absolute; inset:0; }
.topbar { position:absolute; z-index:3; top:14px; left:14px; right:14px; display:flex; align-items:flex-start; justify-content:space-between; gap:12px; pointer-events:none; }
.identity,.selection { pointer-events:auto; border:1px solid var(--line); background:var(--panel); box-shadow:0 8px 24px rgba(0,0,0,0.22); }
.identity { padding:12px 14px; max-width:min(560px,70vw); }
.eyebrow { font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
h1 { font-size:16px; margin:4px 0 2px; font-weight:650; }
.detail { font-size:11px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tools { display:flex; gap:7px; pointer-events:auto; }
.tool { border:1px solid var(--line); border-radius:3px; background:var(--button-bg); color:var(--button-fg); padding:7px 10px; cursor:pointer; }
.tool:hover { background:var(--button-hover); border-color:var(--accent); }
.reliability { position:absolute; z-index:3; left:14px; bottom:14px; padding:7px 10px; border:1px solid var(--line); background:var(--panel); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
.reliability.ok { color:var(--ink); border-color:var(--accent); }
.selection { position:absolute; z-index:3; right:14px; bottom:14px; width:min(350px,calc(100vw - 28px)); max-height:42vh; overflow:auto; }
.selection-head { position:sticky; top:0; display:flex; justify-content:space-between; align-items:center; gap:10px; padding:11px 12px; background:var(--panel-strong); border-bottom:1px solid var(--line); }
.selection-list { padding:5px; }
.face { display:grid; grid-template-columns:1fr auto; gap:8px; padding:9px; border-bottom:1px solid var(--line); }
.face code { color:var(--vscode-textLink-foreground, var(--ink)); font-size:11px; font-family:var(--vscode-editor-font-family,monospace); font-variant-numeric:tabular-nums; }
.meta { color:var(--muted); font-size:10.5px; margin-top:4px; }
.copy { border:1px solid var(--line); border-radius:3px; background:var(--button-bg); color:var(--button-fg); cursor:pointer; padding:5px 8px; }
.copy:hover { background:var(--button-hover); border-color:var(--accent); }
.empty { position:absolute; inset:0; display:grid; place-content:center; text-align:center; padding:40px; color:var(--muted); }
.empty strong { display:block; color:var(--ink); font-size:20px; margin-bottom:6px; }
.row-actions { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; margin-top:14px; }
.toast { position:absolute; z-index:5; top:84px; left:50%; transform:translateX(-50%); background:var(--vscode-notifications-background,var(--panel)); color:var(--vscode-notifications-foreground,var(--ink)); border:1px solid var(--vscode-notifications-border,var(--line)); border-radius:4px; padding:7px 12px; font-size:11px; font-weight:600; opacity:0; transition:opacity .18s; }
.toast.show { opacity:1; }
`;
document.head.appendChild(style);

const app = document.querySelector<HTMLElement>("#app")!;
app.innerHTML = `<div class="shell">
  <div class="canvas"></div>
  <div class="topbar"><div class="identity"><div class="eyebrow">AIENG CAD Preview</div><h1>Waiting for model</h1><div class="detail">Open an .aieng package or connect to a live project.</div></div>
    <div class="tools"><button class="tool refresh">Refresh</button><button class="tool clear">Clear</button><button class="tool copy-all">Copy selected</button></div></div>
  <div class="reliability">Face pointers unavailable</div>
  <div class="selection" hidden><div class="selection-head"><strong>Selected faces</strong><span class="count">0</span></div><div class="selection-list"></div></div>
  <div class="empty"><strong>No preview loaded</strong><span>Waiting for an AIENG package or live Workbench project.</span></div>
  <div class="toast"></div>
</div>`;

const host = app.querySelector<HTMLElement>(".canvas")!;
const title = app.querySelector<HTMLElement>("h1")!;
const detail = app.querySelector<HTMLElement>(".detail")!;
const empty = app.querySelector<HTMLElement>(".empty")!;
const reliability = app.querySelector<HTMLElement>(".reliability")!;
const selection = app.querySelector<HTMLElement>(".selection")!;
const selectionList = app.querySelector<HTMLElement>(".selection-list")!;
const count = app.querySelector<HTMLElement>(".count")!;
const toast = app.querySelector<HTMLElement>(".toast")!;
const tools = app.querySelector<HTMLElement>(".tools")!;

function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function colorNumber(name: string, fallback: string): number {
  return new THREE.Color(cssColor(name, fallback)).getHex();
}

title.textContent = "Open a project";
detail.textContent = "Use AIENG Home, open an .aieng package, or connect to a live project.";
const eyebrow = app.querySelector<HTMLElement>(".eyebrow");
if (eyebrow) eyebrow.textContent = "AIENG CAD Preview";
for (const [className, label] of [
  ["home", "Home"],
  ["copy-build", "Copy build"],
  ["copy-modify", "Copy modify"],
  ["copy-context", "Copy context"],
] as const) {
  const button = document.createElement("button");
  button.className = `tool ${className}`;
  button.textContent = label;
  tools.appendChild(button);
}

const scene = new THREE.Scene();
scene.background = new THREE.Color(cssColor("--vscode-editor-background", "#1e1e1e"));
const camera = new THREE.PerspectiveCamera(42, 1, 0.0001, 10000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
host.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(colorNumber("--vscode-editor-foreground", "#d4d4d4"), colorNumber("--vscode-editor-background", "#1e1e1e"), 1.8));
const key = new THREE.DirectionalLight(0xffffff, 2.3); key.position.set(5, 10, 7); scene.add(key);
const rim = new THREE.DirectionalLight(colorNumber("--vscode-focusBorder", "#007fd4"), 0.55); rim.position.set(-8, 2, -5); scene.add(rim);
const grid = new THREE.GridHelper(10, 20, colorNumber("--vscode-panel-border", "#3c3c3c"), colorNumber("--vscode-editorIndentGuide-background1", "#2a2a2a")); scene.add(grid);

let object: THREE.Object3D | null = null;
let primitiveFaces = new Map<THREE.Object3D, FaceEntity>();
let faceMeshes = new Map<string, THREE.Mesh[]>();
let selected = new Map<string, FaceEntity>();
let hovered: FaceEntity | null = null;
let reliable = false;
let payload: PreviewPayload | null = null;
const overlays = new THREE.Group(); scene.add(overlays);

function showToast(text: string): void {
  toast.textContent = text; toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 1400);
}

function copy(text: string): void {
  vscode.postMessage({ kind: "copy", text });
  showToast(`Copied ${text}`);
}

function renderSelection(): void {
  selection.hidden = selected.size === 0;
  count.textContent = String(selected.size);
  const modifyButton = app.querySelector<HTMLElement>(".copy-modify");
  if (modifyButton) modifyButton.textContent = selected.size ? `Copy modify (${selected.size})` : "Copy modify";
  selectionList.innerHTML = "";
  for (const face of selected.values()) {
    const row = document.createElement("div");
    row.className = "face";
    const description = document.createElement("div");
    const pointerCode = document.createElement("code");
    pointerCode.textContent = face.pointer;
    const metadata = document.createElement("div");
    metadata.className = "meta";
    metadata.textContent = `${face.surfaceType}${face.bodyId ? ` - ${face.bodyId}` : ""}${face.roles.length ? ` - ${face.roles.join(", ")}` : ""}`;
    description.append(pointerCode, metadata);
    const copyButton = document.createElement("button");
    copyButton.className = "copy";
    copyButton.textContent = "Copy";
    copyButton.addEventListener("click", () => copy(face.pointer));
    row.append(description, copyButton);
    selectionList.appendChild(row);
  }
  renderHighlights();
}

function showEmpty(heading: string, message: string, actions: Array<{ label: string; kind: WebviewToHostMessage["kind"] }> = []): void {
  empty.hidden = false;
  empty.innerHTML = "";
  const strong = document.createElement("strong");
  strong.textContent = heading;
  const description = document.createElement("span");
  description.textContent = message;
  empty.append(strong, description);
  if (actions.length) {
    const row = document.createElement("div");
    row.className = "row-actions";
    for (const action of actions) {
      const button = document.createElement("button");
      button.className = "tool";
      button.textContent = action.label;
      button.addEventListener("click", () => vscode.postMessage({ kind: action.kind } as WebviewToHostMessage));
      row.appendChild(button);
    }
    empty.appendChild(row);
  }
}

function clearHighlights(): void {
  while (overlays.children.length) {
    const child = overlays.children.pop()!;
    overlays.remove(child);
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      (child.material as THREE.Material).dispose();
    }
  }
}

function renderHighlights(): void {
  clearHighlights();
  const highlighted = new Map(selected);
  if (hovered) highlighted.set(hovered.id, hovered);
  const selectedColor = colorNumber("--vscode-focusBorder", "#007fd4");
  const hoveredColor = colorNumber("--vscode-editorWarning-foreground", "#cca700");
  for (const face of highlighted.values()) {
    for (const mesh of faceMeshes.get(face.id) ?? []) {
      const overlay = new THREE.Mesh(mesh.geometry.clone(), new THREE.MeshBasicMaterial({
        color: selected.has(face.id) ? selectedColor : hoveredColor, transparent: true, opacity: 0.58, depthWrite: false, polygonOffset: true,
        polygonOffsetFactor: -5, side: THREE.DoubleSide,
      }));
      mesh.updateMatrixWorld(true);
      overlay.matrixAutoUpdate = false;
      overlay.matrix.copy(mesh.matrixWorld);
      overlay.renderOrder = 1000;
      overlays.add(overlay);
    }
  }
}

function applyThemeToScene(): void {
  scene.background = new THREE.Color(cssColor("--vscode-editor-background", "#1e1e1e"));
  const gridMaterial = Array.isArray(grid.material) ? grid.material : [grid.material];
  gridMaterial.forEach((material) => {
    if (material instanceof THREE.Material && "color" in material) {
      (material as THREE.Material & { color: THREE.Color }).color.set(cssColor("--vscode-panel-border", "#3c3c3c"));
    }
  });
  rim.color.set(cssColor("--vscode-focusBorder", "#007fd4"));
  renderHighlights();
}

type Transform = { isGlb: boolean; scale: number };
function displayToModel(point: THREE.Vector3, transform: Transform): THREE.Vector3 {
  if (!transform.isGlb) return point.clone();
  return new THREE.Vector3(point.x / transform.scale, -point.z / transform.scale, point.y / transform.scale);
}

function faceCenter(face: FaceEntity): THREE.Vector3 | null {
  if (face.center) return new THREE.Vector3(...face.center);
  if (face.boundingBox) {
    const b = face.boundingBox;
    return new THREE.Vector3((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
  }
  return null;
}

function deriveScale(root: THREE.Object3D, faces: FaceEntity[]): number {
  const boxes = faces.map((face) => face.boundingBox).filter(Boolean) as NonNullable<FaceEntity["boundingBox"]>[];
  if (!boxes.length) return 0.001;
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const box of boxes) for (let i = 0; i < 3; i++) { min[i] = Math.min(min[i], box[i]); max[i] = Math.max(max[i], box[i + 3]); }
  const modelMax = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
  const display = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
  return modelMax > 0 ? Math.max(display.x, display.y, display.z) / modelMax : 0.001;
}

function mapFaces(root: THREE.Object3D, faces: FaceEntity[], isGlb: boolean): void {
  primitiveFaces = new Map(); faceMeshes = new Map();
  if (!faces.length || !isGlb) return;
  const transform: Transform = { isGlb, scale: deriveScale(root, faces) };
  const faceRecords = faces.map((face) => ({ face, center: faceCenter(face) })).filter((item): item is { face: FaceEntity; center: THREE.Vector3 } => Boolean(item.center));
  const facesByBody = new Map<string, typeof faceRecords>();
  for (const record of faceRecords) {
    const bucket = facesByBody.get(record.face.bodyId ?? "__none__") ?? [];
    bucket.push(record); facesByBody.set(record.face.bodyId ?? "__none__", bucket);
  }
  type Primitive = { mesh: THREE.Mesh; center: THREE.Vector3 };
  const groups = new Map<THREE.Object3D, Primitive[]>();
  const vector = new THREE.Vector3();
  root.updateMatrixWorld(true);
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const positions = node.geometry.attributes.position;
    if (!positions?.count) return;
    const sum = new THREE.Vector3();
    for (let i = 0; i < positions.count; i++) sum.add(vector.set(positions.getX(i), positions.getY(i), positions.getZ(i)).applyMatrix4(node.matrixWorld));
    const record = { mesh: node, center: displayToModel(sum.multiplyScalar(1 / positions.count), transform) };
    const group = groups.get(node.parent ?? node) ?? []; group.push(record); groups.set(node.parent ?? node, group);
  });
  const bind = (primitive: Primitive, record: typeof faceRecords[number]) => {
    primitiveFaces.set(primitive.mesh, record.face);
    const meshes = faceMeshes.get(record.face.id) ?? []; meshes.push(primitive.mesh); faceMeshes.set(record.face.id, meshes);
  };
  const assign = (primitives: Primitive[], records: typeof faceRecords) => {
    const pairs: Array<{ p: number; f: number; d: number }> = [];
    primitives.forEach((p, pi) => records.forEach((f, fi) => pairs.push({ p: pi, f: fi, d: p.center.distanceToSquared(f.center) })));
    pairs.sort((a, b) => a.d - b.d);
    const usedP = new Set<number>(), usedF = new Set<number>();
    for (const pair of pairs) if (!usedP.has(pair.p) && !usedF.has(pair.f)) {
      usedP.add(pair.p); usedF.add(pair.f); bind(primitives[pair.p], records[pair.f]);
    }
  };
  const scoped = [...facesByBody.keys()].every((body) => body !== "__none__") && facesByBody.size > 1;
  if (!scoped) {
    assign([...groups.values()].flat(), faceRecords);
    return;
  }
  const remainingBodies = new Set(facesByBody.keys());
  for (const primitives of groups.values()) {
    let match: string | undefined;
    let distance = Infinity;
    const pc = primitives.reduce((sum, item) => sum.add(item.center), new THREE.Vector3()).multiplyScalar(1 / primitives.length);
    for (const body of remainingBodies) {
      const records = facesByBody.get(body)!;
      if (records.length !== primitives.length) continue;
      const fc = records.reduce((sum, item) => sum.add(item.center), new THREE.Vector3()).multiplyScalar(1 / records.length);
      const d = pc.distanceToSquared(fc);
      if (d < distance) { distance = d; match = body; }
    }
    if (match) { assign(primitives, facesByBody.get(match)!); remainingBodies.delete(match); }
    else assign(primitives, faceRecords);
  }
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    node.geometry.dispose();
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    materials.forEach((material) => material.dispose());
  });
}

function fit(root: THREE.Object3D): void {
  const box = new THREE.Box3().setFromObject(root);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const distance = Math.max(size.x, size.y, size.z, 0.1) * 1.8;
  camera.near = Math.max(distance / 1000, 0.0001); camera.far = distance * 100; camera.updateProjectionMatrix();
  camera.position.copy(center).add(new THREE.Vector3(distance, distance * .72, distance));
  controls.target.copy(center); controls.update();
  grid.scale.setScalar(Math.max(size.x, size.y, size.z, 1));
  grid.position.y = box.min.y;
}

async function loadPreview(next: PreviewPayload): Promise<void> {
  payload = next; selected.clear(); renderSelection(); clearHighlights();
  title.textContent = next.title; detail.textContent = next.detail;
  reliable = next.facePicking === "reliable";
  reliability.textContent = reliable ? "Stable face pointers available" : "Preview only - face pointers unavailable";
  reliability.classList.toggle("ok", reliable);
  if (object) { scene.remove(object); disposeObject(object); object = null; }
  if (!next.assetBase64 || !next.format) {
    if (next.emptyReason === "no_preview") {
      showEmpty("Project created", next.detail, [
        { label: "Copy starter prompt", kind: "copyStarterPrompt" },
        { label: "Refresh", kind: "refresh" },
        { label: "Back to AIENG Home", kind: "openHome" },
      ]);
    } else {
      showEmpty("Preview unavailable", next.detail, [
        { label: "Refresh", kind: "refresh" },
        { label: "Back to AIENG Home", kind: "openHome" },
      ]);
    }
    return;
  }
  empty.hidden = true;
  const bytes = Uint8Array.from(atob(next.assetBase64), (char) => char.charCodeAt(0)).buffer;
  object = await new Promise<THREE.Object3D>((resolve, reject) => {
    if (next.format === "glb") new GLTFLoader().parse(bytes, "", (gltf) => resolve(gltf.scene), reject);
    else {
      try {
        const geometry = new STLLoader().parse(bytes); geometry.computeVertexNormals();
        resolve(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0xadb9b0, metalness: .18, roughness: .62 })));
      } catch (error) { reject(error); }
    }
  });
  scene.add(object); fit(object);
  mapFaces(object, Object.values(next.faces), next.format === "glb");
  if (reliable && primitiveFaces.size === 0) {
    reliable = false; reliability.textContent = "Topology present - face mapping could not be verified"; reliability.classList.remove("ok");
  }
}

const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
function faceAtEvent(event: MouseEvent): FaceEntity | undefined {
  if (!object || !reliable) return undefined;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObject(object, true)[0];
  return hit ? primitiveFaces.get(hit.object) : undefined;
}

renderer.domElement.addEventListener("mousemove", (event) => {
  const face = faceAtEvent(event);
  if (face?.id === hovered?.id) return;
  hovered = face ?? null;
  renderer.domElement.style.cursor = face ? "crosshair" : "grab";
  renderHighlights();
});

renderer.domElement.addEventListener("mouseleave", () => {
  hovered = null;
  renderHighlights();
});

renderer.domElement.addEventListener("click", (event) => {
  const face = faceAtEvent(event);
  if (!face) return;
  if (!event.shiftKey) selected.clear();
  if (selected.has(face.id)) selected.delete(face.id); else selected.set(face.id, face);
  renderSelection();
});

renderer.domElement.addEventListener("dblclick", (event) => {
  const face = faceAtEvent(event);
  if (face) copy(face.pointer);
});

app.querySelector(".refresh")!.addEventListener("click", () => vscode.postMessage({ kind: "refresh" }));
app.querySelector(".clear")!.addEventListener("click", () => { selected.clear(); renderSelection(); });
app.querySelector(".copy-all")!.addEventListener("click", () => {
  if (selected.size) copy([...selected.values()].map((face) => face.pointer).join(" "));
});
app.querySelector(".home")!.addEventListener("click", () => vscode.postMessage({ kind: "openHome" }));
app.querySelector(".copy-build")!.addEventListener("click", () => vscode.postMessage({ kind: "copyStarterPrompt" }));
app.querySelector(".copy-modify")!.addEventListener("click", () => {
  const pointers = [...selected.values()].map((face) => face.pointer);
  vscode.postMessage({ kind: "copyModifyPrompt", pointers });
  showToast(pointers.length ? `Copied modify prompt - ${pointers.length} face${pointers.length > 1 ? "s" : ""}` : "Copied modify prompt");
});
app.querySelector(".copy-context")!.addEventListener("click", () => vscode.postMessage({ kind: "copyProjectContext" }));

function resize(): void {
  const width = Math.max(host.clientWidth, 1), height = Math.max(host.clientHeight, 1);
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(host); resize();
new MutationObserver(() => applyThemeToScene()).observe(document.body, { attributes: true, attributeFilter: ["class", "style"] });
(function animate() { controls.update(); renderer.render(scene, camera); requestAnimationFrame(animate); })();

window.addEventListener("message", (event: MessageEvent<HostToWebviewMessage>) => {
  if (event.data.kind === "preview") void loadPreview(event.data).catch((error) => {
    showEmpty("Preview load failed", String(error));
  });
  if (event.data.kind === "status") {
    showEmpty(event.data.tone === "error" ? "Connection failed" : "Status", event.data.detail);
  }
});
vscode.postMessage({ kind: "ready" });
