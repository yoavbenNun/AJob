# Offline function-level checks. Never execute launcher startup, use port 8001,
# read private configuration, or start/stop a real process.
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$source = Join-Path $PSScriptRoot '..\scripts\launch_published_dashboard.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Published launcher must parse in Windows PowerShell.' }
# Loading only function definitions also finds Test-DashboardReady inside try.
foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}
$script:assertions = 0
function Assert($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    $script:assertions += 1
}
function New-Connection([int]$Owner, [int]$RawState) {
    [pscustomobject]@{OwningProcess=$Owner; CimInstanceProperties=@{State=@{Value=$RawState}}}
}
$projectRoot = 'C:\synthetic-ajob'
$runtimeRoot = Join-Path $projectRoot '.local-runtime'
$appRoot = Join-Path $runtimeRoot 'releases\candidate'
$releasePrefix = (Join-Path $runtimeRoot 'releases') + '\'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$databaseCredentialPath = Join-Path $projectRoot 'config\database_url.cred'
$healthUrl = 'http://127.0.0.1:8001/health'
$dashboardUrl = 'http://127.0.0.1:8001/'
$ReuseRunning = $false
$env:DATABASE_URL = $null
$script:connections = @()
$script:processes = @{}
$script:requests = @()
$script:healthName = 'AJob'
$script:stopped = @()
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) $script:connections }
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction)
    $script:processes[[int]($Filter -replace '^ProcessId=', '')]
}
function Test-Path { param($LiteralPath) return $false }
function Invoke-WebRequest { param($Uri,[switch]$UseBasicParsing,$TimeoutSec)
    $script:requests += $Uri
    if ($Uri -eq $healthUrl) {
        return [pscustomobject]@{StatusCode=200;Content=(@{name=$script:healthName;started_at='2026-01-01T00:00:00Z';job_id=7;storage='sqlite'} | ConvertTo-Json)}
    }
    return [pscustomobject]@{StatusCode=200;Content='synthetic page'}
}
function Stop-Process { param($Id,[switch]$Force,$ErrorAction) $script:stopped += $Id; $script:connections=@() }
function Start-Sleep { param($Milliseconds) }
function New-Server([string]$Executable=$pythonPath, [string]$Code=$appRoot, [int]$ProcessId=123) {
    [pscustomobject]@{ProcessId=$ProcessId;ParentProcessId=456;ExecutablePath=$Executable;CreationDate=[datetime]'2026-01-01';CommandLine=('"' + $Executable + '" -m uvicorn src.web.app:app --app-dir "' + $Code + '" --host 127.0.0.1 --port 8001')}
}
foreach ($state in 0,2) {
    $script:connections = @(New-Connection 123 $state)
    $script:processes = @{123=(New-Server)}
    $script:requests = @()
    Assert (@(Get-DashboardListeners).Count -eq 1) "Raw TCP state $state must identify a candidate listener"
    Assert (Test-DashboardReady) "Owned healthy raw-state-$state server must be ready"
    Assert (($script:requests -join ',') -eq "$healthUrl,$dashboardUrl,http://127.0.0.1:8001/jobs/7") 'Readiness must probe health, home and representative detail'
}
$script:connections = @((New-Connection 123 0),(New-Connection 123 2),(New-Connection 0 0),(New-Connection 999 11))
Assert (@(Get-DashboardListeners).Count -eq 1) 'Deduplicate owner and ignore PID 0/TIME_WAIT'
Assert (Test-DashboardReady) 'Extra TIME_WAIT and duplicate rows must not hide the healthy owner'
$script:connections += New-Connection 789 2
Assert (-not (Test-DashboardReady)) 'Different listener owners must fail readiness'
$script:connections = @(New-Connection 123 0)
$script:processes[123] = New-Server 'C:\foreign\python.exe'
Assert (-not (Test-DashboardReady)) 'Foreign interpreter must fail even with AJob health and port 8001'
$refused = $false
try { Stop-OwnedDashboard $script:connections[0] } catch { $refused=$true }
Assert ($refused -and $script:stopped.Count -eq 0) 'Foreign owner must never be stopped'
$script:processes[123] = New-Server $pythonPath 'C:\foreign\application'
Assert ($null -eq (Get-AJobProcess 123)) 'Foreign application directory must not prove ownership'
$script:processes[123] = New-Server $pythonPath (Join-Path $runtimeRoot 'releases\previous')
Assert (-not (Test-DashboardReady)) 'Owned but different runtime must fail selected-runtime readiness'
$ReuseRunning = $true
Assert (Test-DashboardReady) 'Reuse may accept an existing verified owned runtime'
$script:processes[123] = New-Server 'C:\foreign\python.exe'
Assert (-not (Test-DashboardReady)) 'Reuse must still verify interpreter ownership'
$ReuseRunning = $false
$script:processes[123] = New-Server 'C:\base-python\python.exe'
$script:processes[456] = New-Server $pythonPath $appRoot 456
Assert ($null -ne (Get-AJobProcess 123)) 'Windows venv interpreter delegation must prove ownership'
$script:processes[456].CreationDate = [datetime]'2026-01-02'
Assert ($null -eq (Get-AJobProcess 123)) 'A newer recycled parent must not establish ownership'
$script:processes = @{123=(New-Server)}
$script:healthName = 'Other application'
Assert (-not (Test-DashboardReady)) 'Owned process must still serve AJob health'
$script:healthName = 'AJob'
Stop-OwnedDashboard $script:connections[0]
Assert (($script:stopped -join ',') -eq '123') 'Only verified current owner may be stopped'

# Execute the complete startup path in a child shell with synthetic filesystem
# and process fixtures. Every network/process operation is replaced; no server,
# credentials or production port is used. A dead child must fail before sleeping.
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('ajob-startup-' + [guid]::NewGuid().ToString('N'))
$fixtureCode = Join-Path $fixture '.local-runtime\code\src\web'
[void][IO.Directory]::CreateDirectory($fixtureCode)
[void][IO.Directory]::CreateDirectory((Join-Path $fixture '.venv\Scripts'))
[void][IO.Directory]::CreateDirectory((Join-Path $fixture 'config'))
[IO.File]::WriteAllText((Join-Path $fixtureCode 'app.py'), '# synthetic, never imported')
[IO.File]::WriteAllText((Join-Path $fixture '.venv\Scripts\python.exe'), 'never executed')
$fixtureLauncher = Join-Path $fixture 'launcher.ps1'
$launcherText = [IO.File]::ReadAllText($source).Replace('Local\AJobDashboardLauncher', ('Local\AJobStartupTest' + [guid]::NewGuid().ToString('N')))
[IO.File]::WriteAllText($fixtureLauncher, $launcherText)
$wrapper = @'
param($Launcher,$Root)
$ErrorActionPreference='Stop'
$env:DATABASE_URL=$null
$env:RESUME_DIRECTORY=$null
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) }
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) }
function Start-Process { param($FilePath,$WindowStyle,$ArgumentList,$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    [pscustomobject]@{Id=9001;HasExited=$true;StartTime=[datetime]::Now}
}
function Stop-Process { throw 'No process should be stopped in the dead-child fixture' }
function Start-Sleep { throw 'An exited startup must fail before polling/sleeping' }
& $Launcher -DataRoot $Root -NoBrowser -NonInteractive
exit $LASTEXITCODE
'@
$wrapperPath = Join-Path $fixture 'run.ps1'
[IO.File]::WriteAllText($wrapperPath, $wrapper)
$result = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $wrapperPath -Launcher $fixtureLauncher -Root $fixture 2>&1
Assert ($LASTEXITCODE -eq 1) 'Exited startup must return failure'
Assert (($result -join "`n") -like '*did not become ready*') 'Dead child must reach controlled startup failure without waiting or stopping unrelated processes'
Write-Output "PASS: $script:assertions assertions; raw TCP states, listener deduplication, ownership, reuse, readiness and safe termination."
# The expected failed child leaves LASTEXITCODE=1. Report this test script's
# successful assertions explicitly to callers such as the Actions shell wrapper.
exit 0
