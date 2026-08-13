@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

:: ============================================================
::  Vel'Koz V1.12.1 — 审视之眼 Pro 绿色版启动脚本
::  首次运行自动初始化内嵌 Python 环境，无需手动安装
:: ============================================================

:: 检查内嵌 Python 是否已就绪
if not exist "python\python.exe" (
    echo.
    echo  [Vel'Koz V1.12.1] 首次启动，正在初始化 Python 环境...
    echo  请稍候，这可能需要几分钟（取决于网络速度）...
    echo.
    call setup_embedded_python.bat
    if errorlevel 1 (
        echo.
        echo  ========================================
        echo   初始化失败！请检查网络连接后重试。
        echo  ========================================
        pause
        exit /b 1
    )
)

:: 自动寻找可用端口
set "PORT=8502"
:find_port
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    set /a PORT+=1
    goto find_port
)

echo.
echo  ========================================
echo   Vel'Koz V1.12.1 — 审视之眼 Pro
echo  ========================================
echo   访问地址: http://localhost:%PORT%
echo   按 Ctrl+C 可停止程序
echo  ========================================
echo.

python\python.exe -m streamlit run app.py --server.port=%PORT%

if errorlevel 1 (
    echo.
    echo  启动失败，请查看上方错误信息。
    pause
)
