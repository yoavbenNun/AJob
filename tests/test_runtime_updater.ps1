# Standalone offline regression checks; no Pester, network, server or user data.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\update_dashboard_runtime.ps1') -LibraryOnly
$originalCommand = ${function:Invoke-RuntimeCommand}
$originalStart = ${function:Start-Runtime}
$originalSave = ${function:Save-RuntimeVersion}
$script:responses = New-Object Collections.Queue
function Invoke-RuntimeCommand { param($File, $Arguments, $Seconds)
    $value = $script:responses.Dequeue()
    if ($value -eq 'NETWORK_FAILURE') { throw 'offline' }
    return $value
}
$script:assertions = 0
function Assert($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    $script:assertions += 1
}
$sha = 'a' * 40
$old = 'b' * 40
function Queue-Run($Conclusion='success', $Event='push', $RunSha=$sha, $Status='completed') {
    $script:responses.Enqueue((@{workflow_runs=@(@{head_sha=$RunSha;head_branch='main';event=$Event;status=$Status;conclusion=$Conclusion})} | ConvertTo-Json -Depth 4))
}
function Queue-Ref { $script:responses.Enqueue((@{object=@{sha=$sha}} | ConvertTo-Json)) }
Queue-Ref
Assert ($null -eq (Get-ApprovedRuntimeSha $sha)) 'Same SHA must be a no-op'
Queue-Ref; Queue-Run; Queue-Run
Assert ((Get-ApprovedRuntimeSha $old) -eq $sha) 'Two exact push successes must pass'
foreach ($workflowPosition in 0,1) {
    foreach ($conclusion in 'failure','cancelled','skipped','neutral') {
        Queue-Ref
        if ($workflowPosition) { Queue-Run }
        Queue-Run $conclusion
        Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'CI/Security failures must be refused'
    }
}
Queue-Ref; Queue-Run 'success' 'pull_request'
Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'PR success is not approval'
Queue-Ref; Queue-Run 'success' 'push' $old
Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'Wrong SHA is not approval'
Queue-Ref; Queue-Run 'success' 'push' $sha 'in_progress'
Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'Pending checks are not approval'
Queue-Ref
$script:responses.Enqueue('{"workflow_runs":[]}')
Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'Missing workflow must not approve'
Queue-Ref
$script:responses.Enqueue((@{workflow_runs=@(@{head_sha=$sha;head_branch='other';event='push';status='completed';conclusion='success'})} | ConvertTo-Json -Depth 4))
Assert ($null -eq (Get-ApprovedRuntimeSha $old)) 'Wrong branch must not approve'
$script:responses.Enqueue('NETWORK_FAILURE')
$offline = $false
try { Get-ApprovedRuntimeSha $old } catch { $offline = $true }
Assert $offline 'Offline must take the fallback path'

$script:starts = @()
$script:saved = $false
function Start-Runtime { param($Launcher,$Root,$Code)
    $script:starts += $Code
    if ($Code -eq 'broken') { throw 'startup failed' }
}
function Save-RuntimeVersion { param($Path,$Version) $script:saved = $true }
$version = @{commit=$old;code_root='previous';python='python'}
Assert (-not (Activate-Runtime $version 'broken' $sha 'launcher' 'root' 'pointer')) 'Failed startup must roll back'
Assert (($script:starts -join ',') -eq 'broken,previous') 'Rollback must launch previous code'
Assert (-not $script:saved) 'Failed startup must not publish pointer'
$script:starts = @()
Assert (Activate-Runtime $version 'candidate' $sha 'launcher' 'root' 'pointer') 'Successful startup must publish'
Assert $script:saved 'Success must save pointer'
function Save-RuntimeVersion { throw 'atomic publish failed' }
$script:starts = @()
Assert (-not (Activate-Runtime $version 'candidate' $sha 'launcher' 'root' 'pointer')) 'Publish failure must roll back'
Assert (($script:starts -join ',') -eq 'candidate,previous') 'Publish failure must restore server'

${function:Invoke-RuntimeCommand} = $originalCommand
${function:Start-Runtime} = $originalStart
${function:Save-RuntimeVersion} = $originalSave
Assert ((Invoke-RuntimeCommand "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @('-NoProfile','-Command','Write-Output "argument with spaces"')) -eq 'argument with spaces') 'Subprocess quoting failed'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('ajob-runtime-test-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $temp)
$pointer = Join-Path $temp 'version.json'
'{"commit":"old"}' | Set-Content -LiteralPath $pointer
Save-RuntimeVersion $pointer @{commit='new';previous_commit='old'}
Assert ((Get-Content -Raw $pointer | ConvertFrom-Json).previous_commit -eq 'old') 'Atomic pointer must retain previous SHA'
$oldPath = Join-Path $temp 'old'
$newPath = Join-Path $temp 'new'
[void](New-Item -ItemType Directory -Path $oldPath, $newPath)
'unchanged' | Set-Content (Join-Path $oldPath 'requirements.txt')
'unchanged' | Set-Content (Join-Path $newPath 'requirements.txt')
Assert-RuntimeDependencies $oldPath $newPath
'changed' | Set-Content (Join-Path $newPath 'requirements.txt')
$blocked = $false
try { Assert-RuntimeDependencies $oldPath $newPath } catch { $blocked = $true }
Assert $blocked 'Changed dependencies must require manual setup'

# Exercise actual Git worktree preparation locally. Substitute only the remote;
# no GitHub writes, development refs or live databases are involved.
$fixtureRepo = Join-Path $temp 'fixture-repository'
[void](Invoke-RuntimeCommand 'git' @('init', '-b', 'main', $fixtureRepo))
'synthetic dependency fixture' | Set-Content (Join-Path $fixtureRepo 'requirements.txt')
[void](Invoke-RuntimeCommand 'git' @('-C', $fixtureRepo, 'add', 'requirements.txt'))
[void](Invoke-RuntimeCommand 'git' @('-C', $fixtureRepo, '-c', 'user.name=Runtime Test', '-c', 'user.email=runtime@example.test', 'commit', '-m', 'Synthetic runtime fixture'))
$fixtureSha = Invoke-RuntimeCommand 'git' @('-C', $fixtureRepo, 'rev-parse', 'HEAD')
function Invoke-RuntimeCommand { param($File, [string[]]$Arguments, [int]$Seconds=8)
    $Arguments = @($Arguments | ForEach-Object { if ($_ -eq 'https://github.com/yoavbenNun/AJob.git') { $fixtureRepo } else { $_ } })
    & $originalCommand $File $Arguments $Seconds
}
$prepared = Prepare-Runtime (Join-Path $temp 'runtime') $fixtureSha $fixtureRepo
Assert ((& $originalCommand 'git' @('-C', $prepared, 'rev-parse', 'HEAD')) -eq $fixtureSha) 'Prepared worktree must use exact SHA'
Assert ((Prepare-Runtime (Join-Path $temp 'runtime') $fixtureSha $fixtureRepo) -eq $prepared) 'Clean prepared worktree should be reusable'
'uncommitted' | Set-Content (Join-Path $prepared 'local-change.txt')
$dirtyRejected = $false
try { Prepare-Runtime (Join-Path $temp 'runtime') $fixtureSha $fixtureRepo } catch { $dirtyRejected = $true }
Assert $dirtyRejected 'Dirty candidate must be preserved and refused'
Assert ((Get-Content (Join-Path $prepared 'local-change.txt')) -eq 'uncommitted') 'Refusal must preserve local edits'
${function:Invoke-RuntimeCommand} = $originalCommand

# A launcher exit 0 must not publish a different/unhealthy server after a race.
function Invoke-RuntimeCommand { param($File,$Arguments,$Seconds,[switch]$IgnoreOutput) return '' }
$script:listenerCount = 1
$script:servingCode = 'C:\synthetic-runtime\.local-runtime\releases\candidate'
$script:healthName = 'AJob'
$script:healthStatus = 'ok'
$script:rawState = 0
$script:executable = 'C:\synthetic-runtime\.venv\Scripts\python.exe'
$script:parentExecutable = $script:executable
$script:serverArguments = '-m uvicorn src.web.app:app'
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction)
    if ($script:listenerCount) { [PSCustomObject]@{OwningProcess=123;CimInstanceProperties=@{State=@{Value=$script:rawState}}} }
}
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction)
    $executable = if ($Filter -eq 'ProcessId=456') { $script:parentExecutable } else { $script:executable }
    [PSCustomObject]@{ProcessId=123;ParentProcessId=456;ExecutablePath=$executable;CreationDate=[datetime]'2026-01-01';CommandLine=('"' + $executable + '" ' + $script:serverArguments + ' --app-dir "' + $script:servingCode + '" --host 127.0.0.1 --port 8001')}
}
function Invoke-RestMethod { param($Uri,$TimeoutSec) [PSCustomObject]@{name=$script:healthName;status=$script:healthStatus} }
$selected = $script:servingCode
Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected
Assert $true 'Selected runtime must be verified after launcher success'
$script:servingCode = 'C:\synthetic-runtime\.local-runtime\releases\other'
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected } catch { $refused=$true }
Assert $refused 'Wrong serving path must be refused despite exit 0'
$script:servingCode = $selected
$script:healthName = 'Other app'
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected } catch { $refused=$true }
Assert $refused 'Wrong health identity must be refused'
$script:listenerCount = 0
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected } catch { $refused=$true }
Assert $refused 'No listener must be refused despite exit 0'
$script:listenerCount = 1
$script:healthName = 'AJob'
foreach ($state in 0,2) {
    $script:rawState = $state
    Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected -Reuse
    Assert $true "Reuse must independently verify a healthy state-$state server"
}
$script:rawState = 11
Assert (@(Get-RuntimeListeners).Count -eq 0) 'TIME_WAIT is not a candidate listener'
$script:rawState = 0
foreach ($reuse in $false,$true) {
    $script:executable = 'C:\foreign\python.exe'
    $script:parentExecutable = 'C:\foreign\python.exe'
    $refused = $false
    try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected -Reuse:$reuse } catch { $refused=$true }
    Assert $refused 'Open port, copied arguments and forged AJob health cannot prove interpreter ownership'
    $script:executable = 'C:\synthetic-runtime\.venv\Scripts\python.exe'
    $script:serverArguments = '-m foreign.app'
    $refused = $false
    try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected -Reuse:$reuse } catch { $refused=$true }
    Assert $refused 'Foreign command must not pass even from the expected interpreter'
    $script:serverArguments = '-m uvicorn src.web.app:app'
    $script:servingCode = 'C:\foreign\code'
    $refused = $false
    try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected -Reuse:$reuse } catch { $refused=$true }
    Assert $refused 'Reuse must not accept a foreign code directory'
    $script:servingCode = $selected
    $script:healthStatus = 'failed'
    $refused = $false
    try { Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected -Reuse:$reuse } catch { $refused=$true }
    Assert $refused 'AJob name alone cannot establish health'
    $script:healthStatus = 'ok'
}
$script:executable = 'C:\base\python.exe'
$script:parentExecutable = 'C:\synthetic-runtime\.venv\Scripts\python.exe'
Start-Runtime 'launcher' 'C:\synthetic-runtime' $selected
Assert $true 'Windows venv parent delegation must retain ownership across interpreter paths'

# Current-pointer failures use the recorded fallback; publish only after it starts.
$script:starts = @()
$script:savedVersion = $null
function Start-Runtime { param($Launcher,$Root,$Code)
    $script:starts += $Code
    if ($Code -eq 'broken') { throw 'synthetic startup failure' }
}
function Save-RuntimeVersion { param($Path,$Version) $script:savedVersion=$Version }
$broken = @{commit=$sha;code_root='broken';previous_commit=$old;previous_code_root='healthy';python='python'}
Start-CurrentRuntime $broken 'launcher' 'root' 'pointer'
Assert (($script:starts -join ',') -eq 'broken,healthy') 'Broken active runtime must start its previous runtime'
Assert ($script:savedVersion.commit -eq $old) 'Fallback may publish only the verified previous version'
function Start-Runtime { throw 'both runtimes unavailable' }
$script:savedVersion = $null
$refused = $false
try { Start-CurrentRuntime $broken 'launcher' 'root' 'pointer' } catch { $refused=$true }
Assert ($refused -and $null -eq $script:savedVersion) 'Failed fallback must never publish an unhealthy pointer'
Write-Output "PASS: $script:assertions assertions; approval, offline, rollback, atomic publication, dependencies, real isolated Git preparation and selected-server verification."
