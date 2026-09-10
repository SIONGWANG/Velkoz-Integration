@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================
::  开发者工具 — 一键打包 Vel'Koz V1.21.0
::  生成 Vel'Koz_V1.21.0.zip 发布包
:: ============================================================

set "VERSION=1.21.0"
set "OUTPUT_NAME=Vel'Koz_V%VERSION%"
set "STAGE_DIR=_package_staging"

:: 检查内嵌 Python 是否就绪
if not exist "python\python.exe" (
    echo 错误：内嵌 Python 环境不存在。
    echo 请先运行 setup_embedded_python.bat 初始化环境。
    pause
    exit /b 1
)

:: 清理暂存目录
echo [1/4] 准备打包目录...
if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"
mkdir "%STAGE_DIR%\%OUTPUT_NAME%"

:: 复制核心代码
echo [2/4] 复制文件...
set "TARGET=%STAGE_DIR%\%OUTPUT_NAME%"

:: Python 源码
copy /y "app.py" "%TARGET%\" >nul
copy /y "css_styles.py" "%TARGET%\" >nul
copy /y "data_mixin.py" "%TARGET%\" >nul
copy /y "settings_mixin.py" "%TARGET%\" >nul
copy /y "export_mixin.py" "%TARGET%\" >nul
copy /y "zone_a.py" "%TARGET%\" >nul
copy /y "zone_b.py" "%TARGET%\" >nul
copy /y "zone_c.py" "%TARGET%\" >nul
copy /y "disk_io.py" "%TARGET%\" >nul
copy /y "utils.py" "%TARGET%\" >nul
copy /y "export_utils.py" "%TARGET%\" >nul
copy /y "easter_eggs.py" "%TARGET%\" >nul

:: viewer 包（ImageDock 悬浮窗，进程内托管）
xcopy /y /e /i "viewer" "%TARGET%\viewer" >nul

:: 配置文件
copy /y "categories_config.json" "%TARGET%\" >nul
copy /y "scan_rules.json" "%TARGET%\" >nul
xcopy /y /e /i "config" "%TARGET%\config" >nul

:: 启动脚本
copy /y "启动.bat" "%TARGET%\" >nul
copy /y "setup_embedded_python.bat" "%TARGET%\" >nul
copy /y "requirements.txt" "%TARGET%\" >nul

:: 文档
copy /y "用户操作手册_v1.3.0.html" "%TARGET%\" >nul

:: 内嵌 Python 环境
echo [3/4] 复制内嵌 Python 环境...
xcopy /y /e /i "python" "%TARGET%\python" >nul
:: 删除 pip 缓存以减小体积
if exist "%TARGET%\python\Scripts" (
    for %%f in ("%TARGET%\python\Scripts\pip*.exe") do (
        rem 保留 pip 相关文件
    )
)
if exist "%TARGET%\python\Lib\site-packages\pip" (
    rem 保留 pip（用户可能需要安装额外依赖）
)

:: 打包 ZIP
echo [4/4] 生成 ZIP 文件...
if exist "%OUTPUT_NAME%.zip" del "%OUTPUT_NAME%.zip"
powershell -Command "Compress-Archive -Path '%STAGE_DIR%\*' -DestinationPath '%OUTPUT_NAME%.zip' -Force"

:: 清理暂存目录
rmdir /s /q "%STAGE_DIR%"

:: 输出结果
echo.
if exist "%OUTPUT_NAME%.zip" (
    for %%A in ("%OUTPUT_NAME%.zip") do (
        echo  ========================================
        echo   打包完成！
        echo   文件: %OUTPUT_NAME%.zip
        echo   大小: %%~zA 字节
        echo  ========================================
    )
) else (
    echo  打包失败！请检查错误信息。
)
pause
