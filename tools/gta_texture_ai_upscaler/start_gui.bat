@echo off
cd /d "%~dp0"
title GTA Texture AI Upscaler GUI
echo Starting GUI (console log stays open)...
python main.py --gui
if errorlevel 1 pause
