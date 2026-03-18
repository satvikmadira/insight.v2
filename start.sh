#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  =========================================="
echo "    InsightAI - Starting..."
echo "  =========================================="
echo ""
echo "  Installing packages..."
pip install fastapi uvicorn httpx python-multipart -q
echo ""
echo "  Starting server..."
echo "  Browser will open at http://localhost:8000"
echo "  Press Ctrl+C to stop."
echo ""
python server.py
