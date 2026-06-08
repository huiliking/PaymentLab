@echo off
echo Starting Payment Lab...

REM Terminal 1: Flask Server
start "Payment Lab - Server" cmd /k "cd /d C:\Users\Administrator\Documents\Research\AI\AI_Payment\payment-lab\server && venv\Scripts\activate && python app.py"

REM Wait a moment then start the client
timeout /t 2 /nobreak > nul

REM Terminal 2: Vite Client
start "Payment Lab - Client" cmd /k "cd /d C:\Users\Administrator\Documents\Research\AI\AI_Payment\payment-lab\client && npm run dev"

echo Both terminals launched!
