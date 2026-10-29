<# Offline completion tests. All OS probes, task registration and app actions are mocked. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected failure: $Pattern; received: $caught" }
}
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $windows 'AgentWorkstation.Common.ps1')
. (Join-Path $windows 'T3Completion.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-t3-completion-' + [guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
$savedPath = $env:PATH
$savedSystemRoot = $env:SystemRoot
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $env:LOCALAPPDATA = $temporary
    if (-not $env:SystemRoot) { $env:SystemRoot = $temporary }
    & {
        function Get-NativeWindowsUpdateReboot {
            if ($script:ProbeFailure) { throw 'unreadable update state' }
            return $script:UpdatePending
        }
        function Test-Path($LiteralPath) {
            if ($script:RegistryFailure) { throw 'unreadable registry' }
            return ($LiteralPath -eq $script:PendingKey)
        }
        function Get-ItemProperty($LiteralPath) {
            if ($script:RegistryFailure) { throw 'unreadable registry' }
            return [pscustomobject]@{ PendingFileRenameOperations = $script:PendingRenames }
        }
        $script:ProbeFailure = $false; $script:RegistryFailure = $false
        $script:UpdatePending = $false; $script:PendingKey = ''; $script:PendingRenames = @()
        $status = Get-NativeRebootStatus
        Assert-True (-not $status.Required -and -not $status.Unknown) 'Readable clear probes were not reported clear'
        $script:UpdatePending = $true
        Assert-True (Get-NativeRebootStatus).Required 'Windows Update reboot was missed'
        $script:UpdatePending = $false
        $script:PendingKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
        Assert-True (Get-NativeRebootStatus).Required 'Component servicing reboot was missed'
        $script:PendingKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        Assert-True (Get-NativeRebootStatus).Required 'Windows Update registry reboot was missed'
        $script:PendingKey = ''; $script:PendingRenames = @('', '\??\C:\fixture')
        Assert-True (Get-NativeRebootStatus).Required 'Pending file replacement reboot was missed'
        $script:PendingRenames = @(); $script:ProbeFailure = $true
        $status = Get-NativeRebootStatus
        Assert-True ($status.Unknown -and -not $status.Required) 'Unreadable update probe claimed clear'
        $script:ProbeFailure = $false; $script:RegistryFailure = $true
        Assert-True (Get-NativeRebootStatus).Unknown 'Unreadable registry claimed clear'
    }
    & {
        function Get-CimInstance($ClassName, $Property) { return $script:Processes }
        function Invoke-CimMethod($InputObject, $MethodName) {
            if ($InputObject.ProcessId -eq 80) { return [pscustomobject]@{ ReturnValue = 0; Sid = 'other' } }
            return [pscustomobject]@{ ReturnValue = 0; Sid = 'owner' }
        }
        $script:Processes = @(
            [pscustomobject]@{ ProcessId = 10; ParentProcessId = 1; SessionId = 2; ExecutablePath = 'C:\T3 Code.exe'; CreationDate = [datetime]::UtcNow },
            [pscustomobject]@{ ProcessId = 11; ParentProcessId = 10; SessionId = 2; ExecutablePath = 'C:\T3 Code.exe'; CreationDate = [datetime]::UtcNow },
            [pscustomobject]@{ ProcessId = 80; ParentProcessId = 1; SessionId = 2; ExecutablePath = 'C:\T3 Code.exe'; CreationDate = [datetime]::UtcNow },
            [pscustomobject]@{ ProcessId = 90; ParentProcessId = 1; SessionId = 2; ExecutablePath = 'C:\other.exe'; CreationDate = [datetime]::UtcNow }
        )
        $roots = @(Get-NativeT3Roots 'C:\T3 Code.exe' 'owner')
        Assert-True ($roots.Count -eq 1 -and $roots[0].Id -eq 10) 'Desktop inventory crossed account/path/root boundaries'
    }
    & {
        function Get-NativeT3Roots($Desktop, $OwnerSid) { return $script:Roots }
        function Start-NativeT3Desktop($Desktop) { $script:Starts++ }
        function Close-NativeT3Window($Root) {
            $script:Closes++
            if ($script:CloseFailure) { throw 'graceful close failed' }
            $script:Roots = $script:AfterClose
        }
        $root = [pscustomobject]@{ Id = 10; Session = 2; Created = '100' }
        $request = [pscustomobject]@{ Desktop = 'C:\T3 Code.exe'; OwnerSid = 'owner'; Roots = @($root) }
        $script:Starts = 0; $script:Closes = 0; $script:CloseFailure = $false; $script:AfterClose = @()
        $script:Roots = @()
        Assert-True ((Complete-NativeT3Desktop $request 2) -eq 'started' -and $script:Starts -eq 1) 'Stopped desktop did not start'
        $script:Starts = 0; $script:Roots = @($root)
        Assert-True ((Complete-NativeT3Desktop $request 2) -eq 'restarted' -and $script:Closes -eq 1 -and $script:Starts -eq 1) 'Existing desktop did not gracefully restart'
        $script:Starts = 0; $script:Closes = 0; $script:Roots = @($root); $script:CloseFailure = $true
        Assert-Throws { Complete-NativeT3Desktop $request 2 } 'graceful close failed'
        Assert-True ($script:Starts -eq 0) 'Failed graceful close launched a duplicate desktop'
        $script:CloseFailure = $false
        $replacement = [pscustomobject]@{ Id = 10; Session = 2; Created = '101' }
        $script:Roots = @($replacement)
        Assert-True ((Complete-NativeT3Desktop $request 2) -eq 'already-running' -and $script:Closes -eq 1) 'Reused PID or fresh desktop was restarted'
        $script:Closes = 0; $script:Roots = @($root); $script:AfterClose = @($replacement)
        Assert-True ((Complete-NativeT3Desktop $request 2) -eq 'already-relaunched' -and $script:Starts -eq 0) 'Application relaunch produced duplicate desktop'
        $script:Roots = @($root, $replacement)
        Assert-Throws { Complete-NativeT3Desktop $request 2 } 'Multiple T3 instances'
        $script:Closes = 0
        $otherSession = [pscustomobject]@{ Id = 20; Session = 3; Created = '200' }
        $script:Roots = @($otherSession)
        Assert-True ((Complete-NativeT3Desktop $request 2) -eq 'started' -and $script:Closes -eq 0) 'Another Windows session was closed'
    }
    & {
        $script:WindowCalls = 0; $script:WaitCalls = 0
        $script:CloseAllowed = $false; $script:ExitAllowed = $true
        $created = [datetime]::UtcNow
        $script:WindowProcess = [pscustomobject]@{ StartTime = $created }
        $script:WindowProcess | Add-Member ScriptMethod CloseMainWindow { $script:WindowCalls++; return $script:CloseAllowed }
        $script:WindowProcess | Add-Member ScriptMethod WaitForExit { param($Milliseconds) $script:WaitCalls++; return $script:ExitAllowed }
        function Get-Process($Id) { return $script:WindowProcess }
        $root = [pscustomobject]@{ Id = 10; Created = $created.ToUniversalTime().Ticks.ToString() }
        Assert-Throws { Close-NativeT3Window $root } 'no available graceful'
        Assert-True ($script:WaitCalls -eq 0) 'Unavailable graceful close waited or forced process exit'
        $script:CloseAllowed = $true; $script:ExitAllowed = $false
        Assert-Throws { Close-NativeT3Window $root } 'did not close'
        $script:ExitAllowed = $true
        Close-NativeT3Window $root
        $root.Created = '1'
        Assert-Throws { Close-NativeT3Window $root } 'identity changed'
        Assert-True ($script:WindowCalls -eq 3) 'PID reuse reached the window-close operation'
    }
    & {
        # No task or process is launched: exercise request encoding and principal.
        function Get-NativeT3Desktop { return 'C:\T3 Code.exe' }
        function Get-NativeCompletionOwner { return [pscustomobject]@{ Name = 'fixture'; User = [pscustomobject]@{ Value = 'owner' } } }
        function Get-Process($Id) { return [pscustomobject]@{ SessionId = 2 } }
        function Get-NativeT3Roots($Desktop, $OwnerSid) { return @() }
        function New-ScheduledTaskAction($Execute, $Argument) { return [pscustomobject]@{ Execute = $Execute; Argument = $Argument } }
        function New-ScheduledTaskPrincipal($UserId, $LogonType, $RunLevel) { return [pscustomobject]@{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel } }
        function New-ScheduledTaskSettingsSet($ExecutionTimeLimit, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries) {
            return [pscustomobject]@{ ExecutionTimeLimit = $ExecutionTimeLimit }
        }
        function Register-ScheduledTask($TaskName, $Action, $Principal, $Settings, [switch]$Force) {
            $script:Registered = [pscustomobject]@{ Name = $TaskName; Action = $Action; Principal = $Principal; Settings = $Settings }
        }
        function Start-ScheduledTask($TaskName) { $script:Started = $TaskName }
        function Unregister-ScheduledTask($TaskName, [switch]$Confirm) { }
        $source = Join-Path $temporary "source with spaces & quote's"
        New-Item -ItemType Directory -Path (Join-Path $source 'windows') -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $source 'windows\Complete-T3Desktop.ps1') -Value '# fixture'
        Queue-NativeT3Completion $source
        Assert-True ($script:Started -eq $script:Registered.Name -and $script:Registered.Principal.LogonType -eq 'Interactive' -and
            $script:Registered.Principal.RunLevel -eq 'Limited') 'Completion is not an independent limited interactive task'
        Assert-True ($script:Registered.Settings.ExecutionTimeLimit -eq [timespan]::Zero) 'Scheduler timeout could terminate the desktop child'
        $encoded = ($script:Registered.Action.Argument -split ' ')[-1]
        $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
        $tokens = $null; $errors = $null
        [Management.Automation.Language.Parser]::ParseInput($command, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0 -and $command.Contains("quote''s")) 'Worker paths changed across native argument encoding'
        $requestPath = @(Get-ChildItem -LiteralPath (Join-Path $temporary 'Basaltwater\t3-completion') -Filter '*.json' |
            Where-Object { $_.Name -notlike '*.status.json' })[0].FullName
        $request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        Assert-True (($request.PSObject.Properties.Name | Sort-Object) -join ',' -eq 'Desktop,OwnerSid,Roots,Session,Version') 'Completion request retained arbitrary setup/provider fields'
        Assert-True ((Get-Content -LiteralPath ($requestPath + '.status.json') -Raw | ConvertFrom-Json).Status -eq 'queued') 'Completion status was not checkpointed before dispatch'
        function Start-ScheduledTask($TaskName) { throw 'scheduler rejected start' }
        Assert-Throws { Queue-NativeT3Completion $source } 'scheduler rejected start'
        $statuses = @(Get-ChildItem -LiteralPath (Join-Path $temporary 'Basaltwater\t3-completion') -Filter '*.status.json' |
            ForEach-Object { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).Status })
        Assert-True ('manual-restart-required' -in $statuses) 'Failed queue left a successful completion status'
    }
    & {
        function Get-NativeT3Desktop { return 'C:\T3 Code.exe' }
        function Get-NativeCompletionOwner { return [pscustomobject]@{ User = [pscustomobject]@{ Value = 'owner' } } }
        function Get-Process($Id) { return [pscustomobject]@{ SessionId = 2 } }
        function Start-Sleep($Seconds) { }
        function Unregister-ScheduledTask($TaskName, [switch]$Confirm) { $script:RemovedTask = $TaskName }
        function Complete-NativeT3Desktop($Request, $Session) {
            $script:WorkerCalls++
            $otherLock = $null
            $blocked = $false
            try { $otherLock = [IO.File]::Open((Join-Path $env:LOCALAPPDATA 'Basaltwater\setup.lock'), 'Open', 'ReadWrite', 'None') }
            catch [IO.IOException] { $blocked = $true }
            finally { if ($otherLock) { $otherLock.Dispose() } }
            Assert-True $blocked 'Completion released setup lock before desktop restart'
            if ($script:WorkerFailure) { throw 'private provider credential must never be logged' }
            return 'restarted'
        }
        $directory = Join-Path $temporary 'Basaltwater\t3-completion'
        $script:WorkerCalls = 0; $script:WorkerFailure = $false
        foreach ($scenario in @('success', 'mismatch', 'failure')) {
            $id = [guid]::NewGuid().ToString('N')
            $task = 'Basaltwater-T3-Complete-' + $id
            $requestPath = Join-Path $directory ($id + '.json')
            $session = if ($scenario -eq 'mismatch') { 3 } else { 2 }
            $script:WorkerFailure = ($scenario -eq 'failure')
            @{ Version = 1; OwnerSid = 'owner'; Session = $session; Desktop = 'C:\T3 Code.exe'; Roots = @() } |
                ConvertTo-Json | Set-Content -LiteralPath $requestPath -Encoding UTF8
            Invoke-NativeT3CompletionWorker $requestPath $task
            $statusText = Get-Content -LiteralPath ($requestPath + '.status.json') -Raw
            $expected = if ($scenario -eq 'success') { 'restarted' } else { 'manual-restart-required' }
            Assert-True (($statusText | ConvertFrom-Json).Status -eq $expected) 'Worker completion/failure status was wrong'
            Assert-True ($statusText -notmatch 'private|credential' -and -not (Test-Path -LiteralPath $requestPath)) 'Worker leaked error details or retained request'
            Assert-True ($script:RemovedTask -eq $task) 'Worker did not clean up its owned task'
            $released = [IO.File]::Open((Join-Path $temporary 'Basaltwater\setup.lock'), 'Open', 'ReadWrite', 'None')
            $released.Dispose()
        }
        Assert-True ($script:WorkerCalls -eq 2) 'Session mismatch reached desktop completion'
    }
    Write-Host 'Native T3 completion tests passed.'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $env:PATH = $savedPath
    $env:SystemRoot = $savedSystemRoot
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
