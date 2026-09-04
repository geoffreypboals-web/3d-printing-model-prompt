# Vendored three.js

This directory holds a locally-vendored copy of [three.js](https://threejs.org/)
r0.160.0 (MIT license — see `three/LICENSE`), used by `../index.html`.

It is vendored rather than loaded from a CDN `<script>` tag on purpose: the
viewer needs to keep working in an offline / air-gapped Docker deployment
(rule 4 in `CLAUDE.md` — Docker-first, cross-platform), and not depend on an
external CDN being reachable from wherever this tool ends up running.

Only the files the viewer actually imports are included, not the whole
`three` npm package (which is ~20MB with every example/addon):

- `three/build/three.module.min.js` — the core library
- `three/examples/jsm/loaders/GLTFLoader.js` — loads the analyzed mesh
- `three/examples/jsm/utils/BufferGeometryUtils.js` — a dependency of GLTFLoader
- `three/examples/jsm/controls/OrbitControls.js` — mouse/touch camera controls

## Updating the version

```bash
npm install three@<new-version> --prefix /tmp/three_fetch
cp /tmp/three_fetch/node_modules/three/build/three.module.min.js viewer/vendor/three/build/
cp /tmp/three_fetch/node_modules/three/LICENSE viewer/vendor/three/
cp /tmp/three_fetch/node_modules/three/examples/jsm/loaders/GLTFLoader.js viewer/vendor/three/examples/jsm/loaders/
cp /tmp/three_fetch/node_modules/three/examples/jsm/controls/OrbitControls.js viewer/vendor/three/examples/jsm/controls/
cp /tmp/three_fetch/node_modules/three/examples/jsm/utils/BufferGeometryUtils.js viewer/vendor/three/examples/jsm/utils/
```

Then re-check `GLTFLoader.js` and `OrbitControls.js` for any new relative
(non-`'three'`) imports — three.js occasionally adds addon dependencies
between minor versions — and vendor those too.
