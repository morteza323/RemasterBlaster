@echo off
cd /d "%~dp0"
title GTA Texture AI Upscaler v5.4
echo ========================================
echo  GTA Texture AI Upscaler v5.4
echo  Photoreal REALISM mode + UV safety + Resume
echo ========================================
echo.
echo Ctrl+C = pause safely, show progress, then exit.
echo Next run continues from where it stopped.
echo.

echo [1/2] Encoding master prompt (if needed)...
python -u main.py --encode-prompt
if errorlevel 1 (
    echo ERROR: encode-prompt failed
    echo Press any key to exit...
    pause >nul
    exit /b 1
)
echo.
echo [2/2] Starting batch processing...
echo.
python -u main.py --start
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% NEQ 0 (
    echo Finished with code %EXITCODE%.
) else (
    echo Done.
)
echo Press any key to close...
pause >nul
exit /b %EXITCODE%
