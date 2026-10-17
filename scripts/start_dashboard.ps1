param(
    [string]$ResumeDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$localSettingsPath = Join-Path $projectRoot "config\local_settings.txt"
if (-not $ResumeDirectory -and (Test-Path -LiteralPath $localSettingsPath)) {
    $ResumeDirectory = (Get-Content -LiteralPath $localSettingsPath -Raw -Encoding UTF8).Trim()
}
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$databaseCredentialPath = Join-Path $projectRoot "config\database_url.cred"
$dashboardUrl = "http://127.0.0.1:8001/"
$healthUrl = "http://127.0.0.1:8001/health"
$serverPidPath = Join-Path $projectRoot "config\local_server.pid"
$launcherMutex = New-Object System.Threading.Mutex($false, "Local\AJobDashboardLauncher")
$lockAcquired = $false

try {
    # A desktop double-click can start two launcher processes. Do not queue the
    # second process: waiting would make it open a second browser tab after the
    # first launcher releases the mutex.
    $lockAcquired = $launcherMutex.WaitOne([TimeSpan]::Zero)
    if (-not $lockAcquired) {
        exit 0
    }

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "Python environment was not found at $pythonPath",
            "AJob"
        ) | Out-Null
        exit 1
    }

    if (Test-Path -LiteralPath $databaseCredentialPath) {
        try {
            $encryptedDatabaseUrl = Get-Content -LiteralPath $databaseCredentialPath -Raw -Encoding ASCII
            $secureDatabaseUrl = ConvertTo-SecureString -String $encryptedDatabaseUrl.Trim()
            $databaseUrlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureDatabaseUrl)
            try {
                $env:DATABASE_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($databaseUrlPointer)
            }
            finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($databaseUrlPointer)
                $secureDatabaseUrl.Dispose()
            }
        }
        catch {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show(
                "The saved database connection could not be decrypted for this Windows account. Run scripts/import_database_url.ps1 again.",
                "AJob"
            ) | Out-Null
            exit 1
        }
    }

function Test-DashboardReady {
    try {
        $healthResponse = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        $health = $healthResponse.Content | ConvertFrom-Json
        if ($healthResponse.StatusCode -ne 200 -or $health.name -ne "AJob") {
            return $false
        }
        if ([string]::IsNullOrWhiteSpace([string]$health.started_at)) {
            return $false
        }
        $serverStartedAt = [DateTimeOffset]::Parse([string]$health.started_at).UtcDateTime
        $dashboardSources = @(
            (Join-Path $projectRoot "src\web\app.py"),
            (Join-Path $projectRoot "src\web\templates\dashboard.html"),
            (Join-Path $projectRoot "src\web\static\app.css"),
            (Join-Path $projectRoot "src\web\static\ui.js"),
            (Join-Path $projectRoot "config\companies.yaml")
        )
        if (Test-Path -LiteralPath $databaseCredentialPath) {
            $dashboardSources += $databaseCredentialPath
        }
        foreach ($sourcePath in $dashboardSources) {
            if ((Get-Item -LiteralPath $sourcePath).LastWriteTimeUtc -gt $serverStartedAt) {
                return $false
            }
        }
        if ($env:DATABASE_URL -and $health.storage -ne "postgres") {
            return $false
        }
        $response = Invoke-WebRequest -Uri $dashboardUrl -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -ne 200) {
            return $false
        }
        if ($null -ne $health.job_id) {
            $detailUrl = "http://127.0.0.1:8001/jobs/$($health.job_id)"
            $detailResponse = Invoke-WebRequest -Uri $detailUrl -UseBasicParsing -TimeoutSec 3
            if ($detailResponse.StatusCode -ne 200) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-DashboardReady)) {
    $existingListener = Get-NetTCPConnection `
        -LocalAddress 127.0.0.1 `
        -LocalPort 8001 `
        -State Listen `
        -ErrorAction SilentlyContinue
    if ($existingListener) {
        $recordedPid = 0
        if (Test-Path -LiteralPath $serverPidPath) {
            [void][int]::TryParse(
                (Get-Content -LiteralPath $serverPidPath -Raw).Trim(),
                [ref]$recordedPid
            )
        }
        if ($recordedPid -ne $existingListener.OwningProcess) {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show(
                "Port 8001 is being used by another application. Close it, then open AJob again.",
                "AJob"
            ) | Out-Null
            exit 1
        }
        Stop-Process -Id $existingListener.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    $escapedRoot = $projectRoot.Replace("'", "''")
    $escapedPython = $pythonPath.Replace("'", "''")
    $escapedResume = $ResumeDirectory.Replace("'", "''")
    $serverCommand = "Set-Location -LiteralPath '$escapedRoot'; "
    if ($escapedResume) {
        $serverCommand += "`$env:RESUME_DIRECTORY = '$escapedResume'; "
    }
    $serverCommand += "& '$escapedPython' -m uvicorn src.web.app:app --host 127.0.0.1 --port 8001"

    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $serverCommand
    ) -WorkingDirectory $projectRoot

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-DashboardReady) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "The dashboard server did not become ready. Check the PowerShell window for details.",
            "AJob"
        ) | Out-Null
        exit 1
    }
}

$activeListener = Get-NetTCPConnection `
    -LocalAddress 127.0.0.1 `
    -LocalPort 8001 `
    -State Listen `
    -ErrorAction SilentlyContinue
if ($activeListener) {
    Set-Content -LiteralPath $serverPidPath -Value $activeListener.OwningProcess -Encoding ASCII
}

Start-Process $dashboardUrl
# Keep the mutex briefly after dispatching the URL so a near-simultaneous
# launcher process is coalesced instead of opening another tab.
Start-Sleep -Milliseconds 1500
}
finally {
    if ($lockAcquired) {
        [void]$launcherMutex.ReleaseMutex()
    }
    $launcherMutex.Dispose()
}
