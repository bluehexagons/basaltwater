<# Parent-only live qualification fixture. Never invoked by offline tests.
   Generates its own PCM tone; does not change routing, device or preferences. #>
[CmdletBinding()]
param([ValidateRange(20,2000)][int]$Frequency = 440,
    [ValidateRange(1,120)][int]$DurationSeconds = 60,
    [string]$ProfilePath = $env:BASALTWATER_TEST_PROFILE)
$ErrorActionPreference = 'Stop'
if (-not $ProfilePath -or -not (Test-Path -LiteralPath $ProfilePath -PathType Container)) { throw 'Supply an existing disposable fixture profile' }
Add-Type -AssemblyName System.Windows.Extensions
$memory = [IO.MemoryStream]::new()
$writer = [IO.BinaryWriter]::new($memory,[Text.Encoding]::ASCII,$true)
$player = $null
function Publish-ToneState([bool]$Playing) {
    $path=Join-Path $ProfilePath 'tone-state.json';$temporary=$path+'.tmp'
    [IO.File]::WriteAllText($temporary,(@{playing=$Playing;frequency=$Frequency;pid=$PID;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress))
    if(Test-Path -LiteralPath $path){[IO.File]::Replace($temporary,$path,[Management.Automation.Language.NullString]::Value)}else{[IO.File]::Move($temporary,$path)}
}
try {
    $writer.Write([Text.Encoding]::ASCII.GetBytes('RIFF')); $writer.Write([int]88236)
    $writer.Write([Text.Encoding]::ASCII.GetBytes('WAVEfmt ')); $writer.Write([int]16)
    $writer.Write([int16]1); $writer.Write([int16]1); $writer.Write([int]44100)
    $writer.Write([int]88200); $writer.Write([int16]2); $writer.Write([int16]16)
    $writer.Write([Text.Encoding]::ASCII.GetBytes('data')); $writer.Write([int]88200)
    for ($i = 0; $i -lt 44100; $i++) {
        $writer.Write([int16][Math]::Round(1638 * [Math]::Sin(2*[Math]::PI*$Frequency*$i/44100)))
    }
    $writer.Flush(); $memory.Position = 0
    $player = [System.Media.SoundPlayer]::new($memory); $player.Load(); $player.PlayLooping()
    Publish-ToneState $true
    if ($env:BASALTWATER_TEST_READY_FILE -and $env:BASALTWATER_TEST_READY_TOKEN) {
        $temporary = $env:BASALTWATER_TEST_READY_FILE + '.tone-tmp'
        [IO.File]::WriteAllText($temporary,(@{ Token = $env:BASALTWATER_TEST_READY_TOKEN } | ConvertTo-Json))
        [IO.File]::Move($temporary,$env:BASALTWATER_TEST_READY_FILE)
    }
    $clock = [Diagnostics.Stopwatch]::StartNew(); $playing = $true
    while ($clock.Elapsed.TotalSeconds -lt $DurationSeconds) {
        if(Test-Path -LiteralPath (Join-Path $ProfilePath 'stop')){break}
        $silent = Test-Path -LiteralPath (Join-Path $ProfilePath 'silence')
        if ($silent -and $playing) { $player.Stop(); $playing = $false; Publish-ToneState $false }
        elseif (-not $silent -and -not $playing) { $player.PlayLooping(); $playing = $true; Publish-ToneState $true }
        Start-Sleep -Milliseconds 20
    }
} finally {
    if ($player) { $player.Stop(); $player.Dispose() }
    Publish-ToneState $false
    $writer.Dispose(); $memory.Dispose()
}
