// alas-utils.js 是无类型的老 UI 前端库（原样复用），这里给个宽松声明。
declare module '../public/alas-utils.js'

declare global {
  interface Window {
    alasToggleLivePreview?: (instance: string) => void
    alasStartLivePreview?: (instance: string, codec?: string) => void
    alasStopLivePreview?: () => void
    alasShowAnnouncement?: (
      title: string,
      content: string,
      id: number | null,
      url: string,
      force: boolean,
    ) => void
    alasHasBeenShown?: (id: number) => boolean
    alasMarkAnnouncementShown?: (id: number) => void
  }
}

export {}
