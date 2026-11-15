// 主页 HomePage（对照 app_home.py show_home:395-494）。
// 语言选择 4 按钮 + 主题选择 5 按钮 + 下载当前背景图 + 背景管理 4 按钮 + 免责声明。
// 壁纸背景（--alas-apple-bg-image CSS 变量）与图源竞赛（alasWallpaperRace）沿用 assets/gui/js/alas-utils.js 现成实现。
import { useNavigate } from 'react-router-dom'
import { Shell } from '../../components/Shell'

const LANGS = ['简体中文', '繁體中文', 'English', '日本語']
const THEMES = [
  { name: 'Light', value: 'light' },
  { name: 'Dark', value: 'dark' },
  { name: '高级材质', value: 'advanced_material' },
  { name: '高级材质（暗色）', value: 'dark_advanced_material' },
  { name: '透明', value: 'transparent' },
]

export function HomePageMain() {
  const navigate = useNavigate()
  return (
    <Shell active="home">
      <div id="pywebio-scope-content" className="home-page">
      <div className="home-section">
        <div className="home-title">Select your language / 选择语言</div>
        <div className="home-btns">
          {LANGS.map(l => <button key={l} className="log-btn-primary">{l}</button>)}
        </div>
      </div>
      <div className="home-section">
        <div className="home-title">Change theme / 更改主题</div>
        <div className="home-btns">
          {THEMES.map(t => <button key={t.value} className="log-btn">{t.name}</button>)}
        </div>
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <button className="log-btn">下载当前背景图</button>
        </div>
      </div>
      <div className="home-section">
        <div className="home-title">Background / 背景</div>
        <div className="home-btns">
          <button className="log-btn">上传自定义背景</button>
          <button className="log-btn-primary">随机背景</button>
          <button className="log-btn">管理图源</button>
          <button className="log-btn">媒体类型</button>
        </div>
      </div>
      <hr className="hr-group" />
      <p style={{ textAlign: 'center', fontSize: 14, padding: '16px 12%' }}>
        AzurPilot 是基于上游项目 Alas (AzurLaneAutoScript) 的修改版本，采用 GPL-3.0
        许可证，免费开源。如果你在任何渠道付费购买，那你一定是个大傻逼，请申请退款。
      </p>
      <div className="alas-home-marker" aria-hidden="true" />
    </div>
    </Shell>
  )
}
