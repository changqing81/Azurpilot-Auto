// OOBE 首装引导（远控首装场景；桌面已有实例时不触发——/newapi/oobe 判定）。
// 五步：欢迎 → 语言 → 服务器 → 模拟器 → 完成。语言/主题写 deploy settings（现成端点）；
// 服务器/模拟器步骤在首装场景走老 UI OOBE（远控 WebView 内），新 UI 引导页做轻量版。
import { useEffect, useState } from 'react'

const STEPS = ['欢迎', '语言', '服务器', '模拟器', '完成']
const LANGS = [
  { label: '简体中文', value: 'zh-CN' },
  { label: '繁體中文', value: 'zh-TW' },
  { label: 'English', value: 'en-US' },
  { label: '日本語', value: 'ja-JP' },
]

export function OobePage() {
  const [step, setStep] = useState(0)
  const [lang, setLang] = useState('zh-CN')
  const [server, setServer] = useState('cn')
  useEffect(() => {
    // 非 OOBE 场景直接跳主页
    fetch('/newapi/oobe').then(r => r.json()).then(j => {
      if (!j.oobe_needed) location.hash = '#/'
    }).catch(() => {})
  }, [])
  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 60 }}>
      <div className="st-tabs" style={{ marginBottom: 30 }}>
        {STEPS.map((s, i) => (
          <span key={s} className={'oobe-step' + (i === step ? ' st-tab-active' : '')} style={{ padding: '0 14px', opacity: i <= step ? 1 : 0.4 }}>
            {i + 1}. {s}
          </span>
        ))}
      </div>
      {step === 0 && (
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ fontSize: 28 }}>欢迎使用 AzurPilot</h1>
          <p style={{ opacity: 0.6, margin: '16px 0 30px' }}>点击下方开始初始设置</p>
          <button className="ov-btn-primary" onClick={() => setStep(1)}>开始</button>
        </div>
      )}
      {step === 1 && (
        <div style={{ textAlign: 'center' }}>
          <h2>Select your language / 选择语言</h2>
          <div className="home-btns" style={{ marginTop: 20 }}>
            {LANGS.map(l => (
              <button key={l.value} className={'log-btn' + (lang === l.value ? ' st-tab-active' : '')} onClick={() => setLang(l.value)}>
                {l.label}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 30 }}>
            <button className="log-btn" onClick={() => setStep(0)}>上一步</button>
            <button className="ov-btn-primary" style={{ marginLeft: 10 }} onClick={() => {
              fetch('/api/deploy/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ Language: lang }) })
                .then(() => setStep(2))
            }}>下一步</button>
          </div>
        </div>
      )}
      {step === 2 && (
        <div style={{ textAlign: 'center' }}>
          <h2>选择服务器</h2>
          <div className="home-btns" style={{ marginTop: 20 }}>
            {['cn', 'en', 'jp', 'tw'].map(s => (
              <button key={s} className={'log-btn' + (server === s ? ' st-tab-active' : '')} onClick={() => setServer(s)}>
                {s.toUpperCase()}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 30 }}>
            <button className="log-btn" onClick={() => setStep(1)}>上一步</button>
            <button className="ov-btn-primary" style={{ marginLeft: 10 }} onClick={() => setStep(3)}>下一步</button>
          </div>
        </div>
      )}
      {step === 3 && (
        <div style={{ textAlign: 'center' }}>
          <h2>模拟器设置</h2>
          <p style={{ opacity: 0.6, margin: '16px 0' }}>
            请在老 UI（设置页）完成模拟器路径与序列号配置，完成后点击完成。
          </p>
          <button className="ov-btn-primary" onClick={() => setStep(4)}>完成</button>
        </div>
      )}
      {step === 4 && (
        <div style={{ textAlign: 'center' }}>
          <h1>设置完成 🎉</h1>
          <p style={{ opacity: 0.6, margin: '16px 0 30px' }}>进入主页开始使用</p>
          <button className="ov-btn-primary" onClick={() => { location.hash = '#/' }}>进入主页</button>
        </div>
      )}
    </div>
  )
}
