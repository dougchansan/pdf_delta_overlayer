@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   PDF Overlay Comparison Tool
echo   Blue = NEW    Red = REMOVED    Black = SAME
echo ============================================
echo.

:: Check for drag-and-drop or command-line args
if "%~1" NEQ "" (
    set "OLD_DIR=%~1"
) else (
    set /p "OLD_DIR=Drag OLD folder here (previous revision): "
)

:: Remove surrounding quotes if present
set "OLD_DIR=!OLD_DIR:"=!"

if "%~2" NEQ "" (
    set "NEW_DIR=%~2"
) else (
    set /p "NEW_DIR=Drag NEW folder here (current revision):  "
)

set "NEW_DIR=!NEW_DIR:"=!"

if "%~3" NEQ "" (
    set "OUT_DIR=%~3"
) else (
    set /p "OUT_DIR=Drag OUTPUT folder here (or press Enter for default): "
)

set "OUT_DIR=!OUT_DIR:"=!"

echo.
echo  OLD:  !OLD_DIR!
echo  NEW:  !NEW_DIR!
if "!OUT_DIR!" NEQ "" (
    echo  OUT:  !OUT_DIR!
) else (
    echo  OUT:  !NEW_DIR!\Overlays  (default)
)
echo  DPI:  300
echo.

:: Run overlay.py from the same directory as this .bat file
set "SCRIPT_DIR=%~dp0"

if "!OUT_DIR!" NEQ "" (
    python "%SCRIPT_DIR%overlay.py" --old "!OLD_DIR!" --new "!NEW_DIR!" --output-dir "!OUT_DIR!" --dpi 300
) else (
    python "%SCRIPT_DIR%overlay.py" --old "!OLD_DIR!" --new "!NEW_DIR!" --dpi 300
)

echo.
if %ERRORLEVEL% EQU 0 (
    echo  Done! Check the Overlays folder for results.
) else (
    echo  Something went wrong. Check the error messages above.
)
echo.
pause
