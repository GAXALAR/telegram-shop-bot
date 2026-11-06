@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        py -3 launcher.py
        exit /b
    )
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 (
        python launcher.py
        exit /b
    )
)

echo Python 3.10+ with Tcl/Tk was not found.
echo Install Python from https://www.python.org/downloads/ and enable "Add Python to PATH".
pause
