"""提供完整前端构建目录，区分页面导航与静态资源请求。"""
from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


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
            response = await super().get_response('index.html', scope)
        # public 资源名称不带内容哈希，更新后必须向服务端重新验证缓存。
        response.headers['Cache-Control'] = 'no-cache'
        return response
