@echo off
title  Auto - Admin

pushd "%~dp0"

echo =========================================
echo   NFS Auto - Admin Mode
echo =========================================
echo.

REM Check admin rights
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [OK] Admin privileges confirmed
    echo.
    goto :run
)

REM No admin - request elevation via VBS
echo Requesting admin privileges...
echo.

echo Set objShell = CreateObject("Shell.Application") > "%temp%\RunAdmin.vbs"
echo objShell.ShellExecute "%~f0", "", "", "runas", 1 >> "%temp%\RunAdmin.vbs"

"%temp%\RunAdmin.vbs"

timeout /t 1 /nobreak >nul
if exist "%temp%\RunAdmin.vbs" del "%temp%\RunAdmin.vbs"

popd
exit /b

:run
cd /d "%~dp0"
pushd "%~dp0"

echo =========================================
echo   Starting...
echo =========================================
echo.

REM Step 1: Check Python
echo [1/2] Checking Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    popd
    exit /b 1
)
echo [OK] Python is ready
echo.

REM Step 2: Start main program
echo [2/2] Starting main program...
echo.
echo =========================================
echo   Tips:
echo   1. Make sure game is running and visible
echo   2. Click "Start" on the floating window
echo   3. Move mouse to top-left to emergency stop
echo =========================================
echo.

python run.py

echo.
echo =========================================
echo   Program exited
echo =========================================
pause
popd
