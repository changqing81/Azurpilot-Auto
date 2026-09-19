import { useState } from 'react'

/** 背景在当前页面会话中只加载一次，图片服务不可用时保留渐变底色。 */
export function Wallpaper() {
  const [failed, setFailed] = useState(false)
  return <div className="wallpaper" aria-hidden="true">
    {!failed && <img src="https://api.yppp.net/api.php" alt="" referrerPolicy="no-referrer"
      decoding="async" onError={() => setFailed(true)}/>}
  </div>
}
