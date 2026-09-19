import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({mode}) => {
  const backend = mode === 'mock' ? `http://127.0.0.1:${process.env.AZURPILOT_MOCK_PORT ?? 22392}` : process.env.AZURPILOT_BACKEND ?? 'http://127.0.0.1:22267'
  return {
    // 相对 base：index.html 由后端注入 <base>，本地与 P2P 远控（/p2p/{id}/ 前缀）
    // 下都能正确解析构建资源；不能用绝对 '/'，否则远控下资源丢前缀导致白屏。
    base: './',
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
