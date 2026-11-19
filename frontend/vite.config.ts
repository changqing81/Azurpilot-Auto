import { createHash } from 'node:crypto'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** 主题 CSS 内容指纹，用作布局层 CSS 的缓存破坏参数。
 *
 * 取 assets/gui/css 下会影响布局的样式表内容做 sha1，取前 12 位。
 * 仓库外构建（如 CI）拿不到这些文件时回退到固定串，保证可复现。
 */
function layoutCssVersion(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  const cssDir = resolve(here, '..', 'assets', 'gui', 'css')
  const files = ['alas-pc.css', 'alas-mobile.css']
  const hash = createHash('sha1')
  let hashed = 0
  for (const f of files) {
    const p = resolve(cssDir, f)
    if (existsSync(p)) {
      hash.update(readFileSync(p))
      hashed++
    }
  }
  if (!hashed) return 'dev'
  return hash.digest('hex').slice(0, 12)
}

// 新 UI 前端。产物默认输出 dist/，由 module/webapi 挂到 /new/。
// dev 模式代理：/api 与 /assets 转发到老后端 (22267)，复用现有业务与主题 CSS。
export default defineConfig({
  plugins: [react()],
  // 布局层 CSS 缓存破坏参数：alas-mobile.css 内容更新后旧客户端仍会用
  // 缓存旧版（没有 ID 级折叠选择器），菜单折叠失效（2026-11-18 实测）。
  //
  // ⚠️ 这里原来是 Date.now()，每次构建都变 → 产出的 JS 字节不同 →
  //    frontend/dist 一旦入库（方案 A：随仓库分发，让启动器用户也能用新 UI），
  //    每次构建都会产生一个无意义的大 diff，且无法验证「产物是否与源码匹配」。
  //    改成「根据主题 CSS 文件内容算哈希」：
  //      · CSS 变了 → 版本号变 → 缓存照常失效（原功能不丢）
  //      · 源码没变 → 版本号不变 → 构建字节可复现（CI 能校验产物一致性）
  define: {
    __LAYOUT_CSS_V__: JSON.stringify(layoutCssVersion()),
  },
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
