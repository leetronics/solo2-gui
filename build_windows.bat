@echo off
:: build_windows.bat — Build SoloKeys GUI for Windows installer
::
:: Requirements: Python 3.10+, libusb-1.0.dll
:: Usage: build_windows.bat
::
:: The resulting distributable is dist\installer\SoloKeys-GUI-Setup-<version>.exe

setlocal enabledelayedexpansion

set APP_NAME=SoloKeys GUI
for /f "tokens=*" %%v in ('python scripts\app_version.py resolved') do set APP_VERSION=%%v
set APP_DIR=dist\%APP_NAME%
set HOST_EXE=dist\solokeys-secrets-host.exe
set INSTALLER_OUTPUT=dist\installer\SoloKeys-GUI-Setup-%APP_VERSION%.exe

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

echo Installing pywin32 for HID proxy service...
pip install "pywin32>=306"
if errorlevel 1 (
    echo Error: pywin32 install failed.
    exit /b 1
)

python scripts\app_version.py write-build-module --version "%APP_VERSION%" >nul
if errorlevel 1 (
    echo Error: failed to write build version module.
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 3b. Detect Inno Setup compiler
:: ---------------------------------------------------------------------------
set ISCC_CMD=
for %%I in (iscc.exe) do set ISCC_CMD=%%~$PATH:I
if defined ISCC_CMD (
    echo Inno Setup compiler found at %ISCC_CMD%
    goto :iscc_found
)

if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set ISCC_CMD=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
    echo Inno Setup compiler found at %ISCC_CMD%
    goto :iscc_found
)

if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set ISCC_CMD=C:\Program Files\Inno Setup 6\ISCC.exe
    echo Inno Setup compiler found at %ISCC_CMD%
    goto :iscc_found
)

echo Error: Inno Setup Compiler ^(ISCC.exe^) not found.
echo.
echo Install Inno Setup 6 from:
echo   https://jrsoftware.org/isdl.php
echo.
echo Or ensure ISCC.exe is available on PATH.
exit /b 1

:iscc_found

:: ---------------------------------------------------------------------------
:: 4. Clean previous build artifacts
:: ---------------------------------------------------------------------------
echo.
echo Cleaning previous build...
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

:: ---------------------------------------------------------------------------
:: 5. Run PyInstaller for the GUI
:: ---------------------------------------------------------------------------
echo.
echo Running PyInstaller for GUI...
python -m PyInstaller --clean --noconfirm solokeys_gui.spec
if errorlevel 1 (
    echo Error: PyInstaller GUI build failed.
    exit /b 1
)

if not exist "%APP_DIR%" (
    echo Error: PyInstaller did not produce %APP_DIR%\
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 6. Run PyInstaller for the native host helper
:: ---------------------------------------------------------------------------
echo.
echo Running PyInstaller for native host...
python -m PyInstaller --clean --noconfirm native_host.spec
if errorlevel 1 (
    echo Error: PyInstaller native host build failed.
    exit /b 1
)

if not exist "%HOST_EXE%" (
    echo Error: PyInstaller did not produce %HOST_EXE%
    exit /b 1
)

copy /y "%HOST_EXE%" "%APP_DIR%\solokeys-secrets-host.exe" >nul
if errorlevel 1 (
    echo Error: failed to copy native host into %APP_DIR%\
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 7. Build installer
:: ---------------------------------------------------------------------------
echo.
echo Running Inno Setup...
"%ISCC_CMD%" /Qp /DMyAppVersion=%APP_VERSION% /DMyAppSourceDir="%APP_DIR%" /DMyOutputDir="dist\installer" installer_windows.iss
if errorlevel 1 (
    echo Error: Inno Setup build failed.
    exit /b 1
)

if not exist "%INSTALLER_OUTPUT%" (
    echo Error: Installer was not created at %INSTALLER_OUTPUT%
    exit /b 1
)

:: ---------------------------------------------------------------------------
:: 8. Summary
:: ---------------------------------------------------------------------------
echo.
echo ============================================================
echo  Build complete: %INSTALLER_OUTPUT%
echo.
echo  To distribute:
echo    Share the installer EXE with users.
echo.
echo  Intermediate app payload:
echo    %APP_DIR%\
echo.
echo  Note: Windows SmartScreen may warn on first launch because
echo  the installer and app are not code-signed. To suppress this warning,
echo  sign the installer and bundled binaries before distribution.
echo ============================================================

if exist src\solo_gui\_build_version.py del /q src\solo_gui\_build_version.py >nul 2>&1

endlocal
