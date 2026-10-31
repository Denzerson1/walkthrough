// Turns each catalog furniture piece's source model into the two files the
// viewer loads, after checking it is the size of the real product.
//
//   node scripts/prepare-models.mjs [id ...]      (all pieces with a source model if none given)
//
// For each catalog/furniture/<id>.json with `glbPhone` (and `source`, the
// original GLB's URL, cached under data/models/source/):
//   - scale check (scale-check.mjs): the model's box against `dimensionsM`,
//     the real product's [width, height, depth]; only a uniform unit fix is
//     allowed, never a stretch; and the size against catalog/sizes.json;
//   - metres, Y up, pivot at the bottom centre; the front faces +Z (a model
//     turned a quarter fails the check, it is not guessed);
//   - desktop.glb: textures at most 2048 px, phone.glb: at most 1024 px
//     (normal maps 512) and 50k triangles; both with KTX2 textures (ETC1S colour, UASTC normals)
//     and meshopt-compressed geometry;
// into data/assets/furniture/<id>/ (git-ignored), and writes what it measured
// (`measuredM`, `models`) back into the catalog entry. Exits 1 if any piece
// fails, naming it and why.

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { Logger, NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression } from '@gltf-transform/extensions';
import {
  center, dedup, flatten, getBounds, join as joinMeshes, prune, quantize, reorder, simplify,
  textureCompress, weld,
} from '@gltf-transform/functions';
import { ktx2 } from 'ktx2-encoder/gltf-transform';
import { MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import sharp from 'sharp';

import { fitScale, saneSize } from './scale-check.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CATALOG = join(ROOT, 'catalog', 'furniture');
const SOURCES = join(ROOT, 'data', 'models', 'source');
const OUT = join(ROOT, 'data', 'assets', 'furniture');
const RANGES = JSON.parse(readFileSync(join(ROOT, 'catalog', 'sizes.json'), 'utf8'));

const VERSIONS = {
  desktop: { maxTexture: 2048, maxNormal: 2048, maxTriangles: Infinity },
  // A normal map is the largest file on a phone (UASTC) and the least seen
  // at a phone's size: at 1024 px, five pieces came to 1.6-1.7 MB.
  phone: { maxTexture: 1024, maxNormal: 512, maxTriangles: 50_000 },
};
/** What a phone file should stay under; a larger one is reported, not failed. */
const PHONE_BYTES = 1.5e6;

const io = new NodeIO()
  .setLogger(new Logger(Logger.Verbosity.WARN))
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder });

async function imageDecoder(buffer) {
  const { data, info } = await sharp(buffer).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data: new Uint8Array(data), width: info.width, height: info.height };
}

function triangles(doc) {
  return doc.getRoot().listMeshes().flatMap((m) => m.listPrimitives())
    .reduce((n, p) => n + (p.getIndices() ?? p.getAttribute('POSITION')).getCount() / 3, 0);
}

function boxOf(doc) {
  const { min, max } = getBounds(doc.getRoot().listScenes()[0]);
  return [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
}

async function source(entry) {
  const file = join(SOURCES, basename(new URL(entry.source).pathname));
  if (!existsSync(file)) {
    mkdirSync(SOURCES, { recursive: true });
    const response = await fetch(entry.source);
    if (!response.ok) throw new Error(`download failed: ${response.status} ${entry.source}`);
    writeFileSync(file, Buffer.from(await response.arrayBuffer()));
  }
  return file;
}

/** One version of a piece: scaled (factor), centred, cut down, compressed. */
async function build(file, factor, { maxTexture, maxNormal, maxTriangles }) {
  const doc = await io.read(file);
  await doc.transform(dedup(), prune(), flatten(), joinMeshes(), weld());
  if (factor !== 1) {
    for (const node of doc.getRoot().listScenes()[0].listChildren()) {
      node.setScale(node.getScale().map((s) => s * factor));
      node.setTranslation(node.getTranslation().map((t) => t * factor));
    }
  }
  await doc.transform(center({ pivot: 'below' }));
  const count = triangles(doc);
  if (count > maxTriangles) {
    await MeshoptSimplifier.ready;
    await doc.transform(simplify({ simplifier: MeshoptSimplifier, ratio: maxTriangles / count, error: 0.002 }));
  }
  await doc.transform(
    textureCompress({ encoder: sharp, slots: /^normalTexture$/, resize: [maxNormal, maxNormal] }),
    textureCompress({ encoder: sharp, resize: [maxTexture, maxTexture] }),
    ktx2({ slots: /^(baseColorTexture|emissiveTexture)$/, isUASTC: false, qualityLevel: 160, enableDebug: false, generateMipmap: true, isPerceptual: true, isSetKTX2SRGBTransferFunc: true, imageDecoder }),
    ktx2({ slots: /^normalTexture$/, isUASTC: true, needSupercompression: true, isNormalMap: true, enableDebug: false, generateMipmap: true, isPerceptual: false, imageDecoder }),
    ktx2({ isUASTC: false, qualityLevel: 160, enableDebug: false, generateMipmap: true, isPerceptual: false, imageDecoder }),
    reorder({ encoder: MeshoptEncoder }),
    quantize(),
    prune(),
  );
  doc.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  return { doc, triangles: triangles(doc) };
}

async function prepare(entry) {
  const file = await source(entry);
  const original = await io.read(file);
  const box = boxOf(original);
  const flat = Boolean(RANGES[entry.category]?.flat);
  const fit = fitScale(box, entry.dimensionsM, { flat });
  if (!fit.ok) return { ok: false, reason: `scale: ${fit.reason} (model ${box.map((v) => v.toFixed(3)).join(' x ')} m)` };
  const size = box.map((v) => v * fit.factor);
  const sane = saneSize(size, entry.category, RANGES);
  if (!sane.ok) return { ok: false, reason: `size: ${sane.reason}` };
  await MeshoptEncoder.ready;
  const dir = join(OUT, entry.id);
  mkdirSync(dir, { recursive: true });
  const models = {};
  for (const [name, limits] of Object.entries(VERSIONS)) {
    const { doc, triangles: count } = await build(file, fit.factor, limits);
    const bytes = await io.writeBinary(doc);
    writeFileSync(join(dir, `${name}.glb`), bytes);
    models[name] = { bytes: bytes.byteLength, triangles: count };
  }
  return { ok: true, size, unit: fit.unit, models };
}

const wanted = process.argv.slice(2);
const entries = readdirSync(CATALOG).filter((f) => f.endsWith('.json'))
  .map((f) => ({ path: join(CATALOG, f), entry: JSON.parse(readFileSync(join(CATALOG, f), 'utf8')) }))
  .filter(({ entry }) => entry.glbPhone && entry.source && (!wanted.length || wanted.includes(entry.id)));

let failed = 0;
for (const { path, entry } of entries) {
  const result = await prepare(entry).catch((error) => ({ ok: false, reason: error.message }));
  if (!result.ok) {
    failed += 1;
    console.log(`FAIL ${entry.id}: ${result.reason}`);
    continue;
  }
  const { phone, desktop } = result.models;
  const heavy = phone.bytes > PHONE_BYTES ? '  (phone file over 1.5 MB)' : '';
  console.log(`ok   ${entry.id}: ${result.size.map((v) => v.toFixed(2)).join(' x ')} m, `
    + `phone ${(phone.bytes / 1e6).toFixed(2)} MB ${phone.triangles} tris, `
    + `desktop ${(desktop.bytes / 1e6).toFixed(2)} MB${heavy}`);
  writeFileSync(path, `${JSON.stringify({
    ...entry, measuredM: result.size.map((v) => Number(v.toFixed(3))), models: result.models,
  }, null, 2)}\n`);
}
console.log(`${entries.length - failed} of ${entries.length} prepared`);
process.exit(failed ? 1 : 0);
