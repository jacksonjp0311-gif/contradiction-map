@echo off
setlocal
pushd "%~dp0" || exit /b 2
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 or newer is required. Install Python from python.org, then reopen this launcher.
    popd
    exit /b 2
)
if "%~1"=="" (
    python -m contradiction_map serve --open
) else (
    python -m contradiction_map %*
)
set "resultCode=%ERRORLEVEL%"
popd
exit /b %resultCode%
