param(
    [string]$ResumeDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dashboardUrl = "http://127.0.0.1:8001/"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Python environment was not found at $pythonPath",
        "AI Job Search Agent"
    ) | Out-Null
    exit 1
}

function Test-DashboardReady {
    try {
        $response = Invoke-WebRequest -Uri $dashboardUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Test-DashboardReady)) {
    $escapedRoot = $projectRoot.Replace("'", "''")
    $escapedPython = $pythonPath.Replace("'", "''")
    $escapedResume = $ResumeDirectory.Replace("'", "''")
    $serverCommand = "Set-Location -LiteralPath '$escapedRoot'; "
    if ($escapedResume) {
        $serverCommand += "`$env:RESUME_DIRECTORY = '$escapedResume'; "
    }
    $serverCommand += "& '$escapedPython' -m uvicorn src.web.app:app --host 127.0.0.1 --port 8001"

    Start-Process powershell.exe -ArgumentList @(
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
            "AI Job Search Agent"
        ) | Out-Null
        exit 1
    }
}

Start-Process $dashboardUrl
