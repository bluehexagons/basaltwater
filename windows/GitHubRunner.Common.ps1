<# Shared native Windows runner paths, state and bounded subprocess handling. #>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:RunnerVersion = '2.338.1'
$script:RunnerSha256 = '478f533ac2c349040fbaea7e158f7f7d0235085cf35bc81f661af0c715207ff9'

function Get-RunnerRoot {
    return Join-Path $env:LOCALAPPDATA 'Basaltwater\github-runner'
}

function Get-RunnerOwnerSid { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }

function Write-RunnerJson([string]$Path, $Value) {
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
}

function Get-RunnerId([string]$Target) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::ASCII.GetBytes(
            $Target.ToLowerInvariant()))).Replace('-', '').ToLowerInvariant()).Substring(0, 16)
    } finally { $hash.Dispose() }
}

function Get-RunnerSetup {
    $state = Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'Basaltwater\wsl-setup.json') -Raw | ConvertFrom-Json
    if ($state.OwnerSid -ne (Get-RunnerOwnerSid) -or
        $state.Phase -ne 'complete' -or $state.Revision -notmatch '^[0-9a-f]{40}$' -or
        $state.Distro -notmatch '^Ubuntu(?:-[0-9]{2}\.[0-9]{2})?$' -or
        $state.LinuxUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$' -or $state.LinuxUser -eq 'root') {
        throw 'Run as the owner of a completed Basaltwater Ubuntu WSL setup'
    }
    return $state
}

function ConvertTo-RunnerWslPath([string]$Path, $Setup) {
    $translated = & wsl.exe --distribution $Setup.Distro --user $Setup.LinuxUser --exec /usr/bin/wslpath -u $Path
    if ($LASTEXITCODE -ne 0 -or -not $translated) { throw 'Could not translate a runner path into WSL' }
    return ($translated | Select-Object -Last 1).Trim()
}

function Invoke-RunnerGate($Setup, [string[]]$Arguments) {
    $entry = '/opt/basaltwater/releases/' + $Setup.Revision + '/windows/runner_gate.py'
    & wsl.exe --distribution $Setup.Distro --user $Setup.LinuxUser --exec /usr/bin/python3 -I $entry @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'GitHub runner identity or configuration check failed' }
}

function Invoke-RunnerProcess([string]$File, [string]$Arguments, [string]$Directory,
                              [hashtable]$Environment = @{}, [int]$TimeoutSeconds = 240) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($name in @('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN',
                        'GH_DEBUG', 'RUNNER_TRACKING_ID', 'ACTIONS_RUNNER_INPUT_TOKEN')) {
        $info.EnvironmentVariables.Remove($name)
    }
    foreach ($name in @($info.EnvironmentVariables.Keys)) {
        if ($name.StartsWith('ACTIONS_RUNNER_INPUT_', [StringComparison]::OrdinalIgnoreCase)) {
            $info.EnvironmentVariables.Remove($name)
        }
    }
    foreach ($name in $Environment.Keys) { $info.EnvironmentVariables[$name] = $Environment[$name] }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Runner process could not start' }
        # Consume and discard vendor output: registration diagnostics can contain tokens.
        $output = $process.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
        $errors = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
            throw 'Runner registration timed out; check connectivity and retry with a fresh token'
        }
        if (-not $output.Wait(10000) -or -not $errors.Wait(10000)) { throw 'Runner output did not close' }
        if ($process.ExitCode -ne 0) {
            throw 'Runner configuration failed; check the scope, connectivity and fresh registration token'
        }
    } finally { $process.Dispose() }
}

function Get-RunnerPolicies {
    $instances = Join-Path (Get-RunnerRoot) 'instances'
    if (Test-Path -LiteralPath $instances) {
        $directories = @(Get-ChildItem -LiteralPath $instances -Directory)
        if ($directories.Count -gt 100) { throw 'Too many saved runner instances' }
        foreach ($directory in $directories) {
            if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runner instance must not be a reparse point' }
            if ($directory.Name -notmatch '^[a-f0-9]{16}$') { throw 'Invalid runner instance directory' }
            $path = Join-Path $directory.FullName 'policy.json'
            if (Test-Path -LiteralPath $path) {
                [pscustomobject]@{ Directory = $directory.FullName; Policy = (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json) }
            }
        }
    }
}

function Get-RunnerTaskName([string]$Id) { return 'Basaltwater GitHub runner ' + $Id }

function Import-RunnerJobType {
    if ('Basaltwater.ProcessJob' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace Basaltwater {
    // A guardian or launcher crash must stop its worker before releasing the slot.
    public sealed class ProcessJob : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Basic {
            public long PerProcess, PerJob;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcesses;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] struct Io {
            public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] struct Extended {
            public Basic Basic;
            public Io Io;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint length);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source,
            IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        IntPtr handle;
        public ProcessJob(IntPtr process) {
            handle = CreateJobObject(IntPtr.Zero, null);
            var info = new Extended(); info.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE
            if (handle == IntPtr.Zero || !SetInformationJobObject(handle, 9, ref info,
                (uint)Marshal.SizeOf(info)) || !AssignProcessToJobObject(handle, process)) {
                int error = Marshal.GetLastWin32Error(); Dispose(); throw new Win32Exception(error);
            }
        }
        public void Dispose() {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
            GC.SuppressFinalize(this);
        }
        public static void KeepSlotUntilExit(IntPtr slot, IntPtr worker) {
            IntPtr duplicate;
            // The worker owns this duplicate until it exits. Even forcible
            // guardian termination cannot free the slot before the worker dies.
            if (!DuplicateHandle(GetCurrentProcess(), slot, worker, out duplicate, 0, false, 2))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        ~ProcessJob() { Dispose(); }
    }
}
'@
}

function Assert-RunnerArguments([string[]]$Repository, [string]$Organization, [string[]]$AllowedUser,
                                [string[]]$Label, [string]$RunnerGroup, [bool]$RegisterWithGh,
                                [string]$RegistrationTokenFile, [bool]$PromptToken) {
    $login = '[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?'
    if (($Repository.Count -eq 0) -eq (-not $Organization)) { throw 'Choose Repository or Organization' }
    if ($Repository.Count -gt 20 -or @($Repository | Sort-Object -Unique).Count -ne $Repository.Count) {
        throw 'Use at most 20 distinct repositories'
    }
    foreach ($repo in $Repository) {
        if ($repo -notmatch ('^' + $login + '/[A-Za-z0-9_.-]{1,100}$') -or $repo.Split('/')[1] -in @('.', '..')) {
            throw 'Repository must be OWNER/REPO on github.com'
        }
    }
    if ($Organization -and $Organization -notmatch ('^' + $login + '$')) { throw 'Invalid organization login' }
    if ($RunnerGroup -and (-not $Organization -or $RunnerGroup.Length -gt 100 -or
        $RunnerGroup.Trim() -ne $RunnerGroup -or $RunnerGroup -match '[\x00-\x1f\x7f]')) { throw 'Invalid organization runner group' }
    if ($AllowedUser.Count -lt 1 -or $AllowedUser.Count -gt 100) { throw 'Specify 1-100 allowed GitHub users' }
    foreach ($user in $AllowedUser) {
        if ($user -notmatch ('^' + $login + '$')) { throw 'Invalid allowed GitHub user' }
    }
    if ($Label.Count -gt 20) { throw 'Use at most 20 runner labels' }
    foreach ($value in $Label) {
        if ($value -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') { throw 'Invalid runner label' }
    }
    if (([int]$RegisterWithGh + [int][bool]$RegistrationTokenFile + [int]$PromptToken) -gt 1) {
        throw 'Choose RegisterWithGh, RegistrationTokenFile or PromptToken'
    }
    if ($Repository.Count -gt 1 -and ($RegistrationTokenFile -or $PromptToken)) {
        throw 'Use RegisterWithGh for multiple repositories, or register each repository separately'
    }
}
