<# Current-account Terminal, Start Menu and artifact handoff integration. #>
function Get-NativeLocalItem([string]$Path, [switch]$Directory) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -match '[\x00-\x1f]') { throw 'A local Windows path is required' }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -notmatch '\A[A-Za-z]:[\\/]' -or $full.Substring(2).Contains(':')) { throw 'Use a local Windows drive path without streams' }
    $item = Get-Item -LiteralPath $full -ErrorAction Stop
    if ($Directory -and -not $item.PSIsContainer) { throw 'Project path must be a directory' }
    return $item.FullName
}

function Install-NativeShellShortcut([string]$Path, [string]$Shell, [string]$Entry, [string]$Directory) {
    $application = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $application.CreateShortcut($Path)
        try {
            $shortcut.TargetPath = $Shell
            $shortcut.Arguments = '-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "' + $Entry + '"'
            $shortcut.WorkingDirectory = $Directory
            $shortcut.Description = 'Basaltwater native development shell'
            $shortcut.Save()
        } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
    } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) }
}

function Install-NativeShellIntegration([string]$Source, [string]$StorageRoot = '', [switch]$Plan) {
    $entry = Join-Path $Source 'windows\Enter-DevelopmentShell.ps1'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'Development shell source is missing' }
    $fragment = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\Basaltwater\profiles.json'
    $shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Basaltwater Development.lnk'
    Assert-NativeSkillPath $fragment; Assert-NativeSkillPath $shortcut
    Write-Host 'Windows integration: PowerShell 7 development profile in Terminal and Start Menu'
    if ($Plan) { return }
    $shell = Get-NativeInteractivePowerShell7
    if (-not $shell) { throw 'Stable PowerShell 7 is required for the development shell' }
    if (-not (Get-NativeCommand wt)) {
        Ensure-WingetPackage Microsoft.WindowsTerminal 'Windows Terminal installation'
        Update-NativePath
        if (-not (Get-NativeCommand wt)) { throw 'Windows Terminal was not found after installation' }
    }
    $directory = $env:USERPROFILE
    if ($StorageRoot -and (Test-Path -LiteralPath (Join-Path $StorageRoot 'repos') -PathType Container)) {
        $directory = Join-Path $StorageRoot 'repos'
    }
    $profile = @{ profiles = @(@{ guid = '{b252aa38-1ce4-44b4-9341-479f68b66e90}'; name = 'Basaltwater';
        commandline = '"' + $shell + '" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "' + $entry + '"';
        startingDirectory = $directory; hidden = $false }) }
    New-Item -ItemType Directory -Path (Split-Path -Parent $fragment), (Split-Path -Parent $shortcut) -Force | Out-Null
    $profile | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $fragment -Encoding UTF8
    Install-NativeShellShortcut $shortcut $shell $entry $directory
}

function Open-NativeDesktopItem([string]$Path, [switch]$Reveal) {
    $full = Get-NativeLocalItem $Path
    if ($Reveal) {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'explorer.exe') -ArgumentList ('/select,"' + $full + '"') -ErrorAction Stop | Out-Null
    } else { Start-Process -FilePath $full -ErrorAction Stop | Out-Null }
    return [pscustomobject]@{ Path = $full; Operation = $(if ($Reveal) { 'reveal' } else { 'open' }); Requested = $true }
}

function Open-NativeDevelopmentTerminal([string]$Source, [string]$Project, [switch]$Msvc) {
    $directory = Get-NativeLocalItem $Project -Directory
    $terminal = Get-NativeCommand wt
    if (-not $terminal) { throw 'Windows Terminal is unavailable; reapply setup' }
    $entry = Join-Path $Source 'windows\Enter-DevelopmentShell.ps1'
    $shell = Get-NativeInteractivePowerShell7
    if (-not $shell) { throw 'Stable PowerShell 7 is unavailable; reapply setup' }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(
        "& '" + $entry.Replace("'", "''") + "' -Project '" + $directory.Replace("'", "''") + "' -Msvc:`$" + ([bool]$Msvc).ToString().ToLowerInvariant()))
    # Windows argv parsing doubles trailing backslashes before a closing quote.
    # A drive root otherwise consumes the quote and the shell/options after it.
    $quotedDirectory = '"' + [regex]::Replace($directory, '(\\+)$', '$1$1') + '"'
    $arguments = '-w new -d ' + $quotedDirectory + ' "' + $shell + '" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -EncodedCommand ' + $encoded
    Start-Process -FilePath $terminal -ArgumentList $arguments -ErrorAction Stop | Out-Null
    return [pscustomobject]@{ Project = $directory; Requested = $true; Msvc = [bool]$Msvc }
}

function Show-NativeDesktopNotification([string]$Title, [string]$Message) {
    # A short-lived, nonmodal notification. Never steal focus or change settings.
    if ($Title.Length -gt 63 -or $Message.Length -gt 255) { throw 'Notification text exceeds the Windows balloon limit' }
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $icon = New-Object Windows.Forms.NotifyIcon
    try {
        $icon.Icon = [Drawing.SystemIcons]::Information
        $icon.Text = 'Basaltwater'; $icon.Visible = $true
        $icon.ShowBalloonTip(3000, $Title, $Message, [Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 3
    } finally { $icon.Dispose() }
}

function Invoke-NativeDesktopHandoff([string]$Source, [string[]]$Arguments) {
    $operation = $Arguments[0]
    $values = @{}; $positional = @(); $reveal = $false; $msvc = $false
    for ($index = 1; $index -lt $Arguments.Count; $index++) {
        $arg = $Arguments[$index]
        if ($arg -eq '--json') { continue }
        if ($arg -eq '--reveal') { $reveal = $true; continue }
        if ($arg -eq '--msvc') { $msvc = $true; continue }
        if ($arg -in @('--project','--title','--message')) {
            $index++
            if ($index -ge $Arguments.Count -or $values.ContainsKey($arg)) { throw "Missing or duplicate value for $arg" }
            $values[$arg] = $Arguments[$index]; continue
        }
        if ($arg.StartsWith('--')) { throw "Unknown desktop handoff option: $arg" }
        $positional += $arg
    }
    switch ($operation) {
        'open' {
            if ($positional.Count -ne 1 -or $values.Count -or $msvc) { throw 'Usage: basaltw desktop open PATH [--reveal]' }
            $positional[0] = Get-NativeLocalItem $positional[0]
        }
        'terminal' {
            if (-not $values.ContainsKey('--project') -or $values.Count -ne 1 -or $positional.Count -or $reveal) { throw 'Usage: basaltw desktop terminal --project PATH [--msvc]' }
            $values['--project'] = Get-NativeLocalItem $values['--project'] -Directory
        }
        'notify' {
            if (-not $values.ContainsKey('--title') -or -not $values.ContainsKey('--message') -or $values.Count -ne 2 -or $positional.Count -or $msvc -or $reveal) { throw 'Usage: basaltw desktop notify --title TEXT --message TEXT' }
            if ($values['--title'].Length -gt 63 -or $values['--message'].Length -gt 255) { throw 'Notification text exceeds the Windows balloon limit' }
        }
        default { throw 'Unsupported desktop handoff operation' }
    }
    $lock = Enter-NativeDesktopLock (Get-NativeDesktopContext)
    try {
    $state = Read-NativeDesktopState (Get-NativeDesktopContext)
    if ($state.mode -ne 'enabled') { throw "Agent desktop control is $($state.mode)" }
    $ready = Get-NativeDesktopReadiness
    if (-not $ready.healthy) { throw 'The current desktop is unavailable; inspect desktop doctor' }
    $state.windows = @(); $state.elements = @()
    Write-NativeDesktopState $state
    switch ($operation) {
        'open' {
            return Open-NativeDesktopItem $positional[0] -Reveal:$reveal
        }
        'terminal' {
            return Open-NativeDevelopmentTerminal $Source $values['--project'] -Msvc:$msvc
        }
        'notify' {
            Show-NativeDesktopNotification $values['--title'] $values['--message']
            return [pscustomobject]@{ Requested = $true; Operation = 'notify' }
        }
    }
    } finally { Exit-NativeDesktopLock $lock }
}
