@echo off
echo ============================================
echo  FX Trade Generator
echo ============================================
echo.
echo  This needs two terminals running side by side.
echo.
echo  Step 1: Create a .env file in this folder (defaults to AWS Bedrock)
echo  Step 2: Run start-backend.bat in Terminal 1
echo  Step 3: Run start-frontend.bat in Terminal 2
echo  Step 4: Open http://localhost:3000
echo.

if not exist ".env" (
    echo  No .env file found. Creating template...
    (
        echo # Option 1: Anthropic API ^(easiest for local dev^)
        echo # Get your key at https://console.anthropic.com/
        echo # ANTHROPIC_API_KEY=sk-ant-your-key-here
        echo.
        echo # Option 2: AWS Bedrock ^(default^)
        echo # Requires: pip install boto3 ^&^& aws configure --profile fx-bedrock
        echo AWS_PROFILE=fx-bedrock
        echo BEDROCK_REGION=eu-west-1
        echo BEDROCK_MODEL_ID=eu.anthropic.claude-sonnet-4-5-20250929-v1:0
        echo.
        echo # Force a specific backend ^(auto-detects if unset^)
        echo CLAUDE_BACKEND=bedrock
    ) > .env
    echo.
    echo  EDIT the .env file with your AWS profile ^(or an Anthropic API key^), then run start-backend.bat
    echo.
    notepad .env
) else (
    echo  .env file found. Starting both services...
    echo.
    start "FX-Backend" cmd /k "start-backend.bat"
    timeout /t 4 /nobreak >nul
    start "FX-Frontend" cmd /k "start-frontend.bat"
    echo.
    echo  Both services starting. Open http://localhost:3000
)

echo.
pause
