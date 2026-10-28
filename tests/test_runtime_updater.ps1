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
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
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
Write-Output 'PASS: exact-SHA approvals, same version, failed/pending/PR checks, offline, startup/publish rollback, command quoting, atomic pointer.'
