<# Install and manage official native Windows runners for a Basaltwater WSL owner. #>
[CmdletBinding()]
param(
    [ValidateSet('setup', 'register', 'status', 'start', 'stop')][string]$Action = 'setup',
    [string[]]$Repository = @(),
    [string]$Organization = '',
    [string[]]$AllowedUser = @(),
    [string[]]$Label = @('windows-ci', 'basaltwater-wsl'),
    [string]$RunnerGroup = '',
    [switch]$RegisterWithGh,
    [string]$RegistrationTokenFile = '',
    [switch]$PromptToken
)
. (Join-Path $PSScriptRoot 'GitHubRunner.Common.ps1')
$script:RunnerToolsSource = $PSScriptRoot

function Get-RegistrationToken([string]$Target, [bool]$IsOrganization) {
    $token = $null
    if ($RegisterWithGh) {
        $gh = Get-Command gh.exe -ErrorAction SilentlyContinue
        if (-not $gh) { $gh = Get-Item "$env:ProgramFiles\GitHub CLI\gh.exe" -ErrorAction SilentlyContinue }
        if (-not $gh) { throw 'Install GitHub CLI on Windows and run gh auth login --hostname github.com' }
        $file = if ($gh -is [IO.FileInfo]) { $gh.FullName } else { $gh.Source }
        $endpoint = if ($IsOrganization) { 'orgs/' + $Target } else { 'repos/' + $Target }
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = $file
        $info.Arguments = 'api --hostname github.com --method POST ' + $endpoint + '/actions/runners/registration-token --jq .token'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.EnvironmentVariables.Remove('GH_DEBUG')
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $info
        try {
            if (-not $process.Start()) { throw 'GitHub CLI could not start' }
            $output = $process.StandardOutput.ReadToEndAsync()
            $errors = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
            if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'GitHub CLI timed out' }
            if ($process.ExitCode -ne 0) {
                $permission = if ($IsOrganization) { 'organization owner access and admin:org scope' } else { 'repository admin access and repo scope' }
                throw "GitHub CLI could not create a token for $Target. Check Windows gh auth status --hostname github.com and $permission. Personal accounts require Repository OWNER/REPO. GH_TOKEN/GITHUB_TOKEN override saved logins."
            }
            $token = $output.GetAwaiter().GetResult().Trim()
        } finally { $process.Dispose() }
    } elseif ($RegistrationTokenFile) {
        $file = Get-Item -LiteralPath $RegistrationTokenFile
        if ($file.Length -gt 4098 -or $file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Invalid registration token file' }
        $token = [IO.File]::ReadAllText($file.FullName).Trim()
    } elseif ($PromptToken) {
        $secure = Read-Host 'One-time GitHub runner registration token' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
    if ($null -ne $token -and ($token -notmatch '^[A-Za-z0-9_-]{20,4096}$' -or
        $token -match '^(gh[pousr]_|github_pat_)')) { throw 'Use a one-time runner registration token, not a GitHub API token' }
    return $token
}

function Install-RunnerApplication([string]$Directory) {
    $app = Join-Path $Directory 'app'
    $marker = Join-Path $app 'basaltwater-version'
    if ((Test-Path -LiteralPath $marker) -and
        [IO.File]::ReadAllText($marker).Trim() -eq $script:RunnerVersion -and
        (Test-Path -LiteralPath (Join-Path $app 'bin\Runner.Listener.exe')) -and
        (Test-Path -LiteralPath (Join-Path $app 'bin\Runner.Worker.exe'))) { return }
    $archive = Join-Path (Get-RunnerRoot) ('actions-runner-win-x64-' + $script:RunnerVersion + '.zip')
    if (-not (Test-Path -LiteralPath $archive)) {
        $download = $archive + '.download'
        try {
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri (
                'https://github.com/actions/runner/releases/download/v' + $script:RunnerVersion +
                '/actions-runner-win-x64-' + $script:RunnerVersion + '.zip') -OutFile $download
            if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:RunnerSha256) {
                throw 'Runner archive checksum mismatch'
            }
            Move-Item -LiteralPath $download -Destination $archive -Force
        } finally { Remove-Item -LiteralPath $download -Force -ErrorAction SilentlyContinue }
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:RunnerSha256) {
        throw 'Runner archive checksum mismatch; remove the cached archive and retry'
    }
    $staged = Join-Path $Directory ('staging-' + [guid]::NewGuid().ToString('N'))
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $staged
        foreach ($name in @('Runner.Listener.exe', 'Runner.Worker.exe')) {
            if (-not (Test-Path -LiteralPath (Join-Path $staged ('bin\' + $name)))) { throw 'Incomplete runner archive' }
        }
        if (Test-Path -LiteralPath $app) {
            foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
                if (Test-Path -LiteralPath (Join-Path $app $name)) { Copy-Item -LiteralPath (Join-Path $app $name) -Destination $staged -Force }
            }
            Remove-Item -LiteralPath $app -Recurse -Force
        }
        Move-Item -LiteralPath $staged -Destination $app
        [IO.File]::WriteAllText($marker, $script:RunnerVersion)
    } finally { Remove-Item -LiteralPath $staged -Recurse -Force -ErrorAction SilentlyContinue }
}

function Set-RunnerTask([string]$Id) {
    $entry = Join-Path (Get-RunnerRoot) 'tools\Start-GitHubRunner.ps1'
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $entry + '" -Instance ' + $Id
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $task = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName (Get-RunnerTaskName $Id) -Action $task -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

function Register-Runner($Record, $Setup, [string]$Token) {
    $directory = $Record.Directory
    $policy = $Record.Policy
    $app = Join-Path $directory 'app'
    # Register in a disposable copy: no registration-token diagnostics survive.
    $staged = Join-Path $directory ('registration-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $staged | Out-Null
        Copy-Item -LiteralPath (Join-Path $app 'bin'), (Join-Path $app 'externals') -Destination $staged -Recurse
        $target = if ($policy.organization) { $policy.organization } else { $policy.repository }
        $id = Split-Path -Leaf $directory
        $work = Join-Path $directory 'work'
        $arguments = 'configure --unattended --replace --disableupdate --url https://github.com/' + $target +
            ' --name basaltwater-' + $env:COMPUTERNAME + '-' + $id + ' --work "' + $work + '"'
        if ($policy.labels.Count) { $arguments += ' --labels ' + ($policy.labels -join ',') }
        # The group is an environment input too, so arbitrary group names never enter command parsing.
        $environment = @{ ACTIONS_RUNNER_INPUT_TOKEN = $Token }
        if ($policy.runner_group) { $environment['ACTIONS_RUNNER_INPUT_RUNNERGROUP'] = $policy.runner_group }
        Invoke-RunnerProcess (Join-Path $staged 'bin\Runner.Listener.exe') $arguments $staged $environment
        foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
            $file = Get-Item -LiteralPath (Join-Path $staged $name) -Force -ErrorAction SilentlyContinue
            if (-not $file -or $file.Length -eq 0 -or $file.Length -gt 64KB -or
                $file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runner did not produce complete credentials; retry registration' }
        }
        foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
            Move-Item -LiteralPath (Join-Path $staged $name) -Destination (Join-Path $app $name) -Force
        }
        $policy.registration = 'registered'
        Write-RunnerJson (Join-Path $directory 'policy.json') $policy
    } finally {
        $Token = $null
        Remove-Item -LiteralPath $staged -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-RunnerCommand {
  $slot = $null
  $setupLock = $null
  try {
    $setup = Get-RunnerSetup
    $root = Get-RunnerRoot
    if ($Action -eq 'setup') {
        Assert-RunnerArguments $Repository $Organization $AllowedUser $Label $RunnerGroup `
            $RegisterWithGh $RegistrationTokenFile $PromptToken
    } elseif ($Action -eq 'register' -and
        ([int][bool]$RegisterWithGh + [int][bool]$RegistrationTokenFile + [int][bool]$PromptToken) -ne 1) {
        throw 'Choose RegisterWithGh, RegistrationTokenFile or PromptToken'
    }
    $records = @(Get-RunnerPolicies)
    if ($Action -eq 'status') {
        foreach ($record in $records) {
            $policy = $record.Policy
            $task = Get-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction SilentlyContinue
            [pscustomobject]@{ Scope = ($policy.organization + $policy.repository); Registration = $policy.registration;
                Enabled = $policy.enabled; Task = $(if ($task) { $task.State } else { 'missing' });
                Version = $policy.runner_version; Outcomes = (Join-Path $record.Directory 'outcomes.json') }
        }
        return
    }
    if ($Action -in @('start', 'stop')) {
        foreach ($record in $records) {
            if (-not $record.Policy.enabled) { continue }
            $name = Get-RunnerTaskName (Split-Path -Leaf $record.Directory)
            if ($Action -eq 'stop') { Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue }
            elseif ($record.Policy.registration -eq 'registered') { Start-ScheduledTask -TaskName $name }
        }
        return
    }
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    if ((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Runner directory must not be a reparse point'
    }
    $sid = Get-RunnerOwnerSid
    & icacls.exe $root /reset | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not reset runner directory permissions' }
    & icacls.exe $root /inheritance:r /grant:r (('*' + $sid + ':(OI)(CI)F')) `
        '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect the runner directory' }
    $setupLock = [IO.File]::Open((Join-Path $root 'setup.lock'), [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $slot = [IO.File]::Open((Join-Path $root 'queue.lock'), [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch { throw 'A workflow job is active. Wait for it to finish before changing runner setup.' }
    foreach ($record in $records) {
        Stop-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction SilentlyContinue
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while ($true) {
            try {
                $probe = [IO.File]::Open((Join-Path $record.Directory 'listener.lock'),
                    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                $probe.Dispose()
                break
            } catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Runner listener did not stop; check its scheduled task' }
                Start-Sleep -Milliseconds 200
            }
        }
    }
    $tools = Join-Path $root 'tools'
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $script:RunnerToolsSource -Filter '*.ps1') {
        if ($file.DirectoryName -ne $tools) { Copy-Item -LiteralPath $file.FullName -Destination $tools -Force }
    }
    if ($Action -eq 'setup') {
        $targets = if ($Organization) { @($Organization) } else { $Repository }
        $selected = @($targets | ForEach-Object { Get-RunnerId $_ })
        foreach ($record in $records) {
            if ((Split-Path -Leaf $record.Directory) -notin $selected) {
                $record.Policy.enabled = $false
                Write-RunnerJson (Join-Path $record.Directory 'policy.json') $record.Policy
                Disable-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction SilentlyContinue | Out-Null
            }
        }
        foreach ($target in $targets) {
            $id = Get-RunnerId $target
            $directory = Join-Path $root ('instances\' + $id)
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            $policy = [pscustomobject]@{ schema = 1; repository = $(if ($Organization) { '' } else { $target });
                organization = $Organization; runner_group = $RunnerGroup; allowed_users = @($AllowedUser);
                labels = @($Label); runner_version = $script:RunnerVersion; registration = 'pending'; enabled = $true }
            $policyPath = Join-Path $directory 'policy.json'
            $candidate = Join-Path $directory 'candidate.json'
            try {
                Write-RunnerJson $candidate $policy
                Invoke-RunnerGate $setup @('--validate', '--policy', (ConvertTo-RunnerWslPath $candidate $setup))
            } finally { Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue }
            $old = @($records | Where-Object { $_.Directory -eq $directory })
            $app = Join-Path $directory 'app'
            $complete = @(@('.runner', '.credentials', '.credentials_rsaparams') | Where-Object {
                -not (Test-Path -LiteralPath (Join-Path $app $_) -PathType Leaf) }).Count -eq 0
            $same = $old.Count -eq 1 -and $old[0].Policy.runner_group -eq $RunnerGroup -and
                ($old[0].Policy.labels -join ',') -eq ($Label -join ',')
            if ($same -and $complete -and $old[0].Policy.registration -eq 'registered') {
                $policy.registration = 'registered'
            } elseif (Test-Path -LiteralPath $app) {
                $retired = Join-Path $directory ('retired-' + [guid]::NewGuid().ToString('N'))
                foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
                    if (Test-Path -LiteralPath (Join-Path $app $name)) {
                        New-Item -ItemType Directory -Path $retired -Force | Out-Null
                        Move-Item -LiteralPath (Join-Path $app $name) -Destination $retired -Force
                    }
                }
            }
            Write-RunnerJson $policyPath $policy
            Install-RunnerApplication $directory
            $hook = Join-Path $directory 'job-started.ps1'
            $invoke = "& '" + (Join-Path $tools 'Invoke-GitHubRunnerHook.ps1').Replace("'", "''") +
                "' -PolicyPath '" + $policyPath.Replace("'", "''") + "'"
            # Keep a denial boundary even if the shared hook cannot be loaded.
            $content = @'
$ErrorActionPreference = 'Stop'
$passed = $false
try {
    __INVOKE__
    if (-not $?) { throw 'Runner hook failed' }
    $passed = $true
} finally {
    if (-not $passed) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
        $worker = [Diagnostics.Process]::GetProcessById($parent.ParentProcessId)
        if ($worker.ProcessName -eq 'Runner.Worker') { $worker.Kill() }
        exit 1
    }
}
'@
            $content = $content.Replace('__INVOKE__', $invoke)
            [IO.File]::WriteAllText($hook, $content, (New-Object Text.UTF8Encoding($true)))
            [IO.File]::WriteAllText((Join-Path $app '.env'), ('ACTIONS_RUNNER_HOOK_JOB_STARTED=' + $hook + "`n"))
            Set-RunnerTask $id
        }
        $records = @(Get-RunnerPolicies | Where-Object { $_.Policy.enabled })
    } else {
        $records = @($records | Where-Object { $_.Policy.enabled -and
            ((-not $Repository -and -not $Organization) -or $_.Policy.repository -in $Repository -or
            ($Organization -and $_.Policy.organization -eq $Organization)) })
        if (-not $records.Count) { throw 'No matching installed runner; run setup first' }
        if ($records.Count -gt 1 -and -not $RegisterWithGh) { throw 'Select one repository for a one-time token' }
    }
    $pending = $false
    foreach ($record in $records) {
        $policy = $record.Policy
        $target = $policy.organization + $policy.repository
        if ($policy.registration -eq 'registered') { continue }
        try {
            $token = Get-RegistrationToken $target ([bool]$policy.organization)
            if ($token) { Register-Runner $record $setup $token }
            else { Write-Host "$target`: pending registration. Retry with RegisterWithGh or PromptToken." }
        } catch {
            $pending = $true
            Write-Warning ("$target`: " + $_.Exception.Message)
        } finally { $token = $null }
    }
    if ($pending) { Write-Warning 'Some runners remain pending. Other setup work can continue; retry with -Action register.' }
  } finally {
    # Resume every enabled healthy runner even when another scope needs repair.
    if ($slot) { $slot.Dispose() }
    if ($setupLock) {
        foreach ($record in @(Get-RunnerPolicies)) {
            if ($record.Policy.enabled -and $record.Policy.registration -eq 'registered') {
                Enable-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction Continue | Out-Null
                Start-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction Continue
            } else {
                Disable-ScheduledTask -TaskName (Get-RunnerTaskName (Split-Path -Leaf $record.Directory)) -ErrorAction SilentlyContinue | Out-Null
            }
        }
        $setupLock.Dispose()
    }
  }
}

Invoke-RunnerCommand
