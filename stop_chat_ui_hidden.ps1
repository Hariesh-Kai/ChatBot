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

function Get-PidPath {
    param([string]$Name)
    Join-Path $pidDir "$Name.pid"
}

function Get-DescendantProcessIds {
    param([int]$ParentId)

    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentId" -ErrorAction SilentlyContinue)
    $allIds = @()

    foreach ($child in $children) {
        $childId = [int]$child.ProcessId
        $allIds += $childId
        $allIds += Get-DescendantProcessIds -ParentId $childId
    }

    return $allIds
}

function Stop-ManagedProcess {
    param([string]$Name)

    $pidPath = Get-PidPath -Name $Name
    if (-not (Test-Path $pidPath)) {
        Write-LauncherLog "$Name had no pid file to stop."
        return
    }

    $rawValue = (Get-Content $pidPath -Raw).Trim()
    $rootPid = 0
    if (-not [int]::TryParse($rawValue, [ref]$rootPid)) {
        Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
        Write-LauncherLog "$Name pid file was invalid and has been removed."
        return
    }

    $processIds = @($rootPid) + (Get-DescendantProcessIds -ParentId $rootPid)
    $processIds = $processIds | Sort-Object -Unique -Descending

    foreach ($processId in $processIds) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-LauncherLog "$Name stopped process $processId."
        }
        catch {
            Write-LauncherLog "$Name process $processId was already stopped."
        }
    }

    Remove-Item $pidPath -Force -ErrorAction SilentlyContinue
}

foreach ($serviceName in @("frontend", "celery-beat", "celery-worker", "backend", "minio")) {
    Stop-ManagedProcess -Name $serviceName
}
