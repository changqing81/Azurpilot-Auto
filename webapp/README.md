# AzurPilot Webapp

这里保留的是 AzurPilot 的纯 Web 前端资源，不再包含桌面壳或桌面打包流程。

## 常用命令

```bash
pnpm install
pnpm run watch
pnpm run build
pnpm run typecheck
pnpm run lint
```

`packages/renderer` 是 Vue 3 + Vite 应用，通过 iframe 访问本地 WebUI，默认地址 `http://127.0.0.1:25548`（与 `WebuiPort` 默认值一致；实际端口以部署配置 `config/deploy.yaml` 为准）。如需覆盖地址，可设置环境变量 `VITE_WEBUI_URL`。
