import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({mode}) => {
  const backend = mode === 'mock' ? `http://127.0.0.1:${process.env.AZURPILOT_MOCK_PORT ?? 22392}` : process.env.AZURPILOT_BACKEND ?? 'http://127.0.0.1:22267'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target: backend, ws: true },
        '/healthz': { target: backend },
      },
    },
    build: { sourcemap: false },
  }
})
