import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Backend serves everything under /api. Proxy it in dev so the React app
// can fetch relative paths just like the vanilla frontend does in prod.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
