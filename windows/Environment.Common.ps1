<# Read-only host inventory. Use the existing isolated eight-second worker;
   no UIA/window inventory, pixels, input, preference changes or tool launches. #>
function Get-NativeEnvironmentGraphics {
    $controllers = @(Get-CimInstance -ClassName Win32_VideoController -Property Name,DriverVersion,AdapterCompatibility -OperationTimeoutSec 3 -ErrorAction Stop | Select-Object -First 33)
    if ($controllers.Count -gt 32) { throw 'Graphics inventory exceeds 32-controller limit' }
    return @($controllers | ForEach-Object {
        [pscustomobject]@{
            Name = ([string]$_.Name).Substring(0, [Math]::Min(256, ([string]$_.Name).Length));
            DriverVersion = ([string]$_.DriverVersion).Substring(0, [Math]::Min(128, ([string]$_.DriverVersion).Length));
            Vendor = ([string]$_.AdapterCompatibility).Substring(0, [Math]::Min(128, ([string]$_.AdapterCompatibility).Length))
            Truncated = (([string]$_.Name).Length -gt 256 -or ([string]$_.DriverVersion).Length -gt 128 -or ([string]$_.AdapterCompatibility).Length -gt 128)
        }
    })
}
function Get-NativeEnvironmentDisplays {
    if (-not ('Basaltwater.EnvironmentInfo.Native' -as [type])) {
        Add-Type -Path (Join-Path $PSScriptRoot 'Environment.Native.cs') -ErrorAction Stop
    }
    return @([Basaltwater.EnvironmentInfo.Native]::Displays())
}
function Invoke-NativeEnvironmentWorkerBody {
    $started = [DateTime]::UtcNow.ToString('o'); $clock = [Diagnostics.Stopwatch]::StartNew()
    $graphics = $null; $displays = $null; $errors = @()
    try { $graphics = @(Get-NativeEnvironmentGraphics) } catch { $errors += 'Graphics: ' + $_.Exception.Message }
    try { $displays = @(Get-NativeEnvironmentDisplays) } catch { $errors += 'Display: ' + $_.Exception.Message }
    $state = if ($null -eq $graphics -and $null -eq $displays) { 'unavailable' } elseif ($errors.Count) { 'partial' } else { 'available' }
    return [pscustomobject]@{
        SchemaVersion = 1; State = $state; StartedUtc = $started; CapturedUtc = [DateTime]::UtcNow.ToString('o');
        ProbeSeconds = $clock.Elapsed.TotalSeconds; WindowsVersion = [Environment]::OSVersion.VersionString;
        Graphics = $graphics; Displays = $displays; Errors = @($errors);
        Dpi = $null; Hdr = $null; Uncollected = @('per-window DPI','HDR state','active renderer GPU','VRR/scanout timing');
        CapturePerformed = $false; Source = 'Win32_VideoController; EnumDisplayMonitors/EnumDisplaySettings; GetScaleFactorForMonitor';
        Limitations = 'Host configuration at capture time, not renderer qualification. Refresh is integer desktop mode Hz; scale is the API scale factor, not per-window DPI.'
    }
}
function Get-NativeEnvironmentReport {
    try { return Invoke-NativeDesktopWorker @{ command = 'environment' } }
    catch {
        return [pscustomobject]@{
            SchemaVersion = 1; State = 'unavailable'; CapturedUtc = [DateTime]::UtcNow.ToString('o');
            Graphics = $null; Displays = $null; Errors = @($_.Exception.Message);
            Dpi = $null; Hdr = $null; CapturePerformed = $false
        }
    }
}
