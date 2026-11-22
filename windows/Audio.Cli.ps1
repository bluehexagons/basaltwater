<# Public explicit recording action. The bounded child uses PowerShell 7 while
   the ordinary desktop CLI remains usable in Windows PowerShell 5.1. #>
function Invoke-NativeProcessAudioCli([string[]]$Arguments) {
    if(-not $Arguments.Count -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help','-h'))){return @{ok=$true;help='audio --task ID --project PATH [--member PID:FILETIME] [--seconds 1] [--helper EXE]. Explicitly captures only the selected owned process tree to private task evidence. Duration 0.1-30 seconds; Windows process-loopback support required. No system-audio fallback, recording devices or preference changes. Optional helper builds with existing C++/SDK tools.'}}
    $options=@{seconds='1'};$seen=@{}
    for($i=0;$i -lt $Arguments.Count;$i++){
        $key=$Arguments[$i];if($key -eq '--json'){continue}
        if($key -notin @('--task','--project','--member','--seconds','--helper') -or $seen.ContainsKey($key) -or ++$i -ge $Arguments.Count){throw 'Unknown, duplicate or missing process-audio option'}
        $seen[$key]=$true;$options[$key.Substring(2)]=$Arguments[$i]
    }
    foreach($key in @('task','project')){if(-not $options.ContainsKey($key) -or -not $options[$key]){throw "Audio requires --$key"}}
    $seconds=0.0
    if(-not [double]::TryParse($options.seconds,[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$seconds) -or
        [double]::IsNaN($seconds) -or [double]::IsInfinity($seconds) -or $seconds -lt 0.1 -or $seconds -gt 30){throw 'Audio duration must be a finite 0.1-30 seconds'}
    if($options.task -cnotmatch '\A[0-9a-f]{32}\z'){throw 'Invalid process-audio task ID'}
    if($options.ContainsKey('member') -and $options.member -cnotmatch '\A[1-9][0-9]{0,9}:[1-9][0-9]{0,18}\z'){throw 'Expected member PID:FILETIME'}
    Assert-NativeDesktopPath $options.project
    $shell=Get-NativePowerShell7
    if(-not $shell){throw 'Process audio requires the prepared PowerShell 7 development shell'}
    # Validate retained project/task selection before compiling a missing helper.
    . (Join-Path $PSScriptRoot 'Development.Common.ps1')
    . (Join-Path $PSScriptRoot 'Development.Diagnostics.ps1')
    . (Join-Path $PSScriptRoot 'Evidence.Target.ps1')
    $null=Get-DevelopmentEvidenceTarget $options.task $options.project $options['member']
    $helper=$options['helper'];if(-not $helper){$helper=Get-NativeEvidenceTool 'Audio.Capture'}
    Assert-NativeDesktopPath $helper
    $transport=Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-audio-cli-'+[guid]::NewGuid().ToString('N'))
    New-NativeDesktopPrivateDirectory $transport
    try {
        $request=Join-Path $transport 'request.json'
        $caller=Get-NativeEvidenceCaller
        Write-NativeHeldJson $request @{task=$options.task;project=$options.project;member=$options['member'];seconds=$seconds;helper=$helper;caller_pid=$caller.pid;caller_created=$caller.filetime}
        $result=Invoke-NativeEvidenceProcess $shell @('-NoProfile','-File',(Join-Path $PSScriptRoot 'Invoke-ProcessAudio.ps1'),'-Request',$request) 55 $transport
        return $result.Receipt
    } finally {
        Assert-NativeDesktopPrivatePath $transport
        foreach($name in @('request.json','helper.stdout.log','helper.stderr.log')){
            $path=Join-Path $transport $name
            if(Test-Path -LiteralPath $path){Assert-NativeDesktopPrivatePath $path;Remove-Item -LiteralPath $path}
        }
        [IO.Directory]::Delete($transport)
    }
}
