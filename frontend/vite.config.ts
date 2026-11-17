import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 新 UI 前端。产物默认输出 dist/，由 module/webapi 挂到 /new/。
// dev 模式代理：/api 与 /assets 转发到老后端 (22267)，复用现有业务与主题 CSS。
export default defineConfig({
  plugins: [react()],
  // base 用 '/'（绝对根路径）。**不能**用 './'：
  // 远控隧道把页面挂在 /p2p/<peer_id>/ 下（remote_access.py:_normalize_proxy_path
  // 剥掉该前缀后转发），相对路径 './assets/x.js' 会被浏览器解析成
  // '/p2p/<id>/assets/x.js' 再请求，而服务端 SPA fallback 只挂在 / 与 /new/，
  // 子路径一律 404 → 主 JS 拿不到 → React 不挂载 → 页面永远卡在骨架屏
  // （长卿 2026-11-17：远控端「正在加载 AzurPilot...」出不来，老 UI 正常）。
  // 绝对 '/' 在根挂载、/new/ 挂载、隧道剥前缀三种情形下都命中。
  base: '/',
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:22267', changeOrigin: true },
      '/new-assets/gui': {
        target: 'http://127.0.0.1:22267',
        changeOrigin: true,
        rewrite: p => p.replace('/new-assets', '/assets')
      },
      '/assets/gui': { target: 'http://127.0.0.1:22267', changeOrigin: true },
      '/static/assets': { target: 'http://127.0.0.1:22267', changeOrigin: true }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: { output: { manualChunks: { vendor: ['react', 'react-dom', 'react-router-dom'] } } }
  }
})
