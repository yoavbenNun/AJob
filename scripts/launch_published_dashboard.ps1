param(
    [string]$ResumeDirectory = "",
    [switch]$NoBrowser,
    [switch]$NonInteractive,
    [string]$RuntimeCodeRoot = "",
    [string]$DataRoot = "",
    [switch]$ReuseRunning
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1")
$projectRoot = if ($DataRoot) { (Resolve-Path -LiteralPath $DataRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$runtimeRoot = Join-Path $projectRoot ".local-runtime"
$appRoot = if ($RuntimeCodeRoot) { [IO.Path]::GetFullPath($RuntimeCodeRoot) } else { Join-Path $runtimeRoot "code" }
$releasePrefix = (Join-Path $runtimeRoot "releases") + "\"
if ($appRoot -ine (Join-Path $runtimeRoot "code") -and -not $appRoot.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Untrusted runtime path." }
if (-not (Test-Path -LiteralPath (Join-Path $appRoot "src\web\app.py"))) { throw "The published AJob runtime is missing." }
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

function Show-LauncherError([string]$Message) {
    if ($NonInteractive) {
        Write-Output $Message
    }
    else {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($Message, "AJob") | Out-Null
    }
}

function Get-DashboardListeners {
    # Include wildcard listeners: 0.0.0.0 can also prevent a loopback bind.
    @(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
}

function Get-AJobProcess([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $signature = '(?i)(?:^|\s)-m\s+uvicorn\s+src\.web\.app:app(?:\s|$)'
    if ($process.CommandLine -notmatch $signature -or
        $process.CommandLine -notmatch '(?:^|\s)--host\s+127\.0\.0\.1(?:\s|$)' -or
        $process.CommandLine -notmatch '(?:^|\s)--port\s+8001(?:\s|$)') { return $null }
    $interpreterVerified = $process.ExecutablePath -ieq $pythonPath
    $launcherProcessId = $process.ParentProcessId
    # Windows venv python.exe delegates to a base-interpreter child. Require
    # the project-specific venv parent and the same server command, not a PID file.
    if (-not $interpreterVerified) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent -and $parent.ExecutablePath -ieq $pythonPath -and
            $parent.CommandLine -match $signature -and
            $parent.CreationDate -le $process.CreationDate) {
            $interpreterVerified = $true
            $launcherProcessId = $parent.ParentProcessId
        }
    }
    if (-not $interpreterVerified) { return $null }
    if ($process.CommandLine -match '(?:^|\s)--app-dir\s+(?:"([^"]+)"|(\S+))') {
        $appDirectory = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
        if ([IO.Path]::GetFullPath($appDirectory).TrimEnd('\') -iin @($projectRoot.TrimEnd('\'), (Join-Path $runtimeRoot "code").TrimEnd('\')) -or [IO.Path]::GetFullPath($appDirectory).StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            return $process
        }
        return $null
    }
    # Recognize the exact project-root command used by the previous launcher.
    $legacyLauncher = Get-CimInstance Win32_Process -Filter "ProcessId=$launcherProcessId" -ErrorAction SilentlyContinue
    $rootCommand = "Set-Location -LiteralPath '" + $projectRoot.Replace("'", "''") + "'"
    if ($legacyLauncher -and $legacyLauncher.CommandLine.Contains($rootCommand) -and
        $legacyLauncher.CommandLine.Contains($pythonPath)) { return $process }
    return $null
}

function Stop-OwnedDashboard($Listener) {
    $owned = Get-AJobProcess ([int]$Listener.OwningProcess)
    if (-not $owned) { throw 'The port owner could not be verified as this AJob installation.' }
    # Recheck creation time and port ownership immediately before termination;
    # persisted PIDs can be stale or recycled after a Windows restart.
    $current = Get-AJobProcess ([int]$owned.ProcessId)
    $stillListening = @(Get-DashboardListeners | Where-Object { $_.OwningProcess -eq $owned.ProcessId })
    if (-not $current -or $current.CreationDate -ne $owned.CreationDate -or -not $stillListening) {
        throw 'The port owner changed during launch; no process was stopped.'
    }
    Stop-Process -Id $owned.ProcessId -Force -ErrorAction Stop
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ((Get-DashboardListeners).Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
}

try {
    # A desktop double-click can start two launcher processes. Do not queue the
    # second process: waiting would make it open a second browser tab after the
    # first launcher releases the mutex.
    try { $lockAcquired = $launcherMutex.WaitOne([TimeSpan]::Zero) }
    catch [System.Threading.AbandonedMutexException] { $lockAcquired = $true }
    if (-not $lockAcquired) {
        exit 0
    }

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        Show-LauncherError "The AJob Python environment was not found. Run the local setup first."
        exit 1
    }

function Test-DashboardReady {
    try {
        $listeners = @(Get-DashboardListeners)
        if ($listeners.Count -ne 1 -or -not (Get-AJobProcess ([int]$listeners[0].OwningProcess))) {
            return $false
        }
        # A healthy development server is still the wrong published runtime.
        $owner = Get-AJobProcess ([int]$listeners[0].OwningProcess)
        if ($owner.CommandLine -notmatch '(?:^|\s)--app-dir\s+(?:"([^"]+)"|(\S+))') { return $false }
        $runningRoot = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
        if (-not $ReuseRunning -and [IO.Path]::GetFullPath($runningRoot).TrimEnd('\') -ine $appRoot.TrimEnd('\')) { return $false }
        $healthResponse = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 6
        $health = $healthResponse.Content | ConvertFrom-Json
        if ($healthResponse.StatusCode -ne 200 -or $health.name -ne "AJob") {
            return $false
        }
        if ([string]::IsNullOrWhiteSpace([string]$health.started_at)) {
            return $false
        }
        $serverStartedAt = [DateTimeOffset]::Parse([string]$health.started_at).UtcDateTime
        $dashboardSources = @(
            (Join-Path $appRoot "src\web\app.py"),
            (Join-Path $appRoot "src\web\templates\dashboard.html"),
            (Join-Path $appRoot "src\web\static\app.css"),
            (Join-Path $appRoot "src\web\static\ui.js"),
            (Join-Path $appRoot "config\companies.yaml"),
            (Join-Path $appRoot "src\web\security.py")
        )
        if (Test-Path -LiteralPath $databaseCredentialPath) {
            $dashboardSources += $databaseCredentialPath
        }
        foreach ($sourcePath in $dashboardSources) {
            if (-not $ReuseRunning -and (Test-Path -LiteralPath $sourcePath) -and (Get-Item -LiteralPath $sourcePath).LastWriteTimeUtc -gt $serverStartedAt) {
                return $false
            }
        }
        if (($env:DATABASE_URL -or (Test-Path -LiteralPath $databaseCredentialPath)) -and $health.storage -ne "postgres") {
            return $false
        }
        $response = Invoke-WebRequest -Uri $dashboardUrl -UseBasicParsing -TimeoutSec 15
        if ($response.StatusCode -ne 200) {
            return $false
        }
        if ($null -ne $health.job_id) {
            $detailUrl = "http://127.0.0.1:8001/jobs/$($health.job_id)"
            $detailResponse = Invoke-WebRequest -Uri $detailUrl -UseBasicParsing -TimeoutSec 10
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
    if ($ReuseRunning -and (Get-DashboardListeners).Count -gt 0) {
        throw 'An existing server is in use or unavailable; no process was stopped.'
    }
    $existingListeners = @(Get-DashboardListeners)
    if ($existingListeners.Count -gt 0) {
        if ($existingListeners.Count -ne 1 -or -not (Get-AJobProcess ([int]$existingListeners[0].OwningProcess))) {
            Show-LauncherError "Port 8001 belongs to a different application. AJob did not stop it. Close that application or free port 8001, then open AJob again."
            exit 1
        }
        Stop-OwnedDashboard $existingListeners[0]
        if ((Get-DashboardListeners).Count -gt 0) {
            Show-LauncherError "Port 8001 changed owner while AJob was restarting. No additional process was stopped."
            exit 1
        }
    }
    $previousDatabaseUrl = $env:DATABASE_URL
    $previousResumeDirectory = $env:RESUME_DIRECTORY
    try {
        if (Test-Path -LiteralPath $databaseCredentialPath) {
            $secureDatabaseUrl = ConvertTo-SecureString -String (Get-Content -LiteralPath $databaseCredentialPath -Raw -Encoding ASCII).Trim()
            $databaseUrlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureDatabaseUrl)
            try { $env:DATABASE_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($databaseUrlPointer) }
            finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($databaseUrlPointer)
                $secureDatabaseUrl.Dispose()
            }
        }
        if ($ResumeDirectory) { $env:RESUME_DIRECTORY = $ResumeDirectory }
        $logRoot = Join-Path $projectRoot "data"
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        # Launch Python directly: -NoExit shells from older launches stayed alive
        # even after their server failed. The child inherits credentials in memory.
        $serverArguments = '-m uvicorn src.web.app:app --app-dir "{0}" --host 127.0.0.1 --port 8001' -f $appRoot
        $launchedServer = Start-Process -FilePath $pythonPath -WindowStyle Hidden -ArgumentList $serverArguments -PassThru `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput (Join-Path $logRoot "dashboard.stdout.log") `
            -RedirectStandardError (Join-Path $logRoot "dashboard.stderr.log")
    }
    catch {
        Show-LauncherError "AJob could not start. Check the local Python setup and saved database connection for this Windows account."
        exit 1
    }
    finally {
        $env:DATABASE_URL = $previousDatabaseUrl
        $env:RESUME_DIRECTORY = $previousResumeDirectory
    }

    $ready = $false
    $startupDeadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $startupDeadline) {
        Start-Sleep -Milliseconds 500
        if (Test-DashboardReady) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        # Reap only this attempt's verified venv process/child, even if it never
        # bound the port. Failed candidates must not linger beside the rollback.
        $attemptProcesses = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($launchedServer.Id)" -ErrorAction SilentlyContinue)
        $attemptProcesses += @(Get-CimInstance Win32_Process -Filter "ProcessId=$($launchedServer.Id)" -ErrorAction SilentlyContinue)
        foreach ($attempt in $attemptProcesses) {
            $ownedAttempt = Get-AJobProcess ([int]$attempt.ProcessId)
            if ($ownedAttempt -and $ownedAttempt.CreationDate -eq $attempt.CreationDate -and
                $ownedAttempt.CreationDate -ge $launchedServer.StartTime.AddSeconds(-1) -and
                $ownedAttempt.CommandLine.Contains('--app-dir "' + $appRoot + '"')) {
                Stop-Process -Id $ownedAttempt.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Show-LauncherError "The AJob dashboard did not become ready. Startup details are in data/dashboard.stderr.log. No unrelated process was stopped."
        exit 1
    }
}

$activeListeners = @(Get-DashboardListeners)
if ($activeListeners.Count -eq 1 -and (Get-AJobProcess ([int]$activeListeners[0].OwningProcess))) {
    Set-Content -LiteralPath $serverPidPath -Value $activeListeners[0].OwningProcess -Encoding ASCII
}

if (-not $NoBrowser) { Start-Process $dashboardUrl }
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
