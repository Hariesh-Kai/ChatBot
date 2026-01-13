@echo off
TITLE Kavin AI Controller
COLOR 0A

:: ====================================================
:: 1. CONFIGURATION
:: ====================================================

:: The Main Folder where your code lives
SET PROJECT_ROOT=D:\chat-ui

:: Python Virtual Environment Name 
:: (IMPORTANT: If your folder is named 'text_venv', change 'venv' to 'text_venv' below)
SET VENV_NAME=venv

:: Minio Configuration
SET MINIO_EXE=D:\minio\minio.exe
SET MINIO_DATA=D:\chat-ui\minio-data

:: ====================================================
:: 2. STARTUP SEQUENCE
:: ====================================================

echo.
echo [1/3] Launching Minio Server...
:: FIX: Removed extra quotes around MINIO_DATA to prevent path errors
start "Kavin Minio" cmd /k "%MINIO_EXE% server %MINIO_DATA% --console-address :9001"

echo [2/3] Launching Backend (Uvicorn)...
:: Goes to project root, activates python, runs your specific uvicorn command
start "Kavin Backend" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000"

echo [3/3] Launching Frontend (Next.js)...
:: Goes to project root, then into 'frontend' folder, then runs npm
start "Kavin Frontend" cmd /k "cd /d %PROJECT_ROOT%\frontend && npm run dev"

echo.
echo Waiting for servers to initialize...
timeout /t 5 /nobreak >nul

:: ====================================================
:: 3. OPEN BROWSER
:: ====================================================

echo Opening Browser Tabs...
start http://localhost:3000
start http://localhost:9001
start http://localhost:8000/docs

:: ====================================================
:: 4. THE KILL SWITCH
:: ====================================================

CLS
echo ========================================================
echo   KAVIN AI IS RUNNING
echo ========================================================
echo.
echo   [Status]
echo   - Minio:   Running on Port 9000/9001
echo   - Backend: Running on Port 8000
echo   - Frontend: Running on Port 3000
echo.
echo   PRESS ANY KEY TO STOP SERVERS AND CLOSE WINDOWS.
echo ========================================================
pause >nul

echo.
echo Shutting down...

:: Force kills the windows by their specific titles
taskkill /FI "WINDOWTITLE eq Kavin Minio*" /F /T
taskkill /FI "WINDOWTITLE eq Kavin Backend*" /F /T
taskkill /FI "WINDOWTITLE eq Kavin Frontend*" /F /T

:: Cleanup specific executables to ensure ports are freed
taskkill /F /IM minio.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1

echo Done.
timeout /t 1 >nul