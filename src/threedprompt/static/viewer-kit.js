/*
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/static/viewer-kit.js
Description: Shared helpers for the three.js tool pages (split.html, and
    the print-tool pages that follow - see docs/print-tools-plan.md):
    API calls with readable errors, binary STL loading, and a Z-up viewer
    (scene, orbit camera, lights, bed grid, framing, resize, render loop).
Inputs: ES module imports from the page; the page's own DOM elements.
Outputs: Exported functions/classes; no side effects at import time.
Troubleshooting:
    - "not a binary STL": only binary STL is parsed, which is all the
      service's in-process tools write.
    - Blank canvas: the container element must have a non-zero height
      before createViewer() is called.
*/

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";

/** Turn a FastAPI error body (string detail, or a list of validation errors) into readable text. */
export async function errorText(resp) {
  try {
    const data = await resp.json();
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("\n");
    }
    return data.detail || resp.statusText;
  } catch {
    return resp.statusText;
  }
}

/** POST JSON and return the parsed response, throwing a readable Error on failure. */
export async function postJson(url, body) {
  const resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!resp.ok) throw new Error(await errorText(resp));
  return resp.json();
}

/** Upload a mesh file to /watertight/upload and return its model_id. */
export async function uploadMesh(file) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/watertight/upload", { method: "POST", body: form });
  if (!resp.ok) throw new Error(await errorText(resp));
  return (await resp.json()).model_id;
}

/** Parse a binary STL into an indexed BufferGeometry with vertex normals. */
export function parseBinaryStl(buffer) {
  const view = new DataView(buffer);
  const count = view.getUint32(80, true);
  if (84 + count * 50 > buffer.byteLength) throw new Error("not a binary STL");
  const positions = new Float32Array(count * 9);
  let o = 84;
  for (let i = 0; i < count; i++) {
    o += 12; // skip the stored normal
    for (let j = 0; j < 9; j++) { positions[i * 9 + j] = view.getFloat32(o, true); o += 4; }
    o += 2;
  }
  let geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom = mergeVertices(geom, 1e-4);
  geom.computeVertexNormals();
  return geom;
}

/** Fetch one STL and return a mesh in the given colour. */
export async function loadStl(url, color) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`could not load ${url}: ${resp.status}`);
  const geom = parseBinaryStl(await resp.arrayBuffer());
  return new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05 }));
}

/**
 * Create a Z-up viewer inside `container` (inserted before `before`, if given).
 * Returns { THREE, scene, camera, renderer, controls, frame(objects), onFrame(fn) }.
 */
export function createViewer(container, before = null) {
  const scene = new THREE.Scene();
  const bg = getComputedStyle(document.body).getPropertyValue("--bg").trim();
  scene.background = new THREE.Color(bg || 0x16161a);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 50000);
  camera.up.set(0, 0, 1);
  camera.position.set(420, -420, 260);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  container.insertBefore(renderer.domElement, before);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444455, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(300, -200, 600);
  scene.add(sun);
  const grid = new THREE.GridHelper(400, 20, 0x888888, 0x555555);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);

  /** Match the renderer and camera to the container's size. */
  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  window.addEventListener("resize", resize);
  resize();

  /** Point the camera at the given objects. */
  function frame(objects) {
    const box = new THREE.Box3();
    for (const o of objects) box.expandByObject(o);
    if (box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const extent = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(centre);
    camera.position.copy(centre).add(new THREE.Vector3(extent * 1.15, -extent * 1.15, extent * 0.4));
  }

  const hooks = [];
  /** Register a per-frame callback (receives the rAF timestamp). */
  function onFrame(fn) { hooks.push(fn); }
  function loop(now) {
    requestAnimationFrame(loop);
    for (const fn of hooks) fn(now);
    controls.update();
    renderer.render(scene, camera);
  }
  requestAnimationFrame(loop);

  return { THREE, scene, camera, renderer, controls, frame, onFrame };
}

/**
 * Highlight overhangs: faces that point down more steeply than `angleDeg`
 * from vertical need support. Adds (or removes) a red overlay child on
 * every mesh under `roots`. Faces lying on the bed (the lowest point of
 * all meshes along `up`) are ignored. World transforms are respected, so
 * call it again after moving pieces.
 *
 * @param {THREE.Object3D[]} roots  meshes or groups to scan
 * @param {{enabled: boolean, angleDeg?: number, up?: number[], bedTolerance?: number}} opts
 * @returns {{area: number, faces: number}} overhang area (model units^2) and face count
 */
export function setOverhangs(roots, { enabled, angleDeg = 45, up = [0, 0, 1], bedTolerance = 0.3 }) {
  const upV = new THREE.Vector3(...up).normalize();
  const meshes = [];
  for (const r of roots) {
    if (!r) continue;
    r.updateMatrixWorld(true);
    r.traverse((o) => { if (o.isMesh && !o.userData.overhangOverlay) meshes.push(o); });
  }
  for (const m of meshes) {
    for (const child of [...m.children]) {
      if (child.userData.overhangOverlay) { m.remove(child); child.geometry.dispose(); }
    }
  }
  if (!enabled) return { area: 0, faces: 0 };

  // The bed is the lowest point of everything along `up`.
  const v = new THREE.Vector3();
  let bed = Infinity;
  for (const m of meshes) {
    const pos = m.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) bed = Math.min(bed, v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld).dot(upV));
  }

  const limit = Math.sin(THREE.MathUtils.degToRad(angleDeg)); // flag when -n.up > sin(angle)
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const ab = new THREE.Vector3(), ac = new THREE.Vector3(), n = new THREE.Vector3();
  let area = 0, faces = 0;
  for (const m of meshes) {
    const geom = m.geometry;
    const pos = geom.attributes.position;
    const index = geom.index;
    const tri = index ? index.count / 3 : pos.count / 3;
    const keep = [];
    for (let t = 0; t < tri; t++) {
      const i0 = index ? index.getX(3 * t) : 3 * t;
      const i1 = index ? index.getX(3 * t + 1) : 3 * t + 1;
      const i2 = index ? index.getX(3 * t + 2) : 3 * t + 2;
      a.fromBufferAttribute(pos, i0).applyMatrix4(m.matrixWorld);
      b.fromBufferAttribute(pos, i1).applyMatrix4(m.matrixWorld);
      c.fromBufferAttribute(pos, i2).applyMatrix4(m.matrixWorld);
      n.crossVectors(ab.subVectors(b, a), ac.subVectors(c, a));
      const twiceArea = n.length();
      if (twiceArea === 0) continue;
      n.divideScalar(twiceArea);
      if (-n.dot(upV) <= limit) continue;
      const lowest = Math.min(a.dot(upV), b.dot(upV), c.dot(upV));
      const highest = Math.max(a.dot(upV), b.dot(upV), c.dot(upV));
      if (highest - bed <= bedTolerance && lowest - bed <= bedTolerance) continue; // sits on the bed
      keep.push(i0, i1, i2);
      area += twiceArea / 2;
      faces += 1;
    }
    if (!keep.length) continue;
    const overlay = new THREE.BufferGeometry();
    overlay.setAttribute("position", pos);
    overlay.setIndex(keep);
    const mat = new THREE.MeshBasicMaterial({
      color: 0xe5484d, transparent: true, opacity: 0.85, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
    });
    const mesh = new THREE.Mesh(overlay, mat);
    mesh.userData.overhangOverlay = true;
    m.add(mesh);
  }
  return { area, faces };
}
