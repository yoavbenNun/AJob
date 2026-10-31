param(
    [string]$DataRoot = "",
    [string]$ResumeDirectory = "",
    [switch]$NoBrowser,
    [switch]$NonInteractive,
    [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'
# Desktop Windows PowerShell may not auto-discover Get-FileHash. Resolve its
# module from this shell's PSHOME instead of relying on session discovery.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')

function Invoke-RuntimeCommand([string]$File, [string[]]$Arguments, [int]$Seconds = 8, [switch]$IgnoreOutput) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($Seconds * 1000)) {
            # Terminate only this updater-owned command tree, never a server.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F 2>$null | Out-Null
            throw 'Runtime command timed out.'
        }
        if ($process.ExitCode -ne 0) { throw 'Runtime command failed.' }
        # A detached server can inherit a pipe handle after its launcher exits.
        # Startup is signalled by exit status/readiness, not stdout reaching EOF.
        if ($IgnoreOutput) { return '' }
        if (-not $output.Wait(2000)) { throw 'Runtime command output timed out.' }
        return $output.GetAwaiter().GetResult().Trim()
    }
    finally { $process.Dispose() }
}

function Get-ApprovedRuntimeSha([string]$Current) {
    $gh = (Get-Command gh -ErrorAction Stop).Source
    $reference = Invoke-RuntimeCommand $gh @('api', 'repos/yoavbenNun/AJob/git/ref/heads/main')
    $sha = ($reference | ConvertFrom-Json).object.sha
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw 'Invalid main SHA.' }
    if ($sha -eq $Current) { return $null }
    foreach ($workflow in @('ci.yml', 'security.yml')) {
        $endpoint = "repos/yoavbenNun/AJob/actions/workflows/$workflow/runs?branch=main&event=push&head_sha=$sha&per_page=1"
        $response = (Invoke-RuntimeCommand $gh @('api', $endpoint)) | ConvertFrom-Json
        $runs = @($response.workflow_runs)
        if ($runs.Count -ne 1 -or $runs[0].head_sha -ne $sha -or
            $runs[0].head_branch -ne 'main' -or $runs[0].event -ne 'push' -or
            $runs[0].status -ne 'completed' -or $runs[0].conclusion -ne 'success') {
            return $null
        }
    }
    return $sha
}

function Assert-RuntimeDependencies([string]$Previous, [string]$Candidate) {
    $names = @('requirements.txt', 'pyproject.toml', 'setup.py', 'setup.cfg', '.python-version', 'uv.lock', 'poetry.lock', 'Pipfile', 'Pipfile.lock')
    $names += @(Get-ChildItem -LiteralPath $Previous, $Candidate -Filter 'requirements*.txt' -File | Select-Object -ExpandProperty Name)
    foreach ($name in ($names | Select-Object -Unique)) {
        $old = Join-Path $Previous $name
        $new = Join-Path $Candidate $name
        if ((Test-Path -LiteralPath $old) -ne (Test-Path -LiteralPath $new)) { throw 'Dependencies changed; manual update required.' }
        if ((Test-Path -LiteralPath $old) -and (Get-FileHash -LiteralPath $old).Hash -ne (Get-FileHash -LiteralPath $new).Hash) {
            throw 'Dependencies changed; manual update required.'
        }
    }
}

function Prepare-Runtime([string]$Root, [string]$Sha, [string]$Previous) {
    $git = (Get-Command git -ErrorAction Stop).Source
    $repo = Join-Path $Root 'repository.git'
    if (-not (Test-Path -LiteralPath $repo)) { [void](Invoke-RuntimeCommand $git @('init', '--bare', $repo)) }
    $candidate = Join-Path (Join-Path $Root 'releases') $Sha
    if (-not (Test-Path -LiteralPath $candidate)) {
        [void](Invoke-RuntimeCommand $git @('--git-dir', $repo, 'fetch', '--depth=1', 'https://github.com/yoavbenNun/AJob.git', 'main') 45)
        $fetched = Invoke-RuntimeCommand $git @('--git-dir', $repo, 'rev-parse', 'FETCH_HEAD')
        if ($fetched -ne $Sha) { throw 'main changed during preparation; defer update.' }
        [void](Invoke-RuntimeCommand $git @('--git-dir', $repo, 'worktree', 'add', '--detach', $candidate, $Sha) 20)
    }
    $actual = Invoke-RuntimeCommand $git @('-C', $candidate, 'rev-parse', 'HEAD')
    $dirty = Invoke-RuntimeCommand $git @('-C', $candidate, 'status', '--porcelain', '--untracked-files=all')
    if ($actual -ne $Sha -or $dirty) { throw 'Candidate runtime is not clean.' }
    Assert-RuntimeDependencies $Previous $candidate
    return $candidate
}

function Save-RuntimeVersion([string]$Path, $Version) {
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $Version | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
        [IO.File]::Replace($temporary, $Path, [NullString]::Value)
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}

function Get-RuntimeListeners {
    # Raw state 0 has been observed on a serving Windows socket. These are only
    # candidates: ownership and HTTP identity must still be established.
    @(Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -gt 0 -and $_.CimInstanceProperties['State'].Value -in @(0, 2) } |
        Group-Object OwningProcess | ForEach-Object { $_.Group[0] })
}

function Start-Runtime([string]$Launcher, [string]$Root, [string]$Code, [switch]$Reuse) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Launcher,
                   '-DataRoot', $Root, '-RuntimeCodeRoot', $Code, '-NoBrowser', '-NonInteractive')
    if ($Reuse) { $arguments += '-ReuseRunning' }
    if ($ResumeDirectory) { $arguments += @('-ResumeDirectory', $ResumeDirectory) }
    [void](Invoke-RuntimeCommand "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" $arguments 90 -IgnoreOutput)
    # Independently verify every launch, including reuse.
    # A concurrent legacy launcher can release the child with exit 0 without
    # starting our code. Never publish based on that exit status alone.
    $listeners = @(Get-RuntimeListeners)
    if ($listeners.Count -ne 1) { throw 'Selected runtime has no unique listener.' }
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)" -ErrorAction Stop
    $signature = '(?i)(?:^|\s)-m\s+uvicorn\s+src\.web\.app:app(?:\s|$)'
    if ($owner.CommandLine -notmatch $signature -or
        $owner.CommandLine -notmatch '(?:^|\s)--host\s+127\.0\.0\.1(?:\s|$)' -or
        $owner.CommandLine -notmatch '(?:^|\s)--port\s+8001(?:\s|$)') {
        throw 'Selected listener is not an AJob server.'
    }
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    if ($owner.ExecutablePath -ine $python) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.ParentProcessId)" -ErrorAction Stop
        if (-not $parent -or $parent.ExecutablePath -ine $python -or
            ($parent.CommandLine -replace '^\s*(?:"[^"]+"|\S+)\s*', '') -ine
                ($owner.CommandLine -replace '^\s*(?:"[^"]+"|\S+)\s*', '') -or
            $parent.CreationDate -gt $owner.CreationDate) {
            throw 'Selected listener interpreter cannot be verified.'
        }
    }
    if ($owner.CommandLine -notmatch '(?:^|\s)--app-dir\s+(?:"([^"]+)"|(\S+))') { throw 'Runtime directory cannot be verified.' }
    $runningCode = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    $runningCode = [IO.Path]::GetFullPath($runningCode).TrimEnd('\')
    $runtimeRoot = Join-Path $Root '.local-runtime'
    $releasePrefix = (Join-Path $runtimeRoot 'releases') + '\'
    if ($runningCode -ine (Join-Path $runtimeRoot 'code') -and
        -not $runningCode.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Selected listener is outside the published runtime.'
    }
    if (-not $Reuse -and [IO.Path]::GetFullPath($runningCode).TrimEnd('\') -ine [IO.Path]::GetFullPath($Code).TrimEnd('\')) {
        throw 'A different runtime is listening.'
    }
    $health = Invoke-RestMethod 'http://127.0.0.1:8001/health' -TimeoutSec 6
    if ($health.name -ne 'AJob' -or $health.status -ne 'ok') { throw 'Selected runtime is unhealthy.' }
}

function Test-RuntimeImport([string]$Python, [string]$Candidate) {
    # No cloud configuration, user files or live database in the preparation probe.
    $probe = 'import os,sys,tempfile; os.environ.pop("DATABASE_URL",None); os.environ.pop("RESUME_DIRECTORY",None); temporary=tempfile.TemporaryDirectory(); os.chdir(temporary.name); sys.path.insert(0,sys.argv[1]); from src.web.app import app; from fastapi.testclient import TestClient; client=TestClient(app,base_url="http://127.0.0.1"); assert client.get("/health").status_code==200; assert client.get("/").status_code==200'
    [void](Invoke-RuntimeCommand $Python @('-I', '-c', $probe, $Candidate) 20)
}

function Start-CurrentRuntime($Version, [string]$Launcher, [string]$Root, [string]$VersionPath) {
    try { Start-Runtime $Launcher $Root $Version.code_root }
    catch {
        if (-not $Version.previous_code_root -or $Version.previous_code_root -eq $Version.code_root) { throw }
        Start-Runtime $Launcher $Root $Version.previous_code_root
        $restored = [ordered]@{commit=$Version.previous_commit; code_root=$Version.previous_code_root;
            previous_commit=$Version.commit; previous_code_root=$Version.code_root;
            data_working_directory=$Root; python=$Version.python}
        Save-RuntimeVersion $VersionPath $restored
    }
}

function Activate-Runtime($Version, [string]$Candidate, [string]$Sha, [string]$Launcher, [string]$Root, [string]$VersionPath) {
    try {
        Start-Runtime $Launcher $Root $Candidate
        $next = [ordered]@{commit=$Sha; code_root=$Candidate; previous_commit=$Version.commit;
            previous_code_root=$Version.code_root; data_working_directory=$Root; python=$Version.python}
        Save-RuntimeVersion $VersionPath $next
        return $true
    }
    catch {
        # The old pointer remains intact until candidate readiness AND atomic publish.
        Start-CurrentRuntime $Version $Launcher $Root $VersionPath
        return $false
    }
}

if ($LibraryOnly) { return }

$root = if ($DataRoot) { (Resolve-Path -LiteralPath $DataRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$runtime = Join-Path $root '.local-runtime'
$versionPath = Join-Path $runtime 'runtime-version.json'
$launcher = Join-Path $runtime 'launch_published_dashboard.ps1'
$mutex = New-Object Threading.Mutex($false, 'Local\AJobRuntimeUpdate')
$locked = $false
try {
    try { $locked = $mutex.WaitOne([TimeSpan]::Zero) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { exit 0 }
    $version = Get-Content -LiteralPath $versionPath -Raw | ConvertFrom-Json
    if (@(Get-RuntimeListeners).Count) {
        # Any live listener prevents updating. The launcher verifies ownership.
        Start-Runtime $launcher $root $version.code_root -Reuse
    }
    else {
        $candidate = $null
        try {
            $git = (Get-Command git -ErrorAction Stop).Source
            if (Invoke-RuntimeCommand $git @('-C', $version.code_root, 'status', '--porcelain', '--untracked-files=all')) {
                throw 'Active runtime has local changes; defer update.'
            }
            $sha = Get-ApprovedRuntimeSha $version.commit
            if ($sha) {
                $prepared = Prepare-Runtime $runtime $sha $version.code_root
                Test-RuntimeImport $version.python $prepared
                $candidate = $prepared
            }
        }
        catch { Write-Output 'Update unavailable; using the existing AJob runtime.' }
        if ($candidate) {
            # Recheck after network/preparation in case a server appeared meanwhile.
            if (@(Get-RuntimeListeners).Count) {
                Start-Runtime $launcher $root $version.code_root -Reuse
            }
            else { [void](Activate-Runtime $version $candidate $sha $launcher $root $versionPath) }
        }
        else { Start-CurrentRuntime $version $launcher $root $versionPath }
    }
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8001/' }
    Start-Sleep -Milliseconds 1500
}
catch {
    $failure = [ordered]@{time=[DateTime]::UtcNow.ToString('o'); category=$_.FullyQualifiedErrorId; line=$_.InvocationInfo.ScriptLineNumber}
    $failure | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $root 'data\runtime-updater.log') -Encoding UTF8
    if ($NonInteractive) { Write-Output 'AJob could not start; see data/runtime-updater.log and data/dashboard.stderr.log.' }
    else {
        Add-Type -AssemblyName PresentationFramework
        [Windows.MessageBox]::Show('AJob could not start. See data/runtime-updater.log and data/dashboard.stderr.log.', 'AJob') | Out-Null
    }
    exit 1
}
finally {
    if ($locked) { [void]$mutex.ReleaseMutex() }
    $mutex.Dispose()
}
