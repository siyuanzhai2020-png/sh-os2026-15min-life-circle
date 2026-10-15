@echo off
rem ---------------------------------------------------------------------------
rem  Run every offline self-check.  THIS FILE MUST STAY PURE ASCII.
rem  See launcher comments in the dev-start script for why: cmd.exe reads
rem  .cmd bytes with the console ANSI codepage, so UTF-8 Chinese breaks parsing.
rem
rem  Usage: double-click it.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PS=powershell"
where powershell >nul 2>nul || set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tasks.ps1" verify

echo.
pause
