import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * three's Basis Universal transcoder, served at `basis/`: the furniture
 * models' textures are KTX2 (scripts/prepare-models.mjs), and KTX2Loader
 * loads the transcoder by name from one directory, which hashed imports
 * would not give it.
 */
function basisTranscoder(): Plugin {
  const dir = join(dirname(createRequire(import.meta.url).resolve('three')), '..', 'examples', 'jsm', 'libs', 'basis');
  const files = ['basis_transcoder.js', 'basis_transcoder.wasm'];
  return {
    name: 'basis-transcoder',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = files.find((f) => req.url?.split('?')[0].endsWith(`/basis/${f}`));
        if (!name) return next();
        res.setHeader('Content-Type', name.endsWith('.wasm') ? 'application/wasm' : 'text/javascript');
        res.end(readFileSync(join(dir, name)));
      });
    },
    generateBundle() {
      for (const name of files) {
        this.emitFile({ type: 'asset', fileName: `basis/${name}`, source: readFileSync(join(dir, name)) });
      }
    },
  };
}

export default defineConfig(({ mode }) => ({
  // The GitHub Pages build is served from the repository's path.
  base: mode === 'pages' ? '/walkthrough/' : '/',
  plugins: [react(), basisTranscoder()],
  server: {
    port: 5173,
    // Serve generated scene files straight from the git-ignored data/ dir in dev.
    fs: { allow: ['..', '../..'] },
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
}));
