# Deploy

本目录存放 AzurPilot 的安装与部署脚本。

## 源码安装（Windows / Linux 通用）

在仓库根目录运行：

```shell
python -m deploy.installer
```

该入口用 `uv` 初始化项目本地 `.venv`，并按 `pyproject.toml` + `uv.lock` 同步依赖，
不会向系统 Python 环境安装任何包。

## Linux / Docker 一键部署

`deploy/docker/deploy-image.sh` 是完整的 Docker 部署向导：自动安装 Docker、克隆本仓库、
拉取 `ghcr.io/changqing81/azurpilot-auto` 镜像并启动容器，最后打印访问地址与 WebUI 密码。

```shell
sudo -E bash deploy/docker/deploy-image.sh
```

默认值可用环境变量覆盖：`REPOSITORY`、`IMAGE`、`APP_DIR`（默认 `~/AP`）、`WEBUI_PORT`、`LANGUAGE`。
中国大陆网络可把源码源指向 GitCode 镜像：

```shell
sudo -E env REPOSITORY=https://gitcode.com/gcw_BYvq9jGu/AzurPilot bash deploy/docker/deploy-image.sh
```

## Launcher

启动器 `AzurPilot.exe` 由 `.bat` 经 [Bat To Exe Converter](https://f2ko.de/programme/bat-to-exe-converter/) 转换而来。

若杀毒软件报警，可用 `deploy/launcher/Alas.bat` 替代 `AzurPilot.exe`，两者行为一致。
