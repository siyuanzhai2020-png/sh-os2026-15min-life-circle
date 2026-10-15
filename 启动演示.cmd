@echo off
rem ---------------------------------------------------------------------------
rem  One-click launcher.  THIS FILE MUST STAY PURE ASCII.
rem  cmd.exe reads .cmd/.bat bytes with the console ANSI codepage (GBK on zh-CN),
rem  so any UTF-8 Chinese here gets mangled and cmd then tries to execute the
rem  garbage as commands. Keep all Chinese in tasks.ps1 instead (UTF-8 + BOM,
rem  which PowerShell parses correctly).
rem
rem  Usage: double-click it, or:  launcher.cmd [port]
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PS=powershell"
where powershell >nul 2>nul || set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if "%~1"=="" (
  "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tasks.ps1" dev
) else (
  "%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tasks.ps1" dev -Port %~1
)

echo.
echo [*] Server stopped.
pause
