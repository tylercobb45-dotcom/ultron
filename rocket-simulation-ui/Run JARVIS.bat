@echo off
setlocal
title JARVIS Rocket Simulation

REM Run JARVIS straight from source. Double-click this file.
REM It finds Python, installs anything missing the first time, then launches.

cd /d "%~dp0"

REM --- find a usable Python -------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo.
    echo   Python is not installed, or it is not on your PATH.
    echo.
    echo   Install it from https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH" in the installer,
    echo   then close this window and run this file again.
    echo.
    pause
    exit /b 1
)

REM --- make sure the dependencies are there ---------------------------------
%PY% -c "import PyQt5, matplotlib, numpy, scipy" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   First run - installing the required packages.
    echo   This downloads a few hundred MB and takes a couple of minutes.
    echo.
    %PY% -m pip install --upgrade pip
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   The install failed. The output above says why.
        echo.
        pause
        exit /b 1
    )
    echo.
    echo   Packages installed.
    echo.
)

REM --- launch ---------------------------------------------------------------
echo   Starting JARVIS...
%PY% "src\main.py"

REM Only pause if the app exited badly, so a normal quit closes the window.
if errorlevel 1 (
    echo.
    echo   JARVIS exited with an error. The traceback above says why.
    echo.
    pause
)

endlocal
