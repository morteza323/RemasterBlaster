@echo off
title GTA Batch AI Upscaler
cd /d F:\ai
call torch_venv\Scripts\activate.bat
cd /d H:\Remaster Blaster\Fast Upscale
python gta_batch_upscaler.py
pause