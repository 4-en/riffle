import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';

const api = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  // The bundled country outlines (~760 kB, loaded only with the map) are expected to be large.
  // The built UI goes into the Python package, so every install (and a future binary) carries it.
  build: { outDir: '../src/riffle/web', emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: {
    proxy: { '/api': api, '/thumbs': api, '/previews': api },
  },
});
