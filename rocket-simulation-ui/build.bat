@echo off
REM Build the JARVIS Windows executable.
REM
REM This is a thin wrapper around build_simple.py ON PURPOSE. It used to carry
REM its own copy of the PyInstaller command line, and that copy rotted: it
REM built --onefile under the name JARVIS, while the real build is --onedir
REM under JARVIS_Rocket_Simulation, and it had none of the data files, the
REM --paths entries or the hidden imports the app has needed since. It then
REM looked for dist\JARVIS.exe, which no build has ever produced, so it
REM printed "not found after build" every single time.
REM
REM One build definition. If the flags need changing, change build_simple.py -
REM that is what CI runs and what the release is cut from.

cd /d "%~dp0"

REM Prefer the project virtual environment, fall back to whatever python is on
REM PATH so a fresh clone can still build.
set "PYTHON_EXE=..\..\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Building JARVIS Rocket Simulation...
echo Using: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" -m pip install --quiet --upgrade pyinstaller
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Could not install PyInstaller.
    pause
    exit /b 1
)

"%PYTHON_EXE%" build_simple.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Build failed. The messages above say why.
    pause
    exit /b 1
)

echo.
echo Done. Copy the whole dist\JARVIS_Rocket_Simulation folder to the drive.
pause
