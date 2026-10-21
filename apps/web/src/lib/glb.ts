/** Loading a rebuilt flat's .glb. */

/**
 * Fetch a .glb, reporting how much of it has arrived. Measured against the
 * length in its own header, not Content-Length: GitHub Pages gzips it, and
 * Content-Length is then the compressed size of a body that arrives inflated.
 */
export async function fetchGlb(
  url: string,
  onProgress: (fraction: number) => void,
): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok || !response.body) throw new Error(`${response.status} ${url}`);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // Bytes 8-11 of a .glb are its total length.
    if (!chunks.length && value.byteLength >= 12) {
      length = new DataView(value.buffer, value.byteOffset, 12).getUint32(8, true);
    }
    chunks.push(value);
    loaded += value.byteLength;
    if (length) onProgress(Math.min(1, loaded / length));
  }
  const buffer = new Uint8Array(loaded);
  let at = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, at);
    at += chunk.byteLength;
  }
  return buffer.buffer;
}
