"""提供完整前端构建目录，区分页面导航与静态资源请求。"""
from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

# P2P 远控下页面挂在 /p2p/{id}/ 前缀后，index.html 必须带 <base> 才能解析
# 相对资源路径；本地直连由 app.py 的 index() 注入 <base href="/">。
BASE_TAG = b'<head><base href="/">'
NO_CACHE = {'Cache-Control': 'no-cache'}


def with_base_tag(html: bytes) -> Response:
    html = html.replace(b'<head>', BASE_TAG, 1)
    return Response(html, media_type='text/html', headers=NO_CACHE)


class FrontendFiles(StaticFiles):
    """保留 SPA 页面回退，同时让缺失资源返回真实的 404。"""

    async def get_response(self, path, scope):
        # 构建指纹等内部文件不属于公开资源；路径越界仍由 StaticFiles 拦截。
        if any(part.startswith('.') and part not in ('.', '..') for part in PurePosixPath(path).parts):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or PurePosixPath(path).suffix:
                raise
            # SPA 回退：深路径刷新返回 index.html，并注入 <base> 供相对资源解析
            index = self.directory / 'index.html'
            if index.is_file():
                return with_base_tag(index.read_bytes())
            raise
        # public 资源名称不带内容哈希，更新后必须向服务端重新验证缓存。
        response.headers['Cache-Control'] = 'no-cache'
        return response
