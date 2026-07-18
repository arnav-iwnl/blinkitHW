@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 "%~dp0main.py" %*
) else (
    python "%~dp0main.py" %*
)
