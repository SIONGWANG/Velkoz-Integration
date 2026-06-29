@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================
::  开发者工具 — 内嵌 Python 环境初始化
::  下载 Python 3.11.9 embeddable + 安装 pip + 安装依赖
::  运行此脚本后，python\ 目录可直接打包分发
:: ============================================================

set "PYTHON_DIR=python"
set "PYTHON_VERSION=3.11.9"
set "PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip"
set "DOWNLOAD_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_ZIP%"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"

:: 清理旧目录
if exist "%PYTHON_DIR%" (
    echo [1/5] 清理旧的 Python 目录...
    rmdir /s /q "%PYTHON_DIR%"
)

:: 下载 Python embeddable
echo [1/5] 下载 Python %PYTHON_VERSION% embeddable...
if not exist "%PYTHON_ZIP%" (
    powershell -Command "Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile '%PYTHON_ZIP%'"
    if errorlevel 1 (
        echo 下载失败！请检查网络连接。
        exit /b 1
    )
) else (
    echo   已存在下载文件，跳过下载。
)

:: 解压
echo [2/5] 解压到 %PYTHON_DIR%\...
powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if errorlevel 1 (
    echo 解压失败！
    exit /b 1
)

:: 修改 ._pth 文件，启用 site-packages
echo [3/5] 配置 Python 路径...
set "PTH_FILE="
for %%f in (%PYTHON_DIR%\python*._pth) do set "PTH_FILE=%%f"
if defined PTH_FILE (
    powershell -Command "(Get-Content '%PTH_FILE%') -replace '#import site', 'import site' | Set-Content '%PTH_FILE%'"
    echo   已启用 site-packages: %PTH_FILE%
) else (
    echo   警告：未找到 ._pth 文件
)

:: 安装 pip
echo [4/5] 安装 pip...
powershell -Command "Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%PYTHON_DIR%\get-pip.py'"
if errorlevel 1 (
    echo 下载 get-pip.py 失败！请检查网络连接。
    exit /b 1
)
"%PYTHON_DIR%\python.exe" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location
if errorlevel 1 (
    echo pip 安装失败！
    exit /b 1
)
del "%PYTHON_DIR%\get-pip.py"

:: 安装依赖
echo [5/5] 安装项目依赖...
if exist "requirements.txt" (
    "%PYTHON_DIR%\python.exe" -m pip install -r requirements.txt --no-warn-script-location
    if errorlevel 1 (
        echo 依赖安装失败！请检查 requirements.txt。
        exit /b 1
    )
) else (
    echo 警告：requirements.txt 不存在，跳过依赖安装。
)

:: 清理下载的 zip
if exist "%PYTHON_ZIP%" del "%PYTHON_ZIP%"

echo.
echo  ========================================
echo   初始化完成！
echo   Python 路径: %PYTHON_DIR%\python.exe
echo   现在可以运行 "启动.bat" 启动程序。
echo  ========================================
