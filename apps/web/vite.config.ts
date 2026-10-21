import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  // The GitHub Pages build is served from the repository's path.
  base: mode === 'pages' ? '/walkthrough/' : '/',
  plugins: [react()],
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
