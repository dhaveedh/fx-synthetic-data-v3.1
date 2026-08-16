#!/bin/bash
echo "============================================"
echo " FX Trade Generator - Starting Services"
echo "============================================"
echo ""

# Force Anthropic API (not Bedrock)
export CLAUDE_BACKEND=anthropic

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo " No ANTHROPIC_API_KEY found."
    echo ""
    read -p "  Enter your Anthropic API key: " ANTHROPIC_API_KEY
    export ANTHROPIC_API_KEY
    echo ""
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo " ERROR: API key is required. Get one at https://console.anthropic.com/"
    exit 1
fi

echo " Backend: anthropic"
echo " API key: ${ANTHROPIC_API_KEY:0:12}..."
echo ""

# Python venv
if [ ! -d "venv" ]; then
    echo "[1/4] Creating Python virtual environment..."
    python3 -m venv venv
else
    echo "[1/4] Python venv found"
fi

source venv/bin/activate

echo "[2/4] Installing Python dependencies..."
pip install -r requirements.txt -q

# Node modules
if [ ! -d "frontend/node_modules" ]; then
    echo "[3/4] Installing frontend dependencies..."
    cd frontend && npm install && cd ..
else
    echo "[3/4] Frontend dependencies found"
fi

echo "[4/4] Starting services..."
echo ""
echo "  Backend:  http://127.0.0.1:8000"
echo "  Frontend: http://localhost:3000"
echo "  API docs: http://127.0.0.1:8000/docs"
echo ""
echo "  Press Ctrl+C to stop both."
echo "============================================"
echo ""

# Start backend in background
uvicorn api.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

sleep 2

# Start frontend
cd frontend
npm run dev &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
