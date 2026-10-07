@echo off
title GTA SA Texture Remaster Pipeline v4
echo ============================================================
echo      Los Santos AI - Texture Remastering Pipeline v4
echo ============================================================
echo.

if "%1"=="--terminate" (
    python run_pipeline.py --terminate <NUL
    goto END
)

python run_pipeline.py %* <NUL

:END
pause
