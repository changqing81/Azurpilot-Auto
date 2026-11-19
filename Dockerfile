# docker build -t hgjazhgj/alas:latest -f deploy/docker/Dockerfile .
# docker run -v ${PWD}:/app/AzurPilot -p 25548:25548 --name AzurPilot -it --rm hgjazhgj/alas

# ============================================================
# 阶段 1：前端预构建（node 工具链只存在于这个 stage，不进最终镜像）
# ============================================================
# 为什么放 /opt 而不是仓库目录：运行时 docker-compose 用
#   '.:/app/AzurPilot' 把整个仓库目录盖掉，镜像里的
#   /app/AzurPilot/frontend/dist 会消失。
# Python 侧（module/webapi/app.py:_DIST_CANDIDATES）按优先级取：
#   1) 仓库内 frontend/dist（宿主机自己 pnpm build 的 → 优先）
#   2) ALAS_FRONTEND_DIST 环境变量（自定义覆盖）
#   3) /opt/alas-frontend/dist（本镜像预构建的兜底）
FROM node:22-bookworm-slim AS frontend

WORKDIR /build/frontend

# 先只拷依赖清单，让 pnpm install 这层能被缓存复用：
# 改 React 源码不应触发重新装依赖（装依赖是整条链路最慢的一步）。
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./

# corepack 按 lockfile 取 pnpm，避免不同大版本行为差异。
# --frozen-lockfile：lock 与 package.json 不一致时直接失败，
# 防止镜像里出现与仓库不同的依赖组合。
RUN corepack enable && \
    corepack prepare pnpm@10 --activate && \
    pnpm install --frozen-lockfile

# 再拷构建所需源码：tsconfig / vite.config / index.html / src 缺一不可。
COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src

# tsc -b + vite build（见 frontend/package.json 的 build 脚本）
RUN pnpm run build

# ============================================================
# 阶段 2：运行时镜像
# ============================================================
FROM python:3.14-slim-bookworm

ARG UV_INDEX_URL=https://pypi.org/simple
ARG UV_EXTRA_INDEX_URL=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=127.0.0.1,localhost

ENV UV_INDEX_URL=${UV_INDEX_URL}
ENV UV_EXTRA_INDEX_URL=${UV_EXTRA_INDEX_URL}
ENV http_proxy=${HTTP_PROXY}
ENV https_proxy=${HTTPS_PROXY}
ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV no_proxy=${NO_PROXY}
ENV NO_PROXY=${NO_PROXY}
ENV PATH=/app/AzurPilot/.venv/bin:${PATH}

WORKDIR /app/AzurPilot

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./

RUN if [ -n "$HTTP_PROXY" ]; then \
    printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$HTTP_PROXY" "$HTTPS_PROXY" > /etc/apt/apt.conf.d/99proxy; \
    fi

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    adb \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    openssh-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*' && \
    if [ -n "$HTTP_PROXY" ]; then \
        git config --global http.proxy "$HTTP_PROXY" && \
        git config --global https.proxy "$HTTPS_PROXY"; \
    fi

RUN uv venv --relocatable --python /usr/local/bin/python .venv && \
    uv sync --no-dev --no-install-project && \
    cp /usr/local/bin/uv .venv/bin/uv && \
    cp /usr/bin/adb .venv/bin/adb && \
    cp /usr/bin/git .venv/bin/git && \
    rm -rf /root/.cache/uv

COPY . .

# 把前端产物搬到**挂载路径之外**（见文件头说明）。
# --from=frontend 指向阶段 1，dist 由 vite build 生成。
COPY --from=frontend /build/frontend/dist /opt/alas-frontend/dist

CMD [".venv/bin/python", "gui.py"]
