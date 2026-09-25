import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import tailwindcss from '@tailwindcss/vite';

const api = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  server: {
    proxy: { '/api': api, '/thumbs': api, '/previews': api },
  },
});
