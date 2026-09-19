import { useEffect, useState } from 'react'
import { assetUrl } from '../api/client'

const SPA_BACKGROUNDS = ['screen1.jpg', 'screen2.jpg', 'screen3.jpg', 'screen4.png']

type CustomBackground = {url: string; video: boolean} | null

/**
 * 背景三层兜底：wallpapers/custom_background.*（用户自定义，图片或视频）→
 * assets/spa 占位图按日期轮换 → 随机网络图；全部失败时保留渐变底色。
 * 自定义背景由后端 /api/background/custom 提供。
 */
export function Wallpaper() {
  const [custom, setCustom] = useState<CustomBackground>(null)
  const [customChecked, setCustomChecked] = useState(false)
  const [spaFailed, setSpaFailed] = useState(false)
  const [netFailed, setNetFailed] = useState(false)
  useEffect(() => {
    let active = true
    fetch(assetUrl('api/background/custom'), {method: 'HEAD'}).then(res => {
      if (!active) return
      const type = res.headers.get('content-type') ?? ''
      if (res.ok && type.startsWith('video/')) setCustom({url: res.url, video: true})
      else if (res.ok && type.startsWith('image/')) setCustom({url: res.url, video: false})
      setCustomChecked(true)
    }).catch(() => { if (active) setCustomChecked(true) })
    return () => {active = false}
  }, [])
  const spa = SPA_BACKGROUNDS[new Date().getDate() % SPA_BACKGROUNDS.length]
  const showCustom = customChecked && custom
  const showSpa = customChecked && !custom && !spaFailed
  const showNet = customChecked && !custom && spaFailed && !netFailed
  return <div className="wallpaper" aria-hidden="true">
    {showCustom && (custom!.video
      ? <video src={custom!.url} autoPlay muted loop playsInline/>
      : <img src={custom!.url} alt="" decoding="async"/>)}
    {showSpa && <img src={assetUrl(`static/assets/spa/${spa}`)} alt="" decoding="async" onError={() => setSpaFailed(true)}/>}
    {showNet && <img src="https://api.yppp.net/api.php" alt="" referrerPolicy="no-referrer"
      decoding="async" onError={() => setNetFailed(true)}/>}
  </div>
}
