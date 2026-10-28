param(
    [string]$DataRoot = "",
    [string]$ResumeDirectory = "",
    [switch]$NoBrowser,
    [switch]$NonInteractive,
    [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'

function Invoke-RuntimeCommand([string]$File, [string[]]$Arguments, [int]$Seconds = 8) {
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

function Start-Runtime([string]$Launcher, [string]$Root, [string]$Code, [switch]$Reuse) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Launcher,
                   '-DataRoot', $Root, '-RuntimeCodeRoot', $Code, '-NoBrowser', '-NonInteractive')
    if ($Reuse) { $arguments += '-ReuseRunning' }
    if ($ResumeDirectory) { $arguments += @('-ResumeDirectory', $ResumeDirectory) }
    [void](Invoke-RuntimeCommand "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" $arguments 90)
}

function Test-RuntimeImport([string]$Python, [string]$Candidate) {
    # No cloud configuration, user files or live database in the preparation probe.
    $probe = 'import os,sys,tempfile; os.environ.pop("DATABASE_URL",None); os.environ.pop("RESUME_DIRECTORY",None); temporary=tempfile.TemporaryDirectory(); os.chdir(temporary.name); sys.path.insert(0,sys.argv[1]); from src.web.app import app; from fastapi.testclient import TestClient; client=TestClient(app,base_url="http://127.0.0.1"); assert client.get("/health").status_code==200; assert client.get("/").status_code==200'
    [void](Invoke-RuntimeCommand $Python @('-I', '-c', $probe, $Candidate) 20)
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
        Start-Runtime $Launcher $Root $Version.code_root
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
    if (@(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue).Count) {
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
            if (@(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue).Count) {
                Start-Runtime $launcher $root $version.code_root -Reuse
            }
            else { [void](Activate-Runtime $version $candidate $sha $launcher $root $versionPath) }
        }
        else { Start-Runtime $launcher $root $version.code_root }
    }
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8001/' }
    Start-Sleep -Milliseconds 1500
}
catch {
    if ($NonInteractive) { Write-Output 'AJob could not start; see data/dashboard.stderr.log.' }
    else {
        Add-Type -AssemblyName PresentationFramework
        [Windows.MessageBox]::Show('AJob could not start. See data/dashboard.stderr.log.', 'AJob') | Out-Null
    }
    exit 1
}
finally {
    if ($locked) { [void]$mutex.ReleaseMutex() }
    $mutex.Dispose()
}
