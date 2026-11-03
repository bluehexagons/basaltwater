<# Pure event construction and mocked SendInput; no window or input APIs. #>
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '..\Desktop.Common.ps1')
Initialize-NativeDesktopTypes
function Assert-Transport([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
$flags=[Reflection.BindingFlags]'Static,NonPublic'
$unicode=[Basaltwater.Desktop.Native].GetMethod('UnicodeInputs',$flags)
$keys=[Basaltwater.Desktop.Native].GetMethod('SendKeys',$flags)
$pair=[Basaltwater.Desktop.Native].GetMethod('SendPair',$flags)
$click=[Basaltwater.Desktop.Native].GetMethod('ClickInputs',$flags).Invoke($null,@())
$text='A'+[char]0x65e5+'e'+[char]0x301+[char]::ConvertFromUtf32(0x1f600)
$events=$unicode.Invoke($null,@($text))
Assert-Transport ($events.Length -eq 2*$text.Length) 'Unicode request lost UTF16 units'
for($i=0;$i -lt $text.Length;$i++){
    Assert-Transport ($events[2*$i].data.key.scan -eq [uint16]$text[$i] -and $events[2*$i].data.key.flags -eq 4 -and $events[2*$i+1].data.key.flags -eq 6) 'Unicode event order/flags changed'
}
$script:TransportCalls=@();$script:TransportFirstCount=[uint32]$events.Length;$script:TransportReleaseFails=$false
$send=[Management.Automation.LanguagePrimitives]::ConvertTo({param($batch)
    $script:TransportCalls+=,$batch
    if($script:TransportCalls.Count -eq 1){return $script:TransportFirstCount}
    if($script:TransportReleaseFails){return [uint32]0}
    return [uint32]$batch.Length
},$keys.GetParameters()[1].ParameterType)
$null=$keys.Invoke($null,@($events,$send))
Assert-Transport ($script:TransportCalls.Count -eq 1) 'Unpaced text made one injection per character'
$script:TransportCalls=@();$script:TransportFirstCount=[uint32]3;$caught=$null
try{$null=$keys.Invoke($null,@($events,$send))}catch{$caught=$_}
Assert-Transport ($null -ne $caught -and $script:TransportCalls.Count -eq 2 -and $script:TransportCalls[1].Length -eq 1 -and $script:TransportCalls[1][0].data.key.flags -eq 6 -and $script:TransportCalls[1][0].data.key.scan -eq [uint16]$text[1]) 'Partial Unicode delivery was replayed or released the wrong unit'
foreach($count in @(0,1)){
    $script:TransportCalls=@();$script:TransportFirstCount=[uint32]$count;$caught=$null
    try{$null=$pair.Invoke($null,@($click,$send))}catch{$caught=$_}
    Assert-Transport ($null -ne $caught -and $script:TransportCalls.Count -eq (1+$count)) 'Rejected click released unrelated state or replayed'
    if($count -eq 1){Assert-Transport ($script:TransportCalls[1].Length -eq 1 -and $script:TransportCalls[1][0].data.mouse.flags -eq 4) 'Partial click did not release its accepted down'}
}
$script:TransportCalls=@();$script:TransportFirstCount=[uint32]1;$script:TransportReleaseFails=$true;$caught=$null
try{$null=$pair.Invoke($null,@($click,$send))}catch{$caught=$_}
Assert-Transport ($caught.Exception.Message -match 'unconfirmed' -and $script:TransportCalls.Count -eq 2) 'Unconfirmed mouse release was hidden or retried'
# Admission happens before the first native window probe.
$caught=$null
try{[Basaltwater.Desktop.Native]::InputAction(0,'invalid','text','x',0,0,1,1,[Func[string]]{return 'fixture caller expired'})}catch{$caught=$_}
Assert-Transport ($caught.Exception.Message -match 'fixture caller expired') 'Caller/deadline guard ran after native input admission'
Write-Host 'Atomic Unicode, partial click and caller guard contracts passed (transport mocked).'
