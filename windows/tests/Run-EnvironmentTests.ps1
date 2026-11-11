<# Read-only environment contracts. Native APIs/CIM are never invoked here. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
. (Join-Path $PSScriptRoot '..\Environment.Common.ps1')
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    Assert-True ($null -ne $caught -and $caught.Exception.Message -match $Pattern) "Expected $Pattern; got $caught"
}
$graphicsImplementation = ${function:Get-NativeEnvironmentGraphics}
& {
    function Get-CimInstance {
        param($ClassName, $Property, $OperationTimeoutSec, $ErrorAction)
        Assert-True ($ClassName -eq 'Win32_VideoController' -and $OperationTimeoutSec -eq 3 -and
            ($Property -join ',') -eq 'Name,DriverVersion,AdapterCompatibility') 'CIM query exceeded its intended scope'
        [pscustomobject]@{ Name = ('g' * 300); DriverVersion = '2.3'; AdapterCompatibility = 'Fixture'; PrivateInventory = 'do-not-retain' }
    }
    $graphics = @(& $graphicsImplementation)
    Assert-True ($graphics.Count -eq 1 -and $graphics[0].Name.Length -eq 256 -and $graphics[0].Truncated -and
        $graphics[0].DriverVersion -eq '2.3' -and -not $graphics[0].PSObject.Properties['PrivateInventory']) 'Graphics evidence lost bounds, version or minimization'
    function Get-CimInstance { 1..33 | ForEach-Object { [pscustomobject]@{ Name = 'Fixture'; DriverVersion = '1'; AdapterCompatibility = 'Fixture' } } }
    Assert-Throws { & $graphicsImplementation } '32-controller limit'
}
& {
    function Get-NativeEnvironmentActivePowerScheme { return '00000000-0000-0000-0000-000000000001' }
    function Get-NativeEnvironmentPowerStatus {
        return [pscustomobject]@{ AcOnline = $false; BatteryPresent = $true; BatteryPercent = 42; BatterySaver = $true }
    }
    $power = Get-NativeEnvironmentPower
    Assert-True ($power.State -eq 'available' -and $power.ActiveSchemeGuid -eq '00000000-0000-0000-0000-000000000001' -and
        $power.AcOnline -eq $false -and $power.BatteryPresent -eq $true -and $power.BatteryPercent -eq 42 -and
        $power.BatterySaver -eq $true -and $power.Errors.Count -eq 0) 'Power snapshot lost observed policy/status'
    function Get-NativeEnvironmentActivePowerScheme { throw 'fixture scheme unavailable' }
    $power = Get-NativeEnvironmentPower
    Assert-True ($power.State -eq 'partial' -and $null -eq $power.ActiveSchemeGuid -and $power.BatteryPercent -eq 42 -and
        $power.Errors.Count -eq 1 -and $power.Errors[0] -match 'scheme unavailable') 'Missing scheme discarded independent power status'
    function Get-NativeEnvironmentPowerStatus { throw 'fixture power status unavailable' }
    $power = Get-NativeEnvironmentPower
    Assert-True ($power.State -eq 'unavailable' -and $null -eq $power.AcOnline -and $null -eq $power.BatteryPresent -and
        $null -eq $power.BatteryPercent -and $null -eq $power.BatterySaver -and $power.Errors.Count -eq 2) 'Power failure invented observed false/zero values'
    function Get-NativeEnvironmentActivePowerScheme { return '00000000-0000-0000-0000-000000000002' }
    $power = Get-NativeEnvironmentPower
    Assert-True ($power.State -eq 'partial' -and $power.ActiveSchemeGuid -eq '00000000-0000-0000-0000-000000000002' -and
        $null -eq $power.AcOnline -and $power.Errors.Count -eq 1) 'Missing power status discarded independent scheme'
}
& {
    function Get-NativeEnvironmentGraphics { return [pscustomobject]@{ Name = 'Fixture GPU'; DriverVersion = '2.3' } }
    function Get-NativeEnvironmentDisplays { return [pscustomobject]@{ Id = 'Fixture display'; RefreshRateHz = 60; ScalePercent = 125 } }
    function Get-NativeEnvironmentPower { return [pscustomobject]@{ State = 'available'; ActiveSchemeGuid = '00000000-0000-0000-0000-000000000001'; Errors = @() } }
    $available = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($available.State -eq 'available' -and $available.Graphics.Count -eq 1 -and $available.Displays.Count -eq 1 -and
        -not $available.CapturePerformed -and $null -eq $available.Dpi -and $null -eq $available.Hdr -and
        $available.Errors.Count -eq 0 -and $available.StartedUtc -and $available.ProbeSeconds -ge 0 -and
        $available.Power.ActiveSchemeGuid -eq '00000000-0000-0000-0000-000000000001') 'Available inventory invented measurements or lost provenance'
    function Get-NativeEnvironmentPower { return [pscustomobject]@{ State = 'partial'; ActiveSchemeGuid = $null; Errors = @('fixture power unavailable') } }
    $partial = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($partial.State -eq 'partial' -and $partial.Graphics.Count -eq 1 -and $partial.Displays.Count -eq 1 -and
        $partial.Power.State -eq 'partial' -and $partial.Errors[0] -match 'Power: fixture') 'Power failure discarded unrelated evidence or claimed complete success'
    function Get-NativeEnvironmentPower { return [pscustomobject]@{ State = 'available'; ActiveSchemeGuid = '00000000-0000-0000-0000-000000000001'; Errors = @() } }
    function Get-NativeEnvironmentGraphics { throw 'fixture CIM unavailable' }
    $partial = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($partial.State -eq 'partial' -and $null -eq $partial.Graphics -and $partial.Displays.Count -eq 1 -and
        $partial.Errors[0] -match 'CIM unavailable') 'Graphics failure discarded display evidence'
    function Get-NativeEnvironmentDisplays { throw 'fixture session unavailable' }
    $missing = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($missing.State -eq 'partial' -and $null -eq $missing.Displays -and $missing.Power.State -eq 'available' -and
        $missing.Errors.Count -eq 2) 'Graphics/display failure discarded independent power evidence'
    function Get-NativeEnvironmentPower { return [pscustomobject]@{ State = 'unavailable'; Errors = @('fixture power unavailable') } }
    $missing = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($missing.State -eq 'unavailable' -and $null -eq $missing.Displays -and $missing.Errors.Count -eq 3) 'Unavailable inventory claimed partial success'
    function Get-NativeEnvironmentPower { throw 'fixture native types unavailable' }
    $missing = Invoke-NativeEnvironmentWorkerBody
    Assert-True ($missing.State -eq 'unavailable' -and $null -eq $missing.Power -and
        $missing.Errors[2] -match 'native types unavailable') 'Power worker failure escaped evidence collection'
}
& {
    function Get-NativeDesktopContext { throw 'Environment read account control state' }
    function Read-NativeDesktopState { throw 'Environment read desktop inventory' }
    function Write-NativeDesktopState { throw 'Environment wrote desktop state' }
    $script:EnvironmentCalls = 0
    function Invoke-NativeDesktopWorker($Request, [int]$TimeoutSeconds = 8) {
        Assert-True ($Request.command -eq 'environment' -and $TimeoutSeconds -eq 8) 'Environment escaped bounded worker'
        $script:EnvironmentCalls++
        return [pscustomobject]@{ State = 'partial'; Graphics = @(); Displays = $null; CapturePerformed = $false }
    }
    $result = Invoke-NativeDesktopCli @('environment','--json')
    Assert-True ($result.ok -and $result.result.State -eq 'partial' -and $script:EnvironmentCalls -eq 1) 'Read-only environment command failed or queried control state'
    $invalid = Invoke-NativeDesktopCli @('environment','--window','123')
    Assert-True (-not $invalid.ok -and $script:EnvironmentCalls -eq 1) 'Environment accepted mutation/window selectors'
    function Invoke-NativeDesktopWorker { throw 'fixture worker timed out' }
    $result = Invoke-NativeDesktopCli @('environment')
    Assert-True (-not $result.ok -and $result.result.State -eq 'unavailable' -and $null -eq $result.result.Power -and
        $result.result.Errors[0] -match 'timed out') 'Worker failure lost unavailable state or CLI failure'
}
# Compile and exercise actual native conversion rules without calling user32/shcore.
if (-not ('Basaltwater.EnvironmentInfo.Native' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot '..\Environment.Native.cs') -ErrorAction Stop
}
$method = [Basaltwater.EnvironmentInfo.Native].GetMethod('ApplyMode', [Reflection.BindingFlags]'Static,NonPublic')
$apply = $method.CreateDelegate([Action[Basaltwater.EnvironmentInfo.Display,bool,uint32,uint32,int,int]])
$display = New-Object Basaltwater.EnvironmentInfo.Display
$apply.Invoke($display,$true,0x400000,144,0,125)
Assert-True ($display.RefreshRateHz -eq 144 -and $display.ScalePercent -eq 125) 'Reported desktop mode/scale lost'
foreach ($case in @(@($true,0x400000,0,-1,100), @($true,0x400000,1,-1,100), @($false,0x400000,144,-1,100), @($true,0,144,-1,100))) {
    $display = New-Object Basaltwater.EnvironmentInfo.Display
    $apply.Invoke($display,$case[0],$case[1],$case[2],$case[3],$case[4])
    Assert-True ($null -eq $display.RefreshRateHz -and $null -eq $display.ScalePercent) 'Driver/API fallback was presented as an observed value'
}
$method = [Basaltwater.EnvironmentInfo.Native].GetMethod('ConvertPowerStatus', [Reflection.BindingFlags]'Static,NonPublic')
$convert = $method.CreateDelegate([Func[byte,byte,byte,byte,Basaltwater.EnvironmentInfo.PowerStatus]])
$power = $convert.Invoke(0,8,42,1)
Assert-True ($power.AcOnline -eq $false -and $power.BatteryPresent -eq $true -and $power.BatteryPercent -eq 42 -and
    $power.BatterySaver -eq $true) 'Known battery power status lost'
$power = $convert.Invoke(1,128,0,0)
Assert-True ($power.AcOnline -eq $true -and $power.BatteryPresent -eq $false -and $null -eq $power.BatteryPercent -and
    $power.BatterySaver -eq $false) 'No-battery status invented a zero-percent charge'
foreach ($percent in @(101,255)) {
    $power = $convert.Invoke(255,255,$percent,255)
    Assert-True ($null -eq $power.AcOnline -and $null -eq $power.BatteryPresent -and $null -eq $power.BatteryPercent -and
        $null -eq $power.BatterySaver) 'Unknown/invalid power data was presented as false or zero'
}
foreach ($percent in @(0,100)) {
    $power = $convert.Invoke(1,0,$percent,0)
    Assert-True ($power.BatteryPresent -eq $true -and $power.BatteryPercent -eq $percent) 'Valid charge boundary discarded'
}
Write-Host 'Windows environment contracts passed (no live hardware/desktop calls).'
