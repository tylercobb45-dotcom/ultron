@echo off
echo.
echo JARVIS Rocket Simulation App
echo ===============================
echo.
echo Starting application...
echo.

REM Change to the directory containing this script
cd /d "%~dp0"

REM Run the executable
REM The built app is named JARVIS_Rocket_Simulation, and an onedir build
REM puts it in a folder of that name. This used to look for "JARVIS.exe",
REM which no build has ever produced, so it always reported it missing.
set "EXE=JARVIS_Rocket_Simulation.exe"
if exist "dist\JARVIS_Rocket_Simulation\%EXE%" (
    start "" "dist\JARVIS_Rocket_Simulation\%EXE%"
    echo Application launched.
) else if exist "%EXE%" (
    start "" "%EXE%"
    echo Application launched.
) else (
    echo Could not find %EXE%.
    echo.
    echo   - From a release zip: it sits beside this file.
    echo   - After building locally: look in dist\JARVIS_Rocket_Simulation\
    echo   - Running from source instead? Use "Run JARVIS.bat".
    pause
    exit /b 1
)

echo.
echo Tip: You can also run the .exe directly by double-clicking it
echo.
timeout /t 3 >nul
