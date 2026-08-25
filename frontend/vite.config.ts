import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://localhost:9000/asr-media',
        rewrite: (path) => path.replace(/^\/media/, ''),
      },
      '/grafana': {
        target: 'http://localhost:3002',
        rewrite: (path) => path.replace(/^\/grafana/, ''),
      },
    },
  },
})
