@echo off
:: build_windows.bat — Build SoloKeys GUI for Windows (onedir)
::
:: Requirements: Python 3.10+, libusb-1.0.dll
:: Usage: build_windows.bat
::
:: The resulting distributable is dist\SoloKeys GUI\
:: Zip that folder and share it; users run "SoloKeys GUI.exe" inside.

setlocal enabledelayedexpansion

set APP_NAME=SoloKeys GUI
set APP_VERSION=0.1.0

echo.
echo ============================================================
echo  Building %APP_NAME% v%APP_VERSION% for Windows
echo ============================================================
echo.

:: ---------------------------------------------------------------------------
:: 1. Check Python
:: ---------------------------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo Error: python not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    exit /b 1
)

for /f "tokens=*" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYTHON_VERSION=%%v
echo Python %PYTHON_VERSION% found.

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo Error: Python 3.10+ required ^(found %PYTHON_VERSION%^).
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 2. Detect libusb-1.0.dll
:: ---------------------------------------------------------------------------
if defined LIBUSB_PATH (
    if exist "%LIBUSB_PATH%" (
        echo Using LIBUSB_PATH=%LIBUSB_PATH%
        goto :libusb_found
    )
)

if exist "C:\libusb\MS64\dll\libusb-1.0.dll" (
    set LIBUSB_PATH=C:\libusb\MS64\dll\libusb-1.0.dll
    echo Detected libusb at %LIBUSB_PATH%
    goto :libusb_found
)

if exist "C:\tools\libusb\bin\libusb-1.0.dll" (
    set LIBUSB_PATH=C:\tools\libusb\bin\libusb-1.0.dll
    echo Detected libusb at %LIBUSB_PATH%
    goto :libusb_found
)

echo Error: libusb-1.0.dll not found.
echo.
echo Download the latest libusb Windows release from:
echo   https://github.com/libusb/libusb/releases
echo.
echo Extract and place libusb-1.0.dll at one of these locations:
echo   C:\libusb\MS64\dll\libusb-1.0.dll
echo   C:\tools\libusb\bin\libusb-1.0.dll
echo.
echo Or set the LIBUSB_PATH environment variable to the full DLL path:
echo   set LIBUSB_PATH=C:\path\to\libusb-1.0.dll
echo   build_windows.bat
exit /b 1

:libusb_found

:: ---------------------------------------------------------------------------
:: 3. Install Python dependencies
:: ---------------------------------------------------------------------------
echo.
echo Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo Error: pip install failed.
    exit /b 1
)

pip install "pyinstaller>=6.2.0"
if errorlevel 1 (
    echo Error: pyinstaller install failed.
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 4. Clean previous build artifacts
:: ---------------------------------------------------------------------------
echo.
echo Cleaning previous build...
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

:: ---------------------------------------------------------------------------
:: 5. Run PyInstaller
:: ---------------------------------------------------------------------------
echo.
echo Running PyInstaller...
python -m PyInstaller --clean --noconfirm solokeys_gui.spec
if errorlevel 1 (
    echo Error: PyInstaller failed.
    exit /b 1
)

if not exist "dist\%APP_NAME%" (
    echo Error: PyInstaller did not produce dist\%APP_NAME%\
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 6. Summary
:: ---------------------------------------------------------------------------
echo.
echo ============================================================
echo  Build complete: dist\%APP_NAME%\
echo.
echo  To distribute:
echo    1. Zip the folder: dist\%APP_NAME%\
echo    2. Share the zip — users extract and run "%APP_NAME%.exe"
echo.
echo  Note: Windows SmartScreen may warn on first launch because
echo  the executable is not code-signed. To suppress this warning,
echo  sign with an EV code-signing certificate before distribution.
echo ============================================================

endlocal
