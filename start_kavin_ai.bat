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

:: Fallbacks for local MinIO binaries
IF NOT EXIST "%MINIO_EXE%" IF EXIST "%PROJECT_ROOT%\minio-run\minio.exe" SET MINIO_EXE=%PROJECT_ROOT%\minio-run\minio.exe
IF NOT EXIST "%MINIO_EXE%" IF EXIST "%PROJECT_ROOT%\minio.exe" SET MINIO_EXE=%PROJECT_ROOT%\minio.exe

:: ====================================================
:: 2. STARTUP SEQUENCE
:: ====================================================

echo.
echo [1/3] Launching Minio Server...
IF EXIST "%MINIO_EXE%" (
  IF NOT EXIST "%MINIO_DATA%" mkdir "%MINIO_DATA%"
  start "Kavin Minio" cmd /k "%MINIO_EXE% server %MINIO_DATA% --console-address :9001"
) ELSE (
  echo [WARN] Minio executable not found at "%MINIO_EXE%". Continuing without Minio.
)

echo [2/5] Launching Backend (Uvicorn)...
:: Goes to project root, activates python, runs your specific uvicorn command
start "Kavin Backend" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000"

echo [3/5] Launching Celery Worker...
start "Kavin Celery Worker" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && celery -A backend.queue.celery_app:celery_app worker --loglevel=info -Q kavin.default --pool=solo --concurrency=1"

echo [4/5] Launching Celery Beat...
start "Kavin Celery Beat" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && celery -A backend.queue.celery_app:celery_app beat --loglevel=info"

echo [5/5] Launching Frontend (Next.js)...
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
echo   - Celery Worker: Running (RabbitMQ queue consumer)
echo   - Celery Beat: Running (scheduled outbox retries)
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
taskkill /FI "WINDOWTITLE eq Kavin Celery Worker*" /F /T
taskkill /FI "WINDOWTITLE eq Kavin Celery Beat*" /F /T
taskkill /FI "WINDOWTITLE eq Kavin Frontend*" /F /T

:: Cleanup specific executables to ensure ports are freed
taskkill /F /IM minio.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
taskkill /F /IM celery.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1

echo Done.
timeout /t 1 >nul
