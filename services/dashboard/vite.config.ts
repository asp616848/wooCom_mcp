/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

// `npm run dev` proxies /api to the gateway running in k3d (same-origin, like the Ingress in the cluster).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    proxy: { '/api': { target: 'http://dashboard.localhost', changeOrigin: true } },
  },
  test: { environment: 'node', include: ['src/**/*.test.{ts,tsx}'] },
});
