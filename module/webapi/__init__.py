"""新 UI 薄 API 层（new-ui 分支）。

只做"现成业务函数 → REST/WS"的包装，不复制业务逻辑。
挂载进现有 Starlette 应用（module/webui/app.py 的 asgi_app），与 PyWebIO 并行。
架构与规矩见 docs/new-ui/README.md。
"""
