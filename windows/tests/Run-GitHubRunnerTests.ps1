<# Offline runner tests. Windows additionally exercises real worker job objects. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected error matching $Pattern; received $caught" }
}
function Import-TestFunction([string]$Path, [string]$Name) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    $definition = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name
    }, $true)
    if (-not $definition) { throw "Missing function $Name" }
    # Return code for the caller's test scope, rather than defining it here.
    return $definition.Extent.Text
}

$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$originalLocalAppData = $env:LOCALAPPDATA
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-runner-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporary | Out-Null
$processes = @()
try {
    foreach ($file in Get-ChildItem -LiteralPath $windows -Filter '*.ps1' -Recurse) {
        $tokens = $null; $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) "Syntax errors in $($file.Name): $errors"
    }
    $env:LOCALAPPDATA = Join-Path $temporary 'profile'
    . (Join-Path $windows 'GitHubRunner.Common.ps1')
    Assert-RunnerArguments @('owner/one', 'owner/two') '' @('owner') @('windows-ci') '' $true '' $false
    Assert-RunnerArguments @() 'team' @('owner') @() 'Build Team' $false '' $false
    foreach ($repositories in @(@('owner'), @('owner/a', 'OWNER/A'), @('owner/..'), @('https://github.com/owner/a'))) {
        Assert-Throws { Assert-RunnerArguments $repositories '' @('owner') @() '' $false '' $false } 'Repository|distinct'
    }
    Assert-Throws { Assert-RunnerArguments @('owner/a') 'team' @('owner') @() '' $false '' $false } 'Choose'
    Assert-Throws { Assert-RunnerArguments @('owner/a') '' @() @() '' $false '' $false } 'allowed GitHub users'
    Assert-Throws { Assert-RunnerArguments @('owner/a') '' @('owner') @('bad,label') '' $false '' $false } 'label'
    Assert-Throws { Assert-RunnerArguments @('owner/a') '' @('owner') @() 'group' $false '' $false } 'group'
    Assert-Throws { Assert-RunnerArguments @('owner/a') '' @('owner') @() '' $true 'token' $false } 'Choose'
    Assert-Throws { Assert-RunnerArguments @('owner/a', 'owner/b') '' @('owner') @() '' $false 'token' $false } 'multiple repositories'
    Assert-True ((Get-RunnerId 'bluehexagons/ocelli') -eq '2d9ed0fc1bc97280') 'Instance ID differs from Linux'
    Assert-True ((Get-RunnerId 'OWNER/ONE') -eq (Get-RunnerId 'owner/one')) 'Instance IDs must ignore case'
    New-Item -ItemType Directory -Path (Get-RunnerRoot) -Force | Out-Null
    $json = Join-Path (Get-RunnerRoot) 'test.json'
    Write-RunnerJson $json @{ allowed_users = @('owner'); enabled = $true }
    $bytes = [IO.File]::ReadAllBytes($json)
    Assert-True ($bytes[0] -eq 123) 'JSON must not contain a UTF-8 BOM'
    Assert-True ((Get-Content -LiteralPath $json -Raw | ConvertFrom-Json).enabled) 'JSON round trip failed'

    $setupScript = Join-Path $windows 'Setup-GitHubRunner.ps1'
    foreach ($name in @('Get-RegistrationToken', 'Register-Runner', 'Set-RunnerTask', 'Install-RunnerApplication')) {
        Invoke-Expression (Import-TestFunction $setupScript $name)
    }
    # Upgrade a disposable ZIP and inject a failed activation rename.
    & {
        $instance = Join-Path $temporary 'upgrade instance'
        $app = Join-Path $instance 'app'
        $bundle = Join-Path $temporary 'runner bundle'
        New-Item -ItemType Directory -Path (Join-Path $app 'bin'), (Join-Path $bundle 'bin') -Force | Out-Null
        foreach ($name in @('Runner.Listener.exe', 'Runner.Worker.exe')) {
            Set-Content -LiteralPath (Join-Path $app ('bin\' + $name)) -Value 'old executable'
            Set-Content -LiteralPath (Join-Path $bundle ('bin\' + $name)) -Value 'new executable'
        }
        foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
            [IO.File]::WriteAllText((Join-Path $app $name), 'preserved registration')
        }
        [IO.File]::WriteAllText((Join-Path $app 'basaltwater-version'), 'old version')
        $archive = Join-Path (Get-RunnerRoot) ('actions-runner-win-x64-' + $script:RunnerVersion + '.zip')
        Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath $archive
        $savedDigest = $script:RunnerSha256
        $script:RunnerSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        $script:FailActivation = $true
        $script:FailRollback = $false
        function Move-Item {
            param($LiteralPath, $Destination, [switch]$Force)
            if ($script:FailActivation -and (Split-Path -Leaf $LiteralPath) -like 'staging-*') {
                throw 'simulated activation failure'
            }
            if ($script:FailRollback -and (Split-Path -Leaf $LiteralPath) -eq 'app.previous') {
                throw 'simulated rollback failure'
            }
            Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -Force:$Force
        }
        try {
            Assert-Throws { Install-RunnerApplication $instance } 'activation failure'
            Assert-True ([IO.File]::ReadAllText((Join-Path $app 'basaltwater-version')) -eq 'old version') 'Failed upgrade lost the old app'
            foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
                Assert-True ([IO.File]::ReadAllText((Join-Path $app $name)) -eq 'preserved registration') 'Failed upgrade lost registration'
            }
            Assert-True (@(Get-ChildItem -LiteralPath $instance -Filter 'staging-*').Count -eq 0) 'Failed upgrade left a staging directory'
            $script:FailRollback = $true
            Assert-Throws { Install-RunnerApplication $instance } 'registration is preserved at'
            $previous = Join-Path $instance 'app.previous'
            Assert-True ([IO.File]::ReadAllText((Join-Path $previous '.credentials')) -eq 'preserved registration') 'Failed rollback lost its recovery copy'
            $script:FailRollback = $false
            # Also exercise recovery after a process stops between the two renames.
            Assert-Throws { Install-RunnerApplication $instance } 'activation failure'
            Assert-True (Test-Path -LiteralPath (Join-Path $app '.credentials')) 'Interrupted rename did not recover registration'
            $script:FailActivation = $false
            Install-RunnerApplication $instance
            Assert-True ([IO.File]::ReadAllText((Join-Path $app 'basaltwater-version')) -eq $script:RunnerVersion) 'Upgrade retry did not activate the new app'
            Assert-True ([IO.File]::ReadAllText((Join-Path $app '.credentials')) -eq 'preserved registration') 'Successful upgrade lost registration'
            Assert-True (-not (Test-Path -LiteralPath (Join-Path $instance 'app.previous'))) 'Successful upgrade left a backup'
        } finally { $script:RunnerSha256 = $savedDigest }
    }
    $RegisterWithGh = $false; $PromptToken = $false
    $RegistrationTokenFile = Join-Path $temporary 'registration-token'
    [IO.File]::WriteAllText($RegistrationTokenFile, ('a' * 30) + "`n")
    Assert-True ((Get-RegistrationToken 'owner/one' $false) -eq ('a' * 30)) 'Token file was not read'
    [IO.File]::WriteAllText($RegistrationTokenFile, 'ghp_' + ('a' * 30))
    Assert-Throws { Get-RegistrationToken 'owner/one' $false } 'one-time runner registration token'
    [IO.File]::WriteAllText($RegistrationTokenFile, 'a' * 4099)
    Assert-Throws { Get-RegistrationToken 'owner/one' $false } 'Invalid registration token file'
    $RegistrationTokenFile = ''
    Assert-True ($null -eq (Get-RegistrationToken 'owner/one' $false)) 'Deferred setup unexpectedly requested a token'

    $directory = Join-Path $temporary 'instance with spaces'
    New-Item -ItemType Directory -Path (Join-Path $directory 'app/bin'), (Join-Path $directory 'app/externals') -Force | Out-Null
    $record = [pscustomobject]@{ Directory = $directory; Policy = [pscustomobject]@{
        organization = ''; repository = 'owner/one'; labels = @('windows-ci'); runner_group = ''; registration = 'pending' } }
    $script:IncompleteCredentials = $false
    $script:ConfigurationFailure = $false
    $script:RegistrationCalls = 0
    function Invoke-RunnerProcess($File, $Arguments, $Directory, $Environment) {
        $script:RegistrationCalls++
        Assert-True ($Arguments -notmatch ('a' * 30)) 'Token was included in command arguments'
        Assert-True ($Environment.ACTIONS_RUNNER_INPUT_TOKEN -eq ('a' * 30)) 'Short-lived token missing from child environment'
        Assert-True ($Arguments -match '--replace' -and $Arguments -match '--disableupdate') 'Registration settings missing'
        Assert-True ($File -match 'Runner.Listener.exe$') 'Wrong registration executable'
        [IO.File]::WriteAllText((Join-Path $Directory 'vendor-diagnostics'), 'sensitive')
        if ($script:ConfigurationFailure) { throw 'Runner configuration failed' }
        $names = if ($script:IncompleteCredentials) { @('.runner') } else { @('.runner', '.credentials', '.credentials_rsaparams') }
        foreach ($name in $names) { [IO.File]::WriteAllText((Join-Path $Directory $name), 'fixture') }
    }
    Register-Runner $record $null ('a' * 30)
    Assert-True ($record.Policy.registration -eq 'registered') 'Successful registration was not published'
    Assert-True (@(Get-ChildItem -LiteralPath $directory -Filter 'registration-*').Count -eq 0) 'Registration diagnostics survived success'
    $record.Policy.registration = 'pending'
    $script:IncompleteCredentials = $true
    Assert-Throws { Register-Runner $record $null ('a' * 30) } 'complete credentials'
    Assert-True ($record.Policy.registration -eq 'pending') 'Incomplete credentials marked registered'
    $script:ConfigurationFailure = $true
    Assert-Throws { Register-Runner $record $null ('a' * 30) } 'configuration failed'
    Assert-True (@(Get-ChildItem -LiteralPath $directory -Filter 'registration-*').Count -eq 0) 'Registration diagnostics survived failure'

    # Restore the production helper and execute disposable children only.
    Invoke-Expression (Import-TestFunction (Join-Path $windows 'GitHubRunner.Common.ps1') 'Invoke-RunnerProcess')
    $executable = (Get-Process -Id $PID).Path
    $fixture = Join-Path $temporary 'failed-child.ps1'
    [IO.File]::WriteAllText($fixture, '[Console]::Error.WriteLine($env:ACTIONS_RUNNER_INPUT_TOKEN); exit 23')
    $caught = $null
    try { Invoke-RunnerProcess $executable ('-NoProfile -File "' + $fixture + '"') $temporary @{ ACTIONS_RUNNER_INPUT_TOKEN = 'never-print-this-token' } }
    catch { $caught = $_ }
    Assert-True ($caught -and $caught.Exception.Message -match 'configuration failed' -and
        $caught.ToString() -notmatch 'never-print-this-token') 'Vendor failure leaked a token or lost its fixed diagnostic'

    # Apply complete desired sets to temporary state; every host operation is mocked.
    Invoke-Expression (Import-TestFunction $setupScript 'Invoke-RunnerCommand')
    $script:RunnerToolsSource = $windows
    function Get-RunnerSetup { return [pscustomobject]@{ Distro = 'Ubuntu-26.04'; LinuxUser = 'builder' } }
    function Get-RunnerOwnerSid { return 'S-1-5-21-12345' }
    function icacls.exe { $global:LASTEXITCODE = 0 }
    function Stop-ScheduledTask { param($TaskName, $ErrorAction) }
    function Start-ScheduledTask { param($TaskName, $ErrorAction) }
    function Enable-ScheduledTask { param($TaskName, $ErrorAction) }
    function Disable-ScheduledTask { param($TaskName, $ErrorAction) }
    function Set-RunnerTask { param($Id) }
    function ConvertTo-RunnerWslPath { param($Path, $Setup); return $Path }
    function Invoke-RunnerGate {
        param($Setup, $Arguments)
        $candidate = Get-Content -LiteralPath $Arguments[2] -Raw | ConvertFrom-Json
        Assert-True ($candidate.allowed_users.Count -gt 0) 'Policy validation received no allowlist'
    }
    function Install-RunnerApplication {
        param($Directory)
        New-Item -ItemType Directory -Path (Join-Path $Directory 'app/bin'), (Join-Path $Directory 'app/externals') -Force | Out-Null
    }
    $script:DeniedTarget = ''
    $script:TokenRequests = @()
    function Get-RegistrationToken {
        param($Target, $IsOrganization)
        if (-not $RegisterWithGh) { return $null }
        $script:TokenRequests += $Target
        if ($Target -eq $script:DeniedTarget) { throw 'GitHub CLI access denied' }
        return 'a' * 30
    }
    function Invoke-RunnerProcess {
        param($File, $Arguments, $Directory, $Environment)
        foreach ($name in @('.runner', '.credentials', '.credentials_rsaparams')) {
            [IO.File]::WriteAllText((Join-Path $Directory $name), 'fixture')
        }
    }
    $Action = 'setup'; $Repository = @('owner/one', 'owner/two'); $Organization = ''
    $AllowedUser = @('owner'); $Label = @('windows-ci'); $RunnerGroup = ''
    $RegisterWithGh = $false; $RegistrationTokenFile = ''; $PromptToken = $false
    Invoke-RunnerCommand
    $records = @(Get-RunnerPolicies)
    Assert-True ($records.Count -eq 2 -and @($records | Where-Object { $_.Policy.registration -eq 'pending' }).Count -eq 2) 'Deferred multi-repository setup failed'
    $script:DeniedTarget = 'owner/one'; $RegisterWithGh = $true
    Invoke-RunnerCommand -WarningAction SilentlyContinue
    $records = @(Get-RunnerPolicies)
    Assert-True (@($records | Where-Object { $_.Policy.registration -eq 'registered' }).Count -eq 1) 'One denied scope blocked registration of another'
    $script:DeniedTarget = ''
    Invoke-RunnerCommand
    $script:TokenRequests = @()
    $Repository = @('owner/two', 'owner/one')
    Invoke-RunnerCommand
    Assert-True ($script:TokenRequests.Count -eq 0) 'Healthy reordered registrations requested new credentials'
    $Repository = @('owner/two')
    Invoke-RunnerCommand
    $disabled = @(Get-RunnerPolicies | Where-Object { -not $_.Policy.enabled })
    Assert-True ($disabled.Count -eq 1 -and $disabled[0].Policy.repository -eq 'owner/one') 'Omitted repository was not disabled'
    $Repository = @('owner/one', 'owner/two')
    Invoke-RunnerCommand
    Assert-True ($script:TokenRequests.Count -eq 0) 'Re-added repository lost its registration'
    $Label = @('new-label'); $RegisterWithGh = $false
    Invoke-RunnerCommand
    Assert-True (@(Get-RunnerPolicies | Where-Object { $_.Policy.registration -eq 'pending' }).Count -eq 2) 'Label replacement did not become retryable'
    $retired = @(Get-ChildItem -LiteralPath (Join-Path (Get-RunnerRoot) 'instances') -Filter 'retired-*' -Directory -Recurse)
    Assert-True ($retired.Count -eq 2) 'Replaced credentials were not retired'
    $held = [IO.File]::Open((Join-Path (Get-RunnerRoot) 'queue.lock'), [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try { Assert-Throws { Invoke-RunnerCommand } 'workflow job is active' }
    finally { $held.Dispose() }

    if ($env:OS -eq 'Windows_NT') {
        # Real Windows process/lock lifecycle: second worker waits through cleanup;
        # killing a guardian kills its worker before another job takes the slot.
        Import-RunnerJobType
        $lock = Join-Path $temporary 'queue.lock'
        $workerScript = Join-Path $temporary 'worker.ps1'
        [IO.File]::WriteAllText($workerScript, 'Start-Sleep -Seconds 90')
        $readyPaths = @()
        $workers = @(); $guardians = @()
        for ($index = 0; $index -lt 3; $index++) {
            $worker = Start-Process -FilePath $executable -ArgumentList ('-NoProfile -File "' + $workerScript + '"') -PassThru -WindowStyle Hidden
            $workers += $worker; $processes += $worker
            $ready = Join-Path $temporary ('ready-' + $index)
            $readyPaths += $ready
            $arguments = '-NoProfile -File "' + (Join-Path $windows 'Hold-GitHubRunnerSlot.ps1') + '" -WorkerId ' +
                $worker.Id + ' -StartedTicks ' + $worker.StartTime.ToUniversalTime().Ticks +
                ' -ReadyPath "' + $ready + '" -LockPath "' + $lock + '"'
            $guardian = Start-Process -FilePath $executable -ArgumentList $arguments -PassThru -WindowStyle Hidden
            $guardians += $guardian; $processes += $guardian
            if ($index -eq 0) {
                $deadline = [DateTime]::UtcNow.AddSeconds(20)
                while (-not (Test-Path -LiteralPath $ready)) {
                    if ($guardian.HasExited -or [DateTime]::UtcNow -ge $deadline) { throw 'First queue guardian failed to acquire the slot' }
                    Start-Sleep -Milliseconds 100
                }
            }
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(2)
        while ([DateTime]::UtcNow -lt $deadline) {
            Assert-True (-not (Test-Path -LiteralPath $readyPaths[1])) 'Two workers acquired the host slot'
            Assert-True (-not (Test-Path -LiteralPath $readyPaths[2])) 'Third worker bypassed the host slot'
            Assert-True (-not $guardians[1].HasExited) 'Waiting guardian failed'
            Start-Sleep -Milliseconds 100
        }
        $workers[1].Kill()
        Assert-True ($guardians[1].WaitForExit(10000)) 'Canceled queued worker left its guardian running'
        $guardians[0].Kill()
        Assert-True ($workers[0].WaitForExit(10000)) 'Guardian crash did not kill its worker'
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while (-not (Test-Path -LiteralPath $readyPaths[2])) {
            if ($guardians[2].HasExited -or [DateTime]::UtcNow -ge $deadline) { throw 'Next worker did not acquire the released slot' }
            Start-Sleep -Milliseconds 100
        }
        $workers[2].Kill()
        Assert-True ($guardians[2].WaitForExit(10000)) 'Worker exit did not release its guardian'
        $probe = [IO.File]::Open($lock, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $probe.Dispose()

        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            # A disposable worker must die before its simulated always() canary,
            # including when the installed shared hook itself is missing.
            $fakeWorker = Join-Path $temporary 'Runner.Worker.exe'
            Add-Type -OutputAssembly $fakeWorker -OutputType ConsoleApplication -TypeDefinition @'
using System.Diagnostics;
using System.IO;
public class RunnerCanary {
    public static void Main(string[] args) {
        var child = Process.Start(args[0], "-NoProfile -ExecutionPolicy Bypass -File \"" + args[1] + "\" -PolicyPath ignored");
        child.WaitForExit();
        File.WriteAllText(args[2], "WORKFLOW_STEP_EXECUTED");
    }
}
'@
            $installed = @(Get-RunnerPolicies)[0]
            $hooks = @((Join-Path $windows 'Invoke-GitHubRunnerHook.ps1'),
                       (Join-Path $installed.Directory 'job-started.ps1'))
            Remove-Item -LiteralPath (Join-Path (Get-RunnerRoot) 'tools\Invoke-GitHubRunnerHook.ps1') -Force
            foreach ($hook in $hooks) {
                $canary = Join-Path $temporary ([guid]::NewGuid().ToString('N') + '.canary')
                $arguments = '"' + $executable + '" "' + $hook + '" "' + $canary + '"'
                $denied = Start-Process -FilePath $fakeWorker -ArgumentList $arguments -PassThru -WindowStyle Hidden
                $processes += $denied
                Assert-True ($denied.WaitForExit(20000)) 'Denied worker did not exit'
                Assert-True (-not (Test-Path -LiteralPath $canary)) 'Denied worker executed its workflow canary'
            }
        }
    }
    Write-Host 'GitHub runner offline tests passed.'
} finally {
    foreach ($process in $processes) {
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
    $env:LOCALAPPDATA = $originalLocalAppData
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
