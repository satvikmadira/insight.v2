@echo off
title InsightAI Server
color 0A
echo.
echo  ==========================================
echo    InsightAI - Starting...
echo  ==========================================
echo.

cd /d "%~dp0"

echo  Installing packages...
pip install fastapi uvicorn httpx python-multipart -q

echo.
echo  Starting server...
echo  Browser will open automatically at http://localhost:8000
echo.
echo  Press Ctrl+C to stop the server.
echo.

python server.py

echo.
echo  Server stopped.
pause
