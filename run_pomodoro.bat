@echo off
title Pomodoro Timer Launcher

:: Check if Python is available
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0pomodoro.pyw"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    start "" python "%~dp0pomodoro.pyw"
    exit /b 0
)

:: Python not found
echo.
echo  ========================================
echo   Pomodoro Timer - Python Required
echo  ========================================
echo.
echo  Python is not installed on this machine.
echo.
echo  To install Python:
echo    1. Go to https://www.python.org/downloads/
echo    2. Download Python 3.8 or newer
echo    3. Run the installer
echo    4. IMPORTANT: Check "Add Python to PATH"
echo    5. Click "Install Now"
echo    6. Double-click this file again
echo.
echo  Or ask your IT team to install Python.
echo.
pause
