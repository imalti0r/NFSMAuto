@echo off
cd /d "%~dp0"
python run.py
if errorlevel 1 (
    echo.
    echo Program exited with error.
    pause
)
