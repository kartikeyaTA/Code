import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Forward HTTP API requests to FastAPI
      '/api': {
        target: 'http://127.0.0.1:5500',
        changeOrigin: true,
      },
      // Forward Auth requests to FastAPI
      '/auth': {
        target: 'http://127.0.0.1:5500',
        changeOrigin: true,
      },
      // Forward WebSocket connections to FastAPI
      '/ws': {
        target: 'ws://127.0.0.1:5500',
        ws: true,
      },
    },
  },
})
