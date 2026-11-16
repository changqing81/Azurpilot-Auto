import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 新 UI 前端。产物默认输出 dist/，由 module/webapi 挂到 /new/。
// dev 模式代理：/api 与 /assets 转发到老后端 (22267)，复用现有业务与主题 CSS。
export default defineConfig({
  plugins: [react()],
  // base 用 './'：同一份 dist 既要能挂在 /new/（与老 UI 并存）又要能挂在 /（UI=new
  // 时新 UI 独占），绝对前缀 '/new/' 会在根挂载时让所有资源 404。
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
