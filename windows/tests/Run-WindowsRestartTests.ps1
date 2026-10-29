<# Offline Windows reboot lifecycle; all shutdown, boot and Scheduler calls are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected $Pattern; received $caught" }
}
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'AgentWorkstation.Common.ps1')
. (Join-Path $windows 'T3Completion.Common.ps1')
. (Join-Path $windows 'WindowsRestart.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-restart-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = $temporary
    $source = Join-Path $temporary ("source & quote's " + [char]0x03bb)
    New-Item -ItemType Directory -Path (Join-Path $source 'windows'), (Join-Path $temporary 'Basaltwater') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $source 'windows\Restart-WindowsSetup.ps1') -Value '# fixture'
    Set-Content -LiteralPath (Join-Path $source 'install.ps1') -Value @'
param($Profile, [switch]$Resume, [switch]$NoUpdateSource, [switch]$NoReboot, [switch]$NoRestartT3)
$global:BasaltwaterRestartTestRecovery = [pscustomobject]@{ Resume = [bool]$Resume;
    NoUpdateSource = [bool]$NoUpdateSource; NoReboot = [bool]$NoReboot; NoRestartT3 = [bool]$NoRestartT3;
    Removed = $global:BasaltwaterRestartTestRemoved }
'@
    function Get-NativeCompletionOwner { return [pscustomobject]@{ User = [pscustomobject]@{ Value = 'owner' } } }
    function Get-NativeWindowsBoot { return $script:Boot }
    function Get-NativeRebootStatus { return [pscustomobject]@{ Required = $script:Required; Unknown = $script:Unknown;
        Reasons = @('Windows Update' | Where-Object { $script:Required }); Advisories = @() } }
    function New-ScheduledTaskAction($Execute, $Argument) { return @{ Execute = $Execute; Argument = $Argument } }
    function New-ScheduledTaskPrincipal($UserId, $LogonType, $RunLevel) { return @{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel } }
    function New-ScheduledTaskSettingsSet($ExecutionTimeLimit, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries) { return @{ Limit = $ExecutionTimeLimit } }
    function New-ScheduledTaskTrigger([switch]$AtLogOn, $User) { return @{ User = $User } }
    function Register-ScheduledTask($TaskName, $TaskPath, $Action, $Principal, $Settings, $Trigger, [switch]$Force) {
        $script:Tasks[$TaskName] = @{ Action = $Action; Principal = $Principal; Trigger = $Trigger; Settings = $Settings }
    }
    function Unregister-ScheduledTask($TaskName, $TaskPath, [switch]$Confirm) {
        $script:Removed += $TaskName; $script:Tasks.Remove($TaskName)
        if ($TaskName -eq 'Basaltwater-Windows-Resume-owner') { $global:BasaltwaterRestartTestRemoved = $true }
    }
    function Start-ScheduledTask($TaskName, $TaskPath) {
        if ($script:DispatchFailure) { throw 'fixture scheduler failure' }
        $script:Started += $TaskName
    }
    function Show-NativeWindowsRestartPrompt($Request) {
        $script:Prompts++
        $script:Grace = ([datetime]::Parse($Request.RestartAtUtc).ToUniversalTime() - [datetime]::UtcNow).TotalSeconds
        Assert-True ($Request.Status -eq 'countdown' -and $Request.Reasons.Count -gt 0) 'Countdown omitted its status or reason'
        if ($script:PromptFailure) { throw 'fixture notification failure' }
        if ($script:ClearDuringPrompt) { $script:Required = $false }
        return $script:RestartAccepted
    }
    function Request-NativeWindowsRestart($Reasons) {
        $script:Shutdowns++
        Assert-True ($Reasons.Count -gt 0) 'Shutdown event omitted its reboot reason'
        $otherLock = $null; $blocked = $false
        try { $otherLock = [IO.File]::Open((Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock'), 'Open', 'ReadWrite', 'None') }
        catch [IO.IOException] { $blocked = $true }
        finally { if ($otherLock) { $otherLock.Dispose() } }
        Assert-True $blocked 'Reboot request did not retain the setup lock'
        Assert-True (-not $script:Tasks.ContainsKey('Basaltwater-Windows-Restart-owner')) 'Shutdown could interrupt removal of the restart task'
        if ($script:ShutdownFailure) { throw 'fixture shutdown failure' }
    }
    $script:Required = $false; $script:Unknown = $false; $script:Boot = '100'
    $script:Tasks = @{}; $script:Started = @(); $script:Removed = @(); $script:Shutdowns = 0
    $script:Grace = 0; $script:DispatchFailure = $false
    $script:ShutdownFailure = $false
    $script:Prompts = 0; $script:RestartAccepted = $true; $script:ClearDuringPrompt = $false; $script:PromptFailure = $false
    $global:BasaltwaterRestartTestRecovery = $null; $global:BasaltwaterRestartTestRemoved = $false
    $state = [pscustomobject]@{ Version = 1; Profile = 'agent_windows'; OwnerSid = 'owner';
        Revision = 'a' * 40; Source = $source; Phase = 'complete' }
    $statePath = Join-Path $temporary 'Basaltwater\windows-setup.json'
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Assert-True (-not (Invoke-NativeWindowsRestart $state)) 'Clear indicators queued a reboot'
    $script:Unknown = $true
    Assert-True (-not (Invoke-NativeWindowsRestart $state) -and $script:Started.Count -eq 0) 'Unknown status queued a reboot'
    $script:Required = $true
    Assert-True (-not (Invoke-NativeWindowsRestart $state -NoReboot) -and $script:Started.Count -eq 0) 'Reboot opt-out dispatched a task'
    Assert-True (Invoke-NativeWindowsRestart $state) 'Required reboot was not queued by default'
    $requestPath = Get-NativeWindowsRestartPath
    [IO.File]::WriteAllText($requestPath, [IO.File]::ReadAllText($requestPath), [Text.UTF8Encoding]::new($false))
    $request = Get-Content -LiteralPath (Get-NativeWindowsRestartPath) -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ($request.Reasons[0] -eq 'Windows Update' -and $request.QueuedUtc -and $request.UpdatedUtc -and
        -not $request.RestartAtUtc) 'Queued restart omitted trigger/timestamps or claimed a countdown before setup exited'
    Assert-True (-not $request.Resume -and $script:Tasks.Count -eq 1) 'Completed setup scheduled unnecessary recovery'
    $task = $script:Tasks['Basaltwater-Windows-Restart-owner']
    Assert-True ($task.Principal.UserId -eq 'owner' -and $task.Principal.LogonType -eq 'Interactive' -and
        $task.Principal.RunLevel -eq 'Limited' -and $task.Action.Argument -match '-WindowStyle Hidden') 'Restart task crossed account/privilege or console boundaries'
    $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(($task.Action.Argument -split ' ')[-1]))
    Assert-True ($command.Contains("quote''s") -and $command.Contains($request.Id)) 'Worker command lost literal paths or request identity'
    Invoke-NativeWindowsRestartWorker Restart $request.Id
    Assert-True ($script:Shutdowns -eq 1 -and $script:Grace -gt 55 -and $script:Grace -le 60 -and $script:Tasks.Count -eq 0) 'Reboot worker skipped the visible countdown/shutdown or retained its task'
    Assert-True ((Read-NativeWindowsRestartRequest $request.Id).Status -eq 'restart-requested') 'Reboot status was not persisted'

    foreach ($scenario in @('cleared-before', 'cleared-during', 'later', 'notification-failed')) {
        $script:Required = $true
        $script:ClearDuringPrompt = $scenario -eq 'cleared-during'
        $script:RestartAccepted = $scenario -ne 'later'
        $script:PromptFailure = $scenario -eq 'notification-failed'
        Invoke-NativeWindowsRestart $state | Out-Null
        $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        if ($scenario -eq 'cleared-before') { $script:Required = $false }
        $beforePrompts = $script:Prompts
        Invoke-NativeWindowsRestartWorker Restart $request.Id
        $expected = switch ($scenario) {
            'later' { 'restart-deferred' }
            'notification-failed' { 'manual-restart-required' }
            default { 'no-longer-required' }
        }
        Assert-True ($script:Shutdowns -eq 1 -and $script:Tasks.Count -eq 0 -and
            (Read-NativeWindowsRestartRequest $request.Id).Status -eq $expected) "Unexpected reboot or missing outcome: $scenario"
        if ($scenario -eq 'cleared-before') { Assert-True ($beforePrompts -eq $script:Prompts) 'Cleared requirement still showed a countdown' }
    }
    $script:ClearDuringPrompt = $false; $script:RestartAccepted = $true; $script:PromptFailure = $false

    $state.Phase = 'restart-required'
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    $script:Required = $false
    Assert-True (Invoke-NativeWindowsRestart $state -Required -NoRestartT3 -InstallerReason 'Git.Git (installer exit code 3010)') 'Installer reboot was ignored when registry indicators were clear'
    $request = Read-NativeWindowsRestartRequest ((Get-Content -LiteralPath (Get-NativeWindowsRestartPath) -Raw | ConvertFrom-Json).Id)
    Assert-True ($request.InstallerRequired -and $request.Reasons -contains 'Git.Git (installer exit code 3010)') 'Installer identity/exit code was not retained'
    Assert-True ($request.Resume -and $script:Tasks.Count -eq 2 -and
        $script:Tasks['Basaltwater-Windows-Resume-owner'].Trigger.User -eq 'owner') 'Interrupted setup has no account-specific continuation'
    Invoke-NativeWindowsRestartWorker Restart $request.Id
    Assert-True ($script:Shutdowns -eq 2) 'Clear OS probes discarded the installer restart requirement'
    Invoke-NativeWindowsRestartWorker Resume $request.Id
    Assert-True ($null -eq $global:BasaltwaterRestartTestRecovery -and $script:Tasks.ContainsKey('Basaltwater-Windows-Resume-owner')) 'A login without a reboot consumed recovery'
    $script:Boot = '200'
    $global:BasaltwaterRestartTestRemoved = $false
    Invoke-NativeWindowsRestartWorker Resume $request.Id
    $recovery = $global:BasaltwaterRestartTestRecovery
    Assert-True ($recovery.Removed -and $recovery.Resume -and $recovery.NoUpdateSource -and
        $recovery.NoReboot -and $recovery.NoRestartT3) 'Recovery lost selections/opt-outs or can create a reboot loop'
    Assert-True ((Read-NativeWindowsRestartRequest $request.Id).Status -eq 'resumed') 'Recovery completion was not recorded'
    $global:BasaltwaterRestartTestRecovery = $null
    Invoke-NativeWindowsRestartWorker Resume $request.Id
    Assert-True ($null -eq $global:BasaltwaterRestartTestRecovery) 'Consumed recovery was applied again'
    $newer = $request.PSObject.Copy()
    $newer.Id = 'f' * 32
    Write-NativeWindowsRestartRequest $newer
    Assert-True (-not (Test-NativeWindowsRestartIdentity $request)) 'Old recovery can overwrite a newer restart request'
    Write-NativeWindowsRestartRequest $request
    $state.OwnerSid = 'other'
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Assert-Throws { Read-NativeWindowsRestartRequest $request.Id } 'no longer matches'
    $state.OwnerSid = 'owner'
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Assert-Throws { Read-NativeWindowsRestartRequest ('f' * 32) } 'no longer matches'
    $script:DispatchFailure = $true
    Assert-Throws { Invoke-NativeWindowsRestart $state -Required } 'scheduler failure'
    Assert-True ($script:Tasks.Count -eq 0 -and
        (Get-Content -LiteralPath (Get-NativeWindowsRestartPath) -Raw | ConvertFrom-Json).Status -eq 'manual-restart-required') 'Dispatch failure left an active reboot/recovery task'
    Clear-NativeWindowsRestart
    Assert-True (-not (Test-Path -LiteralPath (Get-NativeWindowsRestartPath))) 'A new run did not clear prior reboot work'
    $script:DispatchFailure = $false; $script:ShutdownFailure = $true
    Invoke-NativeWindowsRestart $state -Required | Out-Null
    $request = Get-Content -LiteralPath (Get-NativeWindowsRestartPath) -Raw | ConvertFrom-Json
    Invoke-NativeWindowsRestartWorker Restart $request.Id
    Assert-True ((Read-NativeWindowsRestartRequest $request.Id).Status -eq 'manual-restart-required') 'Shutdown failure retained a successful reboot status'

    # A replacement registered as setup.lock is released must survive the old
    # worker's finally block. Never invoke a real scheduler or Windows shutdown.
    & {
        function Wait-NativeSetupCompletion($LockPath) {
            $stream = [IO.File]::Open($LockPath, 'OpenOrCreate', 'ReadWrite', 'None')
            $wrapper = [pscustomobject]@{ Lease = $stream }
            $wrapper | Add-Member ScriptMethod Dispose {
                $this.Lease.Dispose()
                $script:Tasks['Basaltwater-Windows-Restart-owner'] = @{ Replacement = $true }
            }
            return $wrapper
        }
        Invoke-NativeWindowsRestart $state -Required | Out-Null
        $owned = Get-Content -LiteralPath (Get-NativeWindowsRestartPath) -Raw -Encoding UTF8 | ConvertFrom-Json
        # Boot changed before worker startup: exercise its early-return cleanup.
        $script:Boot = '300'
        Invoke-NativeWindowsRestartWorker Restart $owned.Id
        Assert-True ($script:Tasks['Basaltwater-Windows-Restart-owner'].Replacement) 'Old cleanup deleted a replacement after releasing setup.lock'
    }

    # Compile the notification implementation without showing UI or rebooting.
    if ($PSVersionTable.PSVersion.Major -eq 5) {
        Add-Type -Path (Join-Path $windows 'WindowsRestart.Dialog.cs') -ReferencedAssemblies System.dll, System.Drawing.dll, System.Windows.Forms.dll
        Assert-True ($null -ne ('Basaltwater.WindowsRestart.Dialog' -as [type])) 'Restart notification failed to compile'
    }

    # Inspect the real shutdown boundary with a fake command; never invoke the OS.
    & {
        $tokens = $null; $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $windows 'WindowsRestart.Common.ps1'), [ref]$tokens, [ref]$errors)
        $definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Request-NativeWindowsRestart' }, $true)
        Invoke-Expression $definition.Extent.Text
        function Join-Path($Path, $ChildPath) { return 'Mock-Shutdown' }
        function Mock-Shutdown { $script:ShutdownArguments = @($args); $global:LASTEXITCODE = $script:ShutdownExit }
        $script:ShutdownExit = 0
        Request-NativeWindowsRestart -Reasons @('Windows Update')
        Assert-True (($script:ShutdownArguments -join ' ') -match '/r /t 0 /d p:4:2' -and
            $script:ShutdownArguments -notcontains '/f' -and $script:ShutdownArguments[-1] -match 'Reason: Windows Update') 'Shutdown can force apps closed or omit the reason from the Windows event log'
        $script:ShutdownExit = 5
        Assert-Throws { Request-NativeWindowsRestart } 'exit code 5'
    }
    Write-Host 'Native Windows restart tests passed.'
} finally {
    Remove-Variable BasaltwaterRestartTestRecovery, BasaltwaterRestartTestRemoved -Scope Global -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $savedLocal
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
