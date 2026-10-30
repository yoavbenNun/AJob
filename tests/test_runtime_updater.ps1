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
$script:servingCode = 'C:\synthetic-runtime\candidate'
$script:healthName = 'AJob'
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction)
    if ($script:listenerCount) { [PSCustomObject]@{OwningProcess=123} }
}
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction)
    [PSCustomObject]@{CommandLine=('python -m uvicorn src.web.app:app --app-dir "' + $script:servingCode + '" --host 127.0.0.1 --port 8001')}
}
function Invoke-RestMethod { param($Uri,$TimeoutSec) [PSCustomObject]@{name=$script:healthName} }
Start-Runtime 'launcher' 'C:\synthetic-runtime' 'C:\synthetic-runtime\candidate'
Assert $true 'Selected runtime must be verified after launcher success'
$script:servingCode = 'C:\synthetic-runtime\other'
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' 'C:\synthetic-runtime\candidate' } catch { $refused=$true }
Assert $refused 'Wrong serving path must be refused despite exit 0'
$script:servingCode = 'C:\synthetic-runtime\candidate'
$script:healthName = 'Other app'
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' 'C:\synthetic-runtime\candidate' } catch { $refused=$true }
Assert $refused 'Wrong health identity must be refused'
$script:listenerCount = 0
$refused = $false
try { Start-Runtime 'launcher' 'C:\synthetic-runtime' 'C:\synthetic-runtime\candidate' } catch { $refused=$true }
Assert $refused 'No listener must be refused despite exit 0'
Write-Output "PASS: $script:assertions assertions; approval, offline, rollback, atomic publication, dependencies, real isolated Git preparation and selected-server verification."
