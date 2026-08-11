@echo off
setlocal EnableDelayedExpansion

title AutoCAD Electrical MCP — Installer
cd /d "%~dp0"

echo.
echo  ============================================================
echo   AutoCAD Electrical MCP — First-Time Setup
echo  ============================================================
echo.

REM ── 1. Winget availability check ────────────────────────────────────────────
winget --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] winget is not available on this machine.
    echo.
    echo  Please install the following manually, then re-run this script:
    echo    Python 3.11+  ^>  https://www.python.org/downloads/
    echo    Git           ^>  https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

REM ── 2. Python ────────────────────────────────────────────────────────────────
echo  Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python not found. Installing via winget...
    winget install --id Python.Python.3.11 --source winget --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo  [ERROR] Python install failed. Install manually: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    REM Refresh PATH so python.exe is visible in this session
    call :RefreshPath
    python --version >nul 2>&1
    if errorlevel 1 (
        echo  [WARN] Python installed but not yet on PATH.
        echo  Please close this window, reopen it, and run install.bat again.
        pause
        exit /b 1
    )
    echo  Python installed successfully.
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    echo  Found Python !PY_VER!
)

REM ── 3. Git ───────────────────────────────────────────────────────────────────
echo.
echo  Checking Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo  Git not found. Installing via winget...
    winget install --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo  [ERROR] Git install failed. Install manually: https://git-scm.com/download/win
        pause
        exit /b 1
    )
    call :RefreshPath
    git --version >nul 2>&1
    if errorlevel 1 (
        echo  [WARN] Git installed but not yet on PATH.
        echo  Please close this window, reopen it, and run install.bat again.
        pause
        exit /b 1
    )
    echo  Git installed successfully.
) else (
    for /f "tokens=3" %%v in ('git --version 2^>^&1') do set GIT_VER=%%v
    echo  Found Git !GIT_VER!
)

REM ── 4. Python dependencies (scripts/install.py) ───────────────────────────
echo.
echo  Installing Python dependencies...
echo.
python scripts\install.py
if errorlevel 1 (
    echo.
    echo  [ERROR] Dependency installation failed. See errors above.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   All done! Next steps:
echo    1. Edit .env and set MCC_BLOCK_LIBRARY to your block folder
echo    2. Open MCC_LAYOUT.dwg, MCC_UNITDATA.dwg, MCC_NAMEPLATE.dwg
echo       in AutoCAD Electrical
echo    3. Double-click start_web.bat to launch the app
echo  ============================================================
echo.
pause
exit /b 0


REM ── Helper: reload PATH from registry without restarting the shell ──────────
:RefreshPath
    for /f "skip=2 tokens=3*" %%a in (
        'reg query "HKCU\Environment" /v PATH 2^>nul'
    ) do set USER_PATH=%%a%%b
    for /f "skip=2 tokens=3*" %%a in (
        'reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul'
    ) do set SYS_PATH=%%a%%b
    set "PATH=!SYS_PATH!;!USER_PATH!"
    exit /b 0
