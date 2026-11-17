import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 新 UI 前端。产物默认输出 dist/，由 module/webapi 挂到 /new/。
// dev 模式代理：/api 与 /assets 转发到老后端 (22267)，复用现有业务与主题 CSS。
export default defineConfig({
  plugins: [react()],
  // base 用 './'（相对路径）—— 这是远控端能正常加载的**关键**。
  //
  // 远控中继（remurl.a1.nanoda.work）把每个客户端挂在 /{peer_id} 下，
  // 且**只认带 peer 前缀的路径**，不带前缀一律 502。实测：
  //   https://remurl…/89982203008c27b2?app=index        → 200 页面
  //   https://remurl…/assets/index-xxx.js                 → 502
  //   https://remurl…/89982203008c27b2/assets/index-xxx.js → 200 真实 JS
  // 用绝对 '/' 的话浏览器请求站点根 /assets/… → 502 → 主 JS 拿不到
  // → React 不挂载 → 页面永远停在骨架屏。长卿 2026-11-17 反馈，且
  // 「其他人的远控也是这样」—— 那是中继的通用行为，不是个案。
  //
  // 相对 './' 在 /{peer_id} 页面里解析成 /{peer_id}/assets/… → 命中中继；
  // 根挂载（/）与 /new/ 挂载下同样正确。
  // 服务端配套见 module/webapi/app.py:SpaStaticFiles（剥前缀 + SPA 回退）。
  base: './',
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
