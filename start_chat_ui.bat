@echo off
TITLE Chat UI Controller
COLOR 0A

:: ====================================================
:: 1. CONFIGURATION
:: ====================================================

:: The Main Folder where your code lives
SET PROJECT_ROOT=D:\chat-ui

:: Python Virtual Environment Name 
:: (IMPORTANT: If your folder is named 'text_venv', change 'venv' to 'text_venv' below)
SET VENV_NAME=venv

:: MinIO Configuration
SET "MINIO_EXE="
SET "MINIO_DATA=%PROJECT_ROOT%\minio-data"

:: Preferred + fallback MinIO binary locations
IF EXIST "D:\minio\minio.exe" SET "MINIO_EXE=D:\minio\minio.exe"
IF NOT DEFINED MINIO_EXE IF EXIST "D:\minio.exe\minio.exe" SET "MINIO_EXE=D:\minio.exe\minio.exe"
IF NOT DEFINED MINIO_EXE IF EXIST "%PROJECT_ROOT%\minio-run\minio.exe" SET "MINIO_EXE=%PROJECT_ROOT%\minio-run\minio.exe"
IF NOT DEFINED MINIO_EXE IF EXIST "%PROJECT_ROOT%\minio.exe" SET "MINIO_EXE=%PROJECT_ROOT%\minio.exe"
FOR /F "delims=" %%I IN ('where minio 2^>nul') DO (
  IF NOT DEFINED MINIO_EXE SET "MINIO_EXE=%%I"
)

:: RabbitMQ / Celery defaults (required for Developer Dashboard to show "configured")
IF "%CELERY_ENABLED%"=="" SET "CELERY_ENABLED=1"
IF "%CELERY_BROKER_URL%"=="" IF NOT "%RABBITMQ_URL%"=="" SET "CELERY_BROKER_URL=%RABBITMQ_URL%"
IF "%CELERY_BROKER_URL%"=="" SET "CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//"
IF "%RABBITMQ_URL%"=="" SET "RABBITMQ_URL=%CELERY_BROKER_URL%"
IF "%CELERY_RESULT_BACKEND%"=="" SET "CELERY_RESULT_BACKEND=rpc://"
IF "%CELERY_OUTBOX_ENABLED%"=="" SET "CELERY_OUTBOX_ENABLED=1"
IF "%CELERY_DEFAULT_QUEUE%"=="" SET "CELERY_DEFAULT_QUEUE=default"

:: ====================================================
:: 2. STARTUP SEQUENCE
:: ====================================================

echo.
echo [1/5] Launching Minio Server...
IF EXIST "%MINIO_EXE%" (
  IF NOT EXIST "%MINIO_DATA%" mkdir "%MINIO_DATA%"
  start "Chat UI Minio" cmd /k "%MINIO_EXE% server %MINIO_DATA% --console-address :9001"
) ELSE (
  echo [WARN] MinIO executable not found. Checked: D:\minio\minio.exe, D:\minio.exe\minio.exe, %PROJECT_ROOT%\minio-run\minio.exe, %PROJECT_ROOT%\minio.exe, PATH
)

echo [2/5] Launching Backend (Uvicorn)...
:: Goes to project root, activates python, runs your specific uvicorn command
start "Chat UI Backend" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000"

echo [3/5] Launching Celery Worker...
start "Chat UI Celery Worker" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && celery -A backend.queue.celery_app:celery_app worker --loglevel=info -Q %CELERY_DEFAULT_QUEUE% --pool=solo --concurrency=1"

echo [4/5] Launching Celery Beat...
start "Chat UI Celery Beat" cmd /k "cd /d %PROJECT_ROOT% && %VENV_NAME%\Scripts\activate && celery -A backend.queue.celery_app:celery_app beat --loglevel=info"

echo [5/5] Launching Frontend (Next.js)...
:: Goes to project root, then into 'frontend' folder, then runs npm
start "Chat UI Frontend" cmd /k "cd /d %PROJECT_ROOT%\frontend && npm run dev"

echo.
echo Waiting for servers to initialize...
timeout /t 5 /nobreak >nul

:: ====================================================
:: 3. OPEN BROWSER
:: ====================================================

echo Opening Browser Tabs...
start http://localhost:3000
IF EXIST "%MINIO_EXE%" start http://localhost:9001
start http://localhost:8000/docs

:: ====================================================
:: 4. THE KILL SWITCH
:: ====================================================

CLS
echo ========================================================
echo   CHAT UI IS RUNNING
echo ========================================================
echo.
echo   [Status]
echo   - Minio:   Starts when MinIO binary is found
echo   - RabbitMQ Broker: %CELERY_BROKER_URL%
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
taskkill /FI "WINDOWTITLE eq Chat UI Minio*" /F /T
taskkill /FI "WINDOWTITLE eq Chat UI Backend*" /F /T
taskkill /FI "WINDOWTITLE eq Chat UI Celery Worker*" /F /T
taskkill /FI "WINDOWTITLE eq Chat UI Celery Beat*" /F /T
taskkill /FI "WINDOWTITLE eq Chat UI Frontend*" /F /T

:: Cleanup specific executables to ensure ports are freed
taskkill /F /IM minio.exe >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1
taskkill /F /IM celery.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1

echo Done.
timeout /t 1 >nul
