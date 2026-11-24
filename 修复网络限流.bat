@echo off
chcp 65001 >nul
setlocal
title Vel'Koz 网络限流修复（管理员）

:: ============================================================
::  Vel'Koz v1.24.2 - 局域网图片读取优化辅助脚本
::  作用：关闭 Windows 对 SMB/局域网的多媒体网络限流，
::        降低百度网盘等大流量下载时主界面图片读取超时的概率。
::  说明：只改本机注册表，不改动局域网共享盘上的任何文件。
::  用法：右键“以管理员身份运行”。
::        恢复默认：带参数运行  →  修复网络限流.bat restore
:: ============================================================

set "PROFILE_KEY=HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
set "LANMAN_KEY=HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"

net session >nul 2>&1
if errorlevel 1 goto NO_ADMIN
goto RUN

:NO_ADMIN
echo.
echo  [错误] 需要管理员权限。
echo         请右键本文件，选择“以管理员身份运行”。
echo.
pause
exit /b 1

:RUN
if /i "%~1"=="restore" goto RESTORE

echo.
echo  ============================================
echo    正在关闭 Windows 多媒体网络限流
echo  ============================================
echo.

echo  [1/3] NetworkThrottlingIndex = 0xffffffff（关闭限流）
reg add "%PROFILE_KEY%" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f
if errorlevel 1 goto FAIL

echo  [2/3] SystemResponsiveness = 0（前台优先）
reg add "%PROFILE_KEY%" /v SystemResponsiveness /t REG_DWORD /d 0 /f
if errorlevel 1 goto FAIL

echo  [3/3] SessTimeout = 30（局域网会话超时更宽裕）
reg add "%LANMAN_KEY%" /v SessTimeout /t REG_DWORD /d 30 /f
if errorlevel 1 goto FAIL

echo.
echo  ============================================
echo    设置完成！请重启电脑后生效。
echo  ============================================
echo.
echo  提示：
echo   - 如果仍然卡顿，可把百度网盘下载限速到带宽的 80% 左右。
echo   - 如需恢复默认：右键管理员运行    修复网络限流.bat restore
echo.
pause
exit /b 0

:RESTORE
echo.
echo  正在恢复 Windows 默认网络设置...
reg delete "%PROFILE_KEY%" /v NetworkThrottlingIndex /f >nul 2>&1
reg delete "%PROFILE_KEY%" /v SystemResponsiveness /f >nul 2>&1
reg delete "%LANMAN_KEY%" /v SessTimeout /f >nul 2>&1
echo  已恢复（默认值：限流开启 / 响应性 20 / 会话超时系统默认）。
echo  重启后生效。
echo.
pause
exit /b 0

:FAIL
echo.
echo  [失败] 注册表写入失败，可能是权限不足或被安全软件拦截。
echo         请确认以管理员身份运行后重试。
echo.
pause
exit /b 1
