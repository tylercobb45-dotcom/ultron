@echo off
REM ===================================================================
REM  JARVIS - run from a flash drive on Windows
REM
REM  For the SOURCE download (the green "Code" button on GitHub). If you
REM  downloaded a release zip instead, you do not need this - just run
REM  JARVIS_Rocket_Simulation.exe.
REM
REM  This needs Python on the computer, but installs nothing into it:
REM  the libraries go into a folder ON THE DRIVE, keyed by platform and
REM  Python version so several computers can share one stick.
REM
REM  Deliberately NOT a virtual environment: a venv writes the absolute
REM  path it was created at into its own scripts, so one made while the
REM  stick was E: stops working when it comes up as F: on the next
REM  computer. "pip --target" has no such problem.
REM ===================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   JARVIS Rocket Simulation - running from the drive
echo   -------------------------------------------

REM --- find a Python ------------------------------------------------
set PY=
where py >nul 2>&1 && set PY=py -3
if "!PY!"=="" ( where python >nul 2>&1 && set PY=python )
if "!PY!"=="" (
    echo.
    echo   No Python found on this computer.
    echo.
    echo   Either install Python 3.11+ from https://python.org
    echo   ^(tick "Add python.exe to PATH" in the installer^),
    echo   or download the ready-to-run release zip instead, which
    echo   needs no Python at all:
    echo     https://github.com/tylercobb45-dotcom/ultron/releases
    echo.
    pause
    exit /b 1
)

REM --- a library folder per platform + python version ----------------
REM Single quotes inside, double quotes outside: cmd does not treat \" as an
REM escape, so a nested double quote here ends the string early and the whole
REM line breaks. Keep this one-liner free of nested double quotes.
for /f "delims=" %%v in ('!PY! -c "import sys,platform;print('win-'+platform.machine().lower()+'-py'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))"') do set TAG=%%v
set LIBDIR=JARVIS-Data\lib\!TAG!

if not exist "!LIBDIR!" (
    echo.
    echo   First run on this kind of computer - setting up libraries.
    echo   This needs the internet once; after that it works offline.
    echo   Installing into !LIBDIR!
    echo.
    !PY! -m pip install --upgrade pip --quiet
    !PY! -m pip install --target "!LIBDIR!" -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Library install failed. Check the internet connection,
        echo   or use the release zip which needs no install.
        rmdir /s /q "!LIBDIR!" 2>nul
        pause
        exit /b 1
    )
)

REM --- run, with the drive's libraries first on the path -------------
set PYTHONPATH=%CD%\!LIBDIR!;%PYTHONPATH%
echo   Starting JARVIS...
!PY! src\main.py
if errorlevel 1 (
    echo.
    echo   JARVIS exited with an error. The message above says why.
    pause
)
endlocal
