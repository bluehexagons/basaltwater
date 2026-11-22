<# Process-identity-bound, explicitly selected PresentMon 2.6 CSV collection.
   Requires Capture.Common, Development.Common and Development.Diagnostics. #>
. (Join-Path $PSScriptRoot 'Evidence.Target.ps1')
function Get-NativePresentMon([string]$Path='') {
    if(-not $Path){$Path=Join-Path $env:LOCALAPPDATA 'Basaltwater\native-evidence-tools\PresentMon-2.6.0\PresentMon-2.6.0-x64.exe'}
    Assert-NativeDesktopPath $Path
    if(-not(Test-Path -LiteralPath $Path -PathType Leaf)){throw 'PresentMon absent; explicitly run desktop pacing install, or select a reviewed 2.6.0 executable with --presentmon PATH'}
    if([IO.Path]::GetExtension($Path) -ine '.exe'){throw 'PresentMon must be a native executable'}
    # This interface is qualified against a single reproducible console build.
    if((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne 'b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af'){throw 'PresentMon build is not the qualified 2.6.0 console release; review/update its CLI contract explicitly'}
    return $Path
}
function Install-NativePresentMon {
    $root=Join-Path $env:LOCALAPPDATA 'Basaltwater\native-evidence-tools\PresentMon-2.6.0'
    New-NativeDesktopPrivateDirectory $root
    $exe=Join-Path $root 'PresentMon-2.6.0-x64.exe'
    if(Test-Path -LiteralPath $exe){return @{ok=$true;path=(Get-NativePresentMon $exe);version='2.6.0'}}
    $lease=[IO.File]::Open((Join-Path $root 'install.lock'),'OpenOrCreate','ReadWrite','None')
    $temporary=Join-Path $root ([guid]::NewGuid().ToString('N')+'.exe')
    try {
        if(Test-Path -LiteralPath $exe){return @{ok=$true;path=(Get-NativePresentMon $exe);version='2.6.0'}}
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe' -OutFile $temporary -TimeoutSec 120
        $null=Get-NativePresentMon $temporary
        [IO.File]::Move($temporary,$exe)
        return @{ok=$true;path=$exe;version='2.6.0';source='GameTechDev/PresentMon v2.6.0 console; release SHA256 verified'}
    } finally {if(Test-Path -LiteralPath $temporary){Remove-Item -LiteralPath $temporary};$lease.Dispose()}
}
function Get-NativePacingSummary([string]$Path,[int]$ProcessId) {
    Assert-NativeDesktopPrivatePath $Path
    if((Get-Item -LiteralPath $Path).Length -gt 64MB){throw 'PresentMon CSV exceeds 64 MiB'}
    $chains=@{};$rows=0;$unavailable=0
    Import-Csv -LiteralPath $Path | ForEach-Object {
        $row=$_;$rows++
        if($rows -gt 50000){throw 'PresentMon summary exceeds 50000 rows; raw CSV retained'}
        if(-not $row.PSObject.Properties['ProcessID'] -or [string]$row.ProcessID -cne $ProcessId.ToString([Globalization.CultureInfo]::InvariantCulture)) {throw 'PresentMon returned data outside the pinned process identity'}
        if(-not $row.PSObject.Properties['SwapChainAddress'] -or -not $row.PSObject.Properties['CPUFrameTime'] -or -not $row.PSObject.Properties['CPUStartQPC']){throw 'PresentMon CSV contract differs from qualified v2 QPC metrics'}
        $key=[string]$row.SwapChainAddress
        if($key -notmatch '\A0x[0-9a-fA-F]{1,16}\z'){throw 'Invalid PresentMon swap-chain identity'}
        if(-not $chains.ContainsKey($key)){
            if($chains.Count -ge 8){throw 'PresentMon swap-chain summary limit is eight'}
            $chains[$key]=@{values=(New-Object 'Collections.Generic.List[double]');runtimes=@{};modes=@{};last_qpc=0L;first_qpc=$null}
        }
        $chain=$chains[$key]
        $qpc=0L
        if([string]$row.CPUStartQPC -notmatch '\A[0-9]{1,19}\z' -or -not [long]::TryParse($row.CPUStartQPC,[ref]$qpc) -or $qpc -lt $chain.last_qpc){throw 'PresentMon QPC order invalid within swap chain'}
        if($null -eq $chain.first_qpc){$chain.first_qpc=$qpc};$chain.last_qpc=$qpc
        if($row.PSObject.Properties['PresentRuntime']){$chain.runtimes[[string]$row.PresentRuntime]=$true}
        if($row.PSObject.Properties['PresentMode']){$chain.modes[[string]$row.PresentMode]=$true}
        $value=0.0
        if([double]::TryParse([string]$row.CPUFrameTime,[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$value) -and
            -not [double]::IsNaN($value) -and -not [double]::IsInfinity($value) -and $value -gt 0){$chain.values.Add($value)}else{$unavailable++}
    }
    if($rows -eq 0){throw 'No presentation rows observed; an empty trace is not a smooth-run result'}
    $summaries=@(foreach($key in $chains.Keys){
        $chain=$chains[$key];$values=@($chain.values | Sort-Object);$count=$values.Count
        @{swap_chain=$key;samples=$count;cpu_frame_ms_mean=$(if($count){($values | Measure-Object -Average).Average}else{$null});cpu_frame_ms_max=$(if($count){$values[-1]}else{$null});
            cpu_frame_ms_p95=$(if($count -ge 20){$values[[int][Math]::Ceiling($count*0.95)-1]}else{$null});
            hitches_over_50ms=@($values | Where-Object {$_ -gt 50}).Count;first_qpc=$chain.first_qpc;last_qpc=$chain.last_qpc;
            runtimes=@($chain.runtimes.Keys);present_modes=@($chain.modes.Keys)}
    })
    return @{rows=$rows;unavailable_cpu_intervals=$unavailable;swap_chains=$summaries;metric='PresentMon CPUFrameTime milliseconds, per swap chain';event_loss=$null;
        limitations=@('CPU frame intervals are not physical display scanout or input latency','PresentMon OpenGL/Vulkan metrics have documented limitations','Event-loss count unavailable unless separately established from retained collector diagnostics','Raw CSV is retained; raw ETL is not collected by this console route')}
}
function Invoke-NativePacing([string[]]$Arguments) {
    if(-not $Arguments.Count -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help','-h'))){return @{ok=$true;help='pacing --task ID --project PATH --output NEW_DIRECTORY [--member PID:FILETIME] [--seconds 5] [--presentmon EXE] [--events PROJECT.jsonl]. Explicit PresentMon 2.6 CSV trace; no elevation/fallback. pacing install selects a pinned verified optional console download; pacing cleanup BUNDLE reconciles only its retained unique ETW session after interruption.'}}
    if($Arguments.Count -eq 1 -and $Arguments[0] -eq 'install'){return Install-NativePresentMon}
    if($Arguments.Count -eq 2 -and $Arguments[0] -eq 'cleanup'){
        Assert-NativeDesktopPrivatePath $Arguments[1]
        $manifest=Read-NativeHeldJson (Join-Path $Arguments[1] 'manifest.json') 262144
        if($manifest.kind -cne 'windows-process-pacing' -or $manifest.session_name -cnotmatch '\ABasaltwaterTrace-[0-9a-f]{32}\z'){throw 'Invalid retained tracing session identity'}
        $helper=Get-NativeEvidenceTool 'Capture.Trace'
        $result=Invoke-NativeEvidenceProcess $helper @('--cleanup',$manifest.session_name) 5 $Arguments[1]
        $manifest | Add-Member NoteProperty reconciliation $result.Receipt -Force
        $manifest.cleanup_confirmed=[bool]$result.Receipt.cleanup_confirmed
        Write-NativeEvidenceJson (Join-Path $Arguments[1] 'manifest.json') $manifest 262144
        return @{ok=$manifest.cleanup_confirmed;result=$manifest}
    }
    $options=@{seconds='5'};$seen=@{}
    for($i=0;$i -lt $Arguments.Count;$i++){
        $key=$Arguments[$i];if($key -eq '--json'){continue}
        if($key -notin @('--task','--project','--output','--member','--seconds','--presentmon','--events') -or $seen.ContainsKey($key) -or ++$i -ge $Arguments.Count){throw 'Unknown, duplicate or missing pacing option'}
        $seen[$key]=$true;$options[$key.Substring(2)]=$Arguments[$i]
    }
    foreach($key in @('task','project','output')){if(-not $options.ContainsKey($key) -or -not $options[$key]){throw "Pacing requires --$key"}}
    if($options.seconds -notmatch '\A[1-9][0-9]?\z' -or [int]$options.seconds -gt 30){throw 'Pacing duration must be 1-30 seconds'}
    $selection=Get-DevelopmentEvidenceTarget $options.task $options.project $options['member']
    $presentmon=Get-NativePresentMon $options['presentmon'];$helper=Get-NativeEvidenceTool 'Capture.Trace'
    $directory=New-NativeEvidenceBundle $options.output
    $caller=Get-NativeEvidenceCaller
    $receipt=@{schema_version=1;kind='windows-process-pacing';status='incomplete';session_name=('BasaltwaterTrace-'+[guid]::NewGuid().ToString('N'));task=$options.task;
        target_token=([string]$selection.Target.ProcessId+':'+$selection.Target.CreatedFileTimeUtc);seconds=[int]$options.seconds;started_utc=[DateTime]::UtcNow.ToString('o');
        qpc_anchor=[Diagnostics.Stopwatch]::GetTimestamp();qpc_frequency=[Diagnostics.Stopwatch]::Frequency;clock_scope=(Get-NativeDesktopContext).Session;
        tool_version='2.6.0';tool_sha256=(Get-FileHash -LiteralPath $presentmon).Hash.ToLowerInvariant();helper_sha256=(Get-FileHash -LiteralPath $helper).Hash.ToLowerInvariant();
        cleanup_confirmed=$false;errors=@();summary=$null;project_events=$null}
    # Retain the unique session BEFORE starting ETW; a lost caller can reconcile
    # this exact resource, never stop an unrelated user's PresentMon session.
    Write-NativeEvidenceJson (Join-Path $directory 'manifest.json') $receipt 262144
    try {
        $selection=Get-DevelopmentEvidenceTarget $options.task $options.project $options['member']
        $nativeArguments=@($presentmon,[string]$selection.Target.ProcessId,[string]$selection.Target.CreatedFileTimeUtc,[string]$caller.pid,[string]$caller.filetime,$selection.Record.JobName,$directory,$options.seconds,$receipt.session_name)
        $result=Invoke-NativeEvidenceProcess $helper $nativeArguments ([int]$options.seconds+12) $directory
        $receipt.helper=$result.Receipt;$receipt.cleanup_confirmed=[bool]$result.Receipt.cleanup_confirmed
        if($result.ExitCode -ne 0 -or -not $result.Receipt.complete){throw 'PresentMon failed or tracing cleanup is uncertain; inspect retained logs and required ETW privileges'}
        Initialize-NativeEvidenceFileOwner (Join-Path $directory 'raw.csv')
        $receipt.summary=Get-NativePacingSummary (Join-Path $directory 'raw.csv') ([int]$selection.Target.ProcessId)
        if($options.ContainsKey('events')){$receipt.project_events=Read-NativeProjectEvents $options.events}
        $receipt.status='complete'
    }catch{$receipt.errors+=$_.Exception.Message}
    finally{$receipt.finished_utc=[DateTime]::UtcNow.ToString('o');Write-NativeEvidenceJson (Join-Path $directory 'manifest.json') $receipt 262144}
    return @{ok=($receipt.status -eq 'complete');result=$receipt;output=$directory}
}
