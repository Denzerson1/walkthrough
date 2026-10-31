// docs/scale-sheet.png: every prepared furniture model (its phone file, as a
// phone loads it), seen straight from the front at one scale, a row per
// style, each row beginning with a 2.0 m door and a 1.75 m person. Wrong
// scale shows at a glance; labels give each piece's measured W x D x H.
//
// And each model's thumbnail for the design menu: a three-quarter view,
// 256 px WebP, data/assets/furniture/<id>/thumb.webp, named in its catalog
// entry as `thumbnail`.
//
//   node scripts/prepare-models.mjs && node scripts/scale-sheet.mjs

import { createReadStream, existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(ROOT, 'apps', 'web');
const { chromium } = createRequire(join(WEB, 'package.json'))('@playwright/test');

const ORDER = ['sofa', 'armchair', 'coffee_table', 'side_table', 'floor_lamp', 'rug', 'bed', 'nightstand', 'table_lamp'];
const pieces = readdirSync(join(ROOT, 'catalog', 'furniture'))
  .filter((f) => f.endsWith('.json'))
  .map((f) => JSON.parse(readFileSync(join(ROOT, 'catalog', 'furniture', f), 'utf8')))
  .filter((e) => e.models && existsSync(join(ROOT, 'data', 'assets', 'furniture', e.id, 'phone.glb')));
const rows = {};
for (const e of pieces) (rows[e.styles[0]] ??= []).push(e);
for (const list of Object.values(rows)) list.sort((a, b) => ORDER.indexOf(a.category) - ORDER.indexOf(b.category));

const TYPES = { '.js': 'text/javascript', '.wasm': 'application/wasm', '.glb': 'model/gltf-binary', '.html': 'text/html' };
const page = `<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#fff;font:12px system-ui,sans-serif;color:#222}
.l{position:absolute;transform:translateX(-50%);text-align:center;white-space:nowrap;line-height:1.25}
.r{position:absolute;left:12px;font-weight:600;font-size:15px}</style>
<script type="importmap">{"imports":{"three":"/three/build/three.module.js","three/addons/":"/three/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
const rows = ${JSON.stringify(Object.entries(rows).map(([style, list]) => ({ style, list: list.map(({ id, name, measuredM }) => ({ id, name, measuredM })) })))};
const PX = 110, GAP = 0.35, ROW = 3.0, LEFT = 1.6;
const width = Math.max(...rows.map((r) => r.list.reduce((s, p) => s + p.measuredM[0] + GAP, LEFT + 0.9 + 0.6 + 2 * GAP)));
const height = rows.length * ROW;
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(Math.ceil(width * PX), Math.ceil(height * PX));
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color('#ffffff');
scene.add(new THREE.HemisphereLight('#ffffff', '#b0a898', 2.2));
const sun = new THREE.DirectionalLight('#ffffff', 1.6); sun.position.set(2, 4, 6); scene.add(sun);
const camera = new THREE.OrthographicCamera(0, width, 0, -height, -50, 50);
const ktx2 = new KTX2Loader().setTranscoderPath('/three/examples/jsm/libs/basis/').detectSupport(renderer);
const loader = new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
const grey = new THREE.MeshBasicMaterial({ color: '#c9c9c9' });
const line = new THREE.LineBasicMaterial({ color: '#999' });
function label(x, y, html, cls = 'l') {
  const d = document.createElement('div'); d.className = cls; d.innerHTML = html;
  d.style.left = x * PX + 'px'; d.style.top = -y * PX + 'px'; document.body.appendChild(d);
}
for (const [r, row] of rows.entries()) {
  const floor = -(r + 1) * ROW + 0.55;
  label(0, floor + 2.15, row.style, 'r');
  const ground = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, floor, 0), new THREE.Vector3(width, floor, 0)]);
  scene.add(new THREE.Line(ground, line));
  let x = LEFT;
  // A 0.9 x 2.0 m door, outlined, and a 1.75 m person.
  const door = new THREE.BufferGeometry().setFromPoints([[x, floor], [x, floor + 2], [x + 0.9, floor + 2], [x + 0.9, floor]].map(([a, b]) => new THREE.Vector3(a, b, 0)));
  scene.add(new THREE.Line(door, line));
  label(x + 0.45, floor - 0.04, 'door 2.0 m');
  x += 0.9 + GAP;
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 1.75 - 0.34 - 0.24, 4, 12), grey);
  body.position.set(x + 0.3, floor + (1.75 - 0.24) / 2, -1); scene.add(body);
  const head = new THREE.Mesh(new THREE.CircleGeometry(0.11, 24), grey);
  head.position.set(x + 0.3, floor + 1.75 - 0.12, -1); scene.add(head);
  label(x + 0.3, floor - 0.04, 'person 1.75 m');
  x += 0.6 + GAP;
  for (const [i, p] of row.list.entries()) {
    const gltf = await loader.loadAsync('/models/' + p.id + '/phone.glb');
    gltf.scene.position.set(x + p.measuredM[0] / 2, floor, 0);
    scene.add(gltf.scene);
    const [w, h, d] = p.measuredM.map((v) => Math.round(v * 100));
    // Every other label a line lower, so narrow neighbours' do not overlap.
    label(x + p.measuredM[0] / 2, floor - 0.04 - (i % 2) * 0.3, p.name.split(' ').slice(0, 3).join(' ') + '<br>' + w + ' × ' + d + ' × ' + h + ' cm');
    x += p.measuredM[0] + GAP;
  }
}
renderer.render(scene, camera);
document.body.dataset.done = '1';

// Thumbnails: each model alone, from the front left and a little above.
window.thumbnail = async (id) => {
  const size = 256;
  const r = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  r.setPixelRatio(1); r.setSize(size, size); r.outputColorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Scene();
  s.add(new THREE.HemisphereLight('#ffffff', '#b0a898', 2.4));
  const key = new THREE.DirectionalLight('#ffffff', 1.4); key.position.set(-2, 4, 5); s.add(key);
  const model = (await new GLTFLoader().setKTX2Loader(new KTX2Loader().setTranscoderPath('/three/examples/jsm/libs/basis/').detectSupport(r)).setMeshoptDecoder(MeshoptDecoder).loadAsync('/models/' + id + '/phone.glb')).scene;
  s.add(model);
  const box = new THREE.Box3().setFromObject(model);
  const centre = box.getCenter(new THREE.Vector3());
  const radius = box.getSize(new THREE.Vector3()).length() / 2;
  const cam = new THREE.PerspectiveCamera(30, 1, 0.01, 50);
  const dir = new THREE.Vector3(-0.55, 0.45, 1).normalize();
  cam.position.copy(centre).addScaledVector(dir, radius / Math.sin((15 * Math.PI) / 180) * 1.02);
  cam.lookAt(centre);
  r.render(s, cam);
  const url = r.domElement.toDataURL('image/webp', 0.82);
  r.dispose(); r.forceContextLoss();
  return url;
};
</script>`;

const server = createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = url === '/' ? null
    : url.startsWith('/three/') ? join(WEB, 'node_modules', url.slice(1))
      : url.startsWith('/models/') ? join(ROOT, 'data', 'assets', 'furniture', url.slice('/models/'.length))
        : '';
  if (file === null) { res.writeHead(200, { 'content-type': 'text/html' }); res.end(page); return; }
  if (!file || !existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
  createReadStream(file).pipe(res);
}).listen(0);
await new Promise((resolve) => server.once('listening', resolve));

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const tab = await browser.newPage({ viewport: { width: 800, height: 600 } });
tab.on('pageerror', (error) => console.error(error.message));
await tab.goto(`http://localhost:${server.address().port}/`);
await tab.waitForSelector('body[data-done]', { timeout: 300_000 });
await tab.screenshot({ path: join(ROOT, 'docs', 'scale-sheet.png'), fullPage: true });
for (const piece of pieces) {
  const url = await tab.evaluate((id) => window.thumbnail(id), piece.id);
  writeFileSync(join(ROOT, 'data', 'assets', 'furniture', piece.id, 'thumb.webp'),
    Buffer.from(url.split(',')[1], 'base64'));
  const file = join(ROOT, 'catalog', 'furniture', `${piece.id}.json`);
  const entry = JSON.parse(readFileSync(file, 'utf8'));
  const thumbnail = `/files/assets/furniture/${piece.id}/thumb.webp`;
  writeFileSync(file, `${JSON.stringify({ ...entry, thumbnail }, null, 2)}\n`);
}
await browser.close();
server.close();
console.log(`docs/scale-sheet.png: ${pieces.length} models in ${Object.keys(rows).length} rows; ${pieces.length} thumbnails`);
