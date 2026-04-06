param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
$runtimeDir = Join-Path $projectRoot ".runtime"
$logDir = Join-Path $runtimeDir "logs"
$pidDir = Join-Path $runtimeDir "pids"
$launcherLog = Join-Path $logDir "launcher.log"

foreach ($path in @($runtimeDir, $logDir, $pidDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

function Write-LauncherLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $launcherLog -Value "[$timestamp] $Message"
}

function Import-DotEnvIfPresent {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($line in Get-Content $Path) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line.TrimStart().StartsWith("#")) {
            continue
        }

        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }

        $name = $line.Substring(0, $separatorIndex).Trim()
        $value = $line.Substring($separatorIndex + 1)

        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        $existing = [Environment]::GetEnvironmentVariable($name, "Process")
        if ([string]::IsNullOrWhiteSpace($existing)) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

function Ensure-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $existing = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($existing)) {
        Set-Item -Path "Env:$Name" -Value $Value
    }
}

function Get-PidPath {
    param([string]$Name)
    Join-Path $pidDir "$Name.pid"
}

function Try-GetLivePid {
    param([string]$Name)

    $pidPath = Get-PidPath -Name $Name
    if (-not (Test-Path $pidPath)) {
        return $null
    }

    $rawValue = (Get-Content $pidPath -Raw).Trim()
    $parsedPid = 0
    if (-not [int]::TryParse($rawValue, [ref]$parsedPid)) {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }

    try {
        $null = Get-Process -Id $parsedPid -ErrorAction Stop
        return $parsedPid
    }
    catch {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Test-TcpPort {
    param(
        [int]$Port,
        [string]$HostName = "127.0.0.1",
        [int]$TimeoutMs = 750
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-ForPort {
    param(
        [int]$Port,
        [int]$TimeoutSec = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) {
            return $true
        }
        Start-Sleep -Seconds 1
    }

    return $false
}

function Resolve-MinioExe {
    $candidates = @(
        "D:\minio\minio.exe",
        "D:\minio.exe\minio.exe",
        (Join-Path $projectRoot "minio-run\minio.exe"),
        (Join-Path $projectRoot "minio.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    try {
        $command = Get-Command minio.exe -ErrorAction Stop
        if ($command -and $command.Source) {
            return $command.Source
        }
    }
    catch {
    }

    return $null
}

function Find-ExistingProcessId {
    param([string[]]$Patterns)

    if (-not $Patterns -or $Patterns.Count -eq 0) {
        return $null
    }

    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

        $matchesAll = $true
        foreach ($pattern in $Patterns) {
            if ($commandLine.IndexOf($pattern, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
                $matchesAll = $false
                break
            }
        }

        if ($matchesAll) {
            return [int]$process.ProcessId
        }
    }

    return $null
}

function Start-ManagedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string[]]$ExistingCommandPatterns = @(),
        [int]$Port = 0
    )

    $existingPid = Try-GetLivePid -Name $Name
    if ($existingPid) {
        Write-LauncherLog "$Name already running with pid $existingPid."
        return $false
    }

    if ($Port -gt 0 -and (Test-TcpPort -Port $Port)) {
        Write-LauncherLog "$Name skipped because port $Port is already in use."
        return $false
    }

    $externalPid = Find-ExistingProcessId -Patterns $ExistingCommandPatterns
    if ($externalPid) {
        Write-LauncherLog "$Name skipped because a matching process is already running with pid $externalPid."
        return $false
    }

    $stdoutPath = Join-Path $logDir "$Name.out.log"
    $stderrPath = Join-Path $logDir "$Name.err.log"

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    Set-Content -Path (Get-PidPath -Name $Name) -Value $process.Id -Encoding ascii
    Write-LauncherLog "$Name started with pid $($process.Id)."
    return $true
}

Import-DotEnvIfPresent -Path (Join-Path $projectRoot ".env")

$defaultBrokerUrl = $env:RABBITMQ_URL
if ([string]::IsNullOrWhiteSpace($defaultBrokerUrl)) {
    $defaultBrokerUrl = "amqp://guest:guest@127.0.0.1:5672//"
}

Ensure-EnvValue -Name "CELERY_ENABLED" -Value "1"
Ensure-EnvValue -Name "CELERY_BROKER_URL" -Value $defaultBrokerUrl
Ensure-EnvValue -Name "RABBITMQ_URL" -Value $env:CELERY_BROKER_URL
Ensure-EnvValue -Name "CELERY_RESULT_BACKEND" -Value "rpc://"
Ensure-EnvValue -Name "CELERY_OUTBOX_ENABLED" -Value "1"
Ensure-EnvValue -Name "CELERY_DEFAULT_QUEUE" -Value "chatui.default"

$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"
$celeryExe = Join-Path $projectRoot "venv\Scripts\celery.exe"
$npmCmd = (Get-Command npm.cmd -ErrorAction Stop).Source
$commandShell = $env:ComSpec
$minioExe = Resolve-MinioExe
$minioData = Join-Path $projectRoot "minio-data"

if (-not (Test-Path $pythonExe)) {
    throw "Missing Python runtime at $pythonExe"
}
if (-not (Test-Path $celeryExe)) {
    throw "Missing Celery executable at $celeryExe"
}

if ($minioExe) {
    New-Item -ItemType Directory -Force -Path $minioData | Out-Null
    Start-ManagedProcess `
        -Name "minio" `
        -FilePath $minioExe `
        -ArgumentList @("server", $minioData, "--console-address", ":9001") `
        -WorkingDirectory $projectRoot `
        -Port 9000 | Out-Null
}
else {
    Write-LauncherLog "MinIO executable not found; skipping local object storage launch."
}

Start-ManagedProcess `
    -Name "backend" `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "uvicorn", "backend.api.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000") `
    -WorkingDirectory $projectRoot `
    -Port 8000 | Out-Null

Start-ManagedProcess `
    -Name "celery-worker" `
    -FilePath $celeryExe `
    -ArgumentList @(
        "-A",
        "backend.queue.celery_app:celery_app",
        "worker",
        "--loglevel=info",
        "-Q",
        $env:CELERY_DEFAULT_QUEUE,
        "--pool=solo",
        "--concurrency=1"
    ) `
    -ExistingCommandPatterns @("backend.queue.celery_app:celery_app", " worker ") `
    -WorkingDirectory $projectRoot | Out-Null

Start-ManagedProcess `
    -Name "celery-beat" `
    -FilePath $celeryExe `
    -ArgumentList @(
        "-A",
        "backend.queue.celery_app:celery_app",
        "beat",
        "--loglevel=info"
    ) `
    -ExistingCommandPatterns @("backend.queue.celery_app:celery_app", " beat ") `
    -WorkingDirectory $projectRoot | Out-Null

Start-ManagedProcess `
    -Name "frontend" `
    -FilePath $commandShell `
    -ArgumentList @("/d", "/c", "`"$npmCmd`" run dev") `
    -WorkingDirectory (Join-Path $projectRoot "frontend") `
    -Port 3000 | Out-Null

$frontendReady = Wait-ForPort -Port 3000 -TimeoutSec 90
$backendReady = Wait-ForPort -Port 8000 -TimeoutSec 45

if ($backendReady) {
    Write-LauncherLog "Backend is reachable on port 8000."
}
else {
    Write-LauncherLog "Backend did not report ready on port 8000 within the expected time."
}

if ($frontendReady) {
    Write-LauncherLog "Frontend is reachable on port 3000."
}
else {
    Write-LauncherLog "Frontend did not report ready on port 3000 within the expected time."
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:3000"
}
