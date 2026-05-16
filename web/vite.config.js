import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built bundle is mounted by FastAPI at "/" via StaticFiles.
// During `npm run dev`, proxy API routes to the local mimic-tts server so
// you can iterate without rebuilding the image.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/health': 'http://localhost:8000',
      '/voices': 'http://localhost:8000',
      '/tts': 'http://localhost:8000',
      '/clone': 'http://localhost:8000',
      '/v1': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
