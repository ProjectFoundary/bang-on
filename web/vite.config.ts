import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
export default defineConfig({ plugins: [svelte()], base: './', build: { outDir: 'dist', emptyOutDir: true,
  rollupOptions: { output: { manualChunks(id) {
    if (id.includes('/node_modules/')) return 'vendor';
    if (id.includes('/src/squib/')) return 'simulation';
  } } } },
  server: { port: 5178, proxy: { '/api': 'http://127.0.0.1:8765' } } });
