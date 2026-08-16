@echo off
echo ============================================
echo  FX Trade Generator - Frontend
echo ============================================
echo.

if not exist "frontend\node_modules" (
    echo  Installing frontend dependencies...
    cd frontend
    npm install
) else (
    cd frontend
)

echo.
echo  Starting frontend on http://localhost:3000
echo  Press Ctrl+C to stop.
echo ============================================
echo.

npm run dev
