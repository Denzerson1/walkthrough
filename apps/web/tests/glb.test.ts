import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchGlb } from '../src/lib/glb';

/** A .glb-shaped body: the 12-byte header naming its length, then filler. */
function glb(length: number): Uint8Array {
  const bytes = new Uint8Array(length).map((_, i) => i % 251);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, length, true);
  return bytes;
}

/** Serve `bytes` in chunks, with a Content-Length that is not the body's, as gzip makes it. */
function serve(bytes: Uint8Array, chunk: number, contentLength: number) {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let at = 0; at < bytes.length; at += chunk) controller.enqueue(bytes.slice(at, at + chunk));
      controller.close();
    },
  });
  const response = new Response(stream, { headers: { 'Content-Length': String(contentLength) } });
  vi.stubGlobal('fetch', vi.fn(async () => response));
}

describe('fetchGlb', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('measures progress by the length in the header, whatever the server says', async () => {
    const bytes = glb(10_000);
    serve(bytes, 1_000, 6_000);
    const seen: number[] = [];
    const buffer = await fetchGlb('/flat.glb', (f) => seen.push(f));
    expect(new Uint8Array(buffer)).toEqual(bytes);
    expect(seen).toHaveLength(10);
    expect(seen[0]).toBeCloseTo(0.1);
    expect(seen.at(-1)).toBe(1);
    expect(seen.every((f, i) => i === 0 || f > seen[i - 1])).toBe(true);
  });

  it('fails on a missing file rather than parsing an error page', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('Not found', { status: 404 })));
    await expect(fetchGlb('/missing.glb', () => undefined)).rejects.toThrow('404');
  });
});
