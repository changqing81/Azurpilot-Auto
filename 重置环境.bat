@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

echo ============================================================
echo   AzurPilot 环境重置
echo ============================================================
echo.
echo 本操作会删除 .venv 以及仓库里除基础文件之外的内容，
echo 下次启动需要重新下载 Python 和全部依赖（可能几分钟到几十分钟）。
echo.
echo 保留：config / deploy / log / bootstrap / alas-launcher.exe / 本脚本
echo 注意：.git 也会被删除，本地未推送的提交会丢失，请先确认已推送。
echo.

set "PYEXE="

if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if defined PYEXE goto :run

where py >nul 2>&1
if %errorlevel%==0 set "PYEXE=py -3"
if defined PYEXE goto :run

where python >nul 2>&1
if %errorlevel%==0 set "PYEXE=python"
if defined PYEXE goto :run

where uv >nul 2>&1
if %errorlevel%==0 set "PYEXE=uv run --python 3.12 python"
if defined PYEXE goto :run

echo [!] 没有找到可用的 Python 解释器，无法执行重置。
echo.
echo 请改用下面任意一种方式：
echo   1. 打开 alas-launcher.exe，在启动画面上选「重置环境后退出」
echo   2. 先运行 一键启动.bat 让环境恢复，再运行本脚本
echo.
pause
exit /b 1

:run
%PYEXE% "deploy\reset\reset_env.py" %*
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
