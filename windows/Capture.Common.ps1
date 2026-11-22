<# Opt-in GPU-window capture and private evidence; no input authority or fallback. #>
function Get-NativeEvidenceCaller {
    # The native argv launcher passes its own immutable identity through its
    # fixed bootstrap, rather than a user-controlled environment variable.
    $native=Get-Variable BasaltwaterNativeCaller -Scope Global -ErrorAction SilentlyContinue
    if($native){
        if([string]$native.Value.pid -notmatch '\A[1-9][0-9]{0,9}\z' -or [string]$native.Value.filetime -notmatch '\A[1-9][0-9]{0,18}\z'){throw 'Invalid native evidence caller identity'}
        return @{pid=[int]$native.Value.pid;filetime=[long]$native.Value.filetime}
    }
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try{return @{pid=$PID;filetime=$self.StartTime.ToFileTimeUtc()}}finally{$self.Dispose()}
}
function Initialize-NativeEvidenceFileOwner([string]$Path) {
    # Newly produced evidence may be created by an elevated same-account ETW
    # collector, whose default file owner is Administrators. Keep the private
    # DACL and explicitly publish it to the requesting account's ownership.
    Assert-NativeDesktopPrivatePath (Split-Path -Parent $Path)
    Assert-NativeDesktopPath $Path
    $acl=Get-Acl -LiteralPath $Path
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if($owner -notin @($sid.Value,'S-1-5-32-544')){throw 'Unexpected new evidence file owner'}
    foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($sid.Value,'S-1-5-18','S-1-5-32-544')){throw 'Unsafe new evidence file ACL'}
    }
    if($owner -ne $sid.Value){
        $acl.SetOwner($sid);Set-Acl -LiteralPath $Path -AclObject $acl
        Assert-NativeDesktopPrivatePath $Path
    }
}
function Write-NativeEvidenceJson([string]$Path,$Record,[int]$Limit=65536) {
    Write-NativeHeldJson $Path $Record $Limit
}
function Get-NativeEvidenceTool([ValidateSet('Capture.Window','Capture.Trace','Audio.Capture')][string]$Name) {
    $source = Join-Path $PSScriptRoot ($Name + '.cpp')
    $digest = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $root = Join-Path $env:LOCALAPPDATA 'Basaltwater\native-evidence-tools'
    New-NativeDesktopPrivateDirectory $root
    $buildContract = 3 # Static CRT, explicit file ownership, fixed build contract.
    $directory = Join-Path $root ($Name + '-v' + $buildContract + '-' + $digest)
    New-NativeDesktopPrivateDirectory $directory
    $executable = Join-Path $directory ($Name + '.exe')
    $lease = [IO.File]::Open((Join-Path $directory 'build.lock'),'OpenOrCreate','ReadWrite','None')
    try {
        if (-not (Test-Path -LiteralPath $executable)) {
            . (Join-Path $PSScriptRoot 'GameDevelopment.Common.ps1')
            Enter-NativeGameDevEnvironment *> (Join-Path $directory 'environment-build.log')
            $temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.exe')
            $log = Join-Path $directory 'build.log'
            & cl.exe /nologo /O2 /EHsc /MT /std:c++17 $source ('/Fo'+(Join-Path $directory 'build.obj')) ('/Fe'+$temporary) /link runtimeobject.lib ole32.lib oleaut32.lib d3d11.lib dxgi.lib dwmapi.lib windowscodecs.lib user32.lib advapi32.lib mmdevapi.lib *> $log
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporary)) { throw "Native evidence helper build failed; inspect $log" }
            Initialize-NativeEvidenceFileOwner $temporary
            [IO.File]::Move($temporary,$executable)
            Write-NativeEvidenceJson (Join-Path $directory 'build.json') @{schema_version=1;build_contract=$buildContract;source_sha256=$digest;executable_sha256=(Get-FileHash -LiteralPath $executable).Hash.ToLowerInvariant();compiler=(Get-Command cl.exe).Source;built_utc=[DateTime]::UtcNow.ToString('o')}
        }
        Assert-NativeDesktopPrivatePath $executable
        $receipt = Read-NativeHeldJson (Join-Path $directory 'build.json')
        if ($receipt.build_contract -ne $buildContract -or $receipt.source_sha256 -cne $digest -or $receipt.executable_sha256 -cne (Get-FileHash -LiteralPath $executable).Hash.ToLowerInvariant()) { throw 'Native evidence helper changed; inspect the private build before reuse' }
        return $executable
    } finally { $lease.Dispose() }
}
function ConvertTo-NativeEvidenceArgument([string]$Value) {
    if ($null -eq $Value -or $Value.Contains([string][char]0)) { throw 'Invalid evidence helper argument' }
    # CommandLineToArgvW quoting, including quotes and trailing backslashes.
    return '"' + ([regex]::Replace($Value,'(\\*)"','$1$1\"') -replace '(\\+)$','$1$1') + '"'
}
function Invoke-NativeEvidenceProcess([string]$Executable,[string[]]$Arguments,[int]$TimeoutSeconds,[string]$Directory) {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$Executable; $start.Arguments=(@($Arguments | ForEach-Object {ConvertTo-NativeEvidenceArgument $_}) -join ' ')
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true; $start.WindowStyle='Hidden'
    # Fixed-protocol owned helpers return one bounded receipt; diagnostics go to
    # private files. They pin this caller and stop themselves if it disappears.
    $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process; $process.StartInfo=$start; $started=$false
    try {
        $started=$process.Start()
        if (-not $started) { throw 'Evidence helper did not start' }
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds*1000)) { throw 'Evidence helper exceeded its deadline; inspect retained output; no replay' }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { throw 'Evidence helper output cleanup unconfirmed' }
        $out=$stdout.GetAwaiter().GetResult(); $err=$stderr.GetAwaiter().GetResult()
        if ($out.Length -gt 32768 -or $err.Length -gt 32768) { throw 'Evidence helper response exceeded its fixed-protocol bound' }
        [IO.File]::WriteAllText((Join-Path $Directory 'helper.stdout.log'),$out)
        [IO.File]::WriteAllText((Join-Path $Directory 'helper.stderr.log'),$err)
        $result=$out | ConvertFrom-Json
        if ($null -eq $result -or -not $result.PSObject.Properties['schema_version'] -or $result.schema_version -ne 1) { throw 'Invalid evidence helper receipt' }
        return @{ExitCode=$process.ExitCode;Receipt=$result}
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            if (-not $process.WaitForExit(5000)) { throw 'Owned evidence helper cleanup unconfirmed' }
        }
        $process.Dispose()
    }
}
function Read-NativeProjectEvents([string]$Path) {
    if (-not $Path) { return @{status='not-selected';events=@();errors=@()} }
    Assert-NativeDesktopPath $Path
    $stream=[IO.File]::Open($Path,'Open','Read',([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        if ($stream.Length -gt 128KB) { throw 'Project event journal exceeds 128 KiB' }
        $bytes=New-Object byte[] (128KB+1); $used=0
        while($used -lt $bytes.Length){$n=$stream.Read($bytes,$used,$bytes.Length-$used);if($n -eq 0){break};$used+=$n}
        if($used -gt 128KB){throw 'Project event journal grew beyond its limit'}
        $text=(New-Object Text.UTF8Encoding($false,$true)).GetString($bytes,0,$used)
        if ($text -and -not $text.EndsWith("`n")) { throw 'Project event journal ends in an incomplete record' }
        $events=@(); $last=-1L
        foreach($line in @($text -split "`n" | Where-Object {$_ -ne ''})) {
            if ($events.Count -ge 1024 -or $line.Length -gt 4096) { throw 'Project event count/line bound exceeded' }
            $event=$line | ConvertFrom-Json
            if (@($event.PSObject.Properties.Name | Where-Object {$_ -notin @('schema_version','sequence','utc','qpc','qpc_frequency','event','phase')}).Count -or
                $event.schema_version -ne 1 -or [string]$event.sequence -notmatch '\A[0-9]{1,10}\z' -or [long]$event.sequence -le $last -or
                [string]$event.qpc -notmatch '\A[1-9][0-9]{0,18}\z' -or [string]$event.qpc_frequency -notmatch '\A[1-9][0-9]{0,10}\z' -or
                [string]$event.event -notmatch '\A[A-Za-z0-9_.-]{1,64}\z') { throw 'Invalid project event contract' }
            $null=[DateTimeOffset]::Parse($event.utc,[Globalization.CultureInfo]::InvariantCulture)
            if ($event.PSObject.Properties['phase'] -and ([string]$event.phase).Length -gt 128) { throw 'Project event phase exceeds bound' }
            $last=[long]$event.sequence; $events+=,$event
        }
        return @{status='observed';events=$events;errors=@();loss_accounting='Sequence gaps retained; producer and clock provenance remain project-owned'}
    } finally { $stream.Dispose() }
}
function New-NativeEvidenceBundle([string]$Path) {
    Assert-NativeDesktopPath $Path
    $resolved=[IO.Path]::GetFullPath($Path)
    if((Test-Path -LiteralPath $resolved) -or -not(Test-Path -LiteralPath (Split-Path -Parent $resolved) -PathType Container)){throw 'Evidence output must be a new directory under an existing parent'}
    if((Get-PSDrive -Name $resolved.Substring(0,1)).Free -lt 128MB){throw 'Evidence capture needs at least 128 MiB free space'}
    New-NativeDesktopPrivateDirectory $resolved
    # CreateNew closes the concurrent-create race without replacing another run's
    # manifest or PNGs. Retain the claim as evidence even on failure/caller loss.
    $claim=[IO.File]::Open((Join-Path $resolved 'claim'),'CreateNew','Write','None')
    $claim.Dispose()
    return $resolved
}
function Invoke-NativeWindowCapture([string[]]$Arguments) {
    if (-not $Arguments.Count -or ($Arguments.Count -eq 1 -and $Arguments[0] -in @('--help','-h'))) {
        return @{ok=$true;help='capture --window HWND --identity TOKEN --generation GEN --width W --height H --output NEW_DIRECTORY [--milliseconds 2000] [--frames 3] [--interval-ms 250] [--events PROJECT.jsonl]. Explicit Windows Graphics Capture, no PrintWindow fallback. Bounded PNG frames and QPC/UTC evidence; no observation authority. C++/Windows SDK required for first helper build; no automatic installation.'}
    }
    $options=@{milliseconds='2000';frames='3';'interval-ms'='250'}; $seen=@{}
    for($i=0;$i -lt $Arguments.Count;$i++){
        $key=$Arguments[$i]
        if($key -eq '--json'){continue}
        if($key -notin @('--window','--identity','--generation','--width','--height','--output','--milliseconds','--frames','--interval-ms','--events') -or $seen.ContainsKey($key) -or ++$i -ge $Arguments.Count){throw 'Unknown, duplicate or missing capture option'}
        $seen[$key]=$true;$options[$key.Substring(2)]=$Arguments[$i]
    }
    foreach($name in @('window','identity','generation','width','height','output')){if(-not $options.ContainsKey($name) -or -not $options[$name]){throw "Capture requires --$name"}}
    foreach($name in @('window','width','height','milliseconds','frames','interval-ms')){if($options[$name] -notmatch '\A[1-9][0-9]{0,17}\z'){throw "Invalid capture integer: $name"}}
    if([long]$options.window -le 0 -or [long]$options.width -gt 16384 -or [long]$options.height -gt 16384 -or
        [long]$options.width*[long]$options.height -gt 8388608 -or [long]$options.milliseconds -lt 100 -or [long]$options.milliseconds -gt 10000 -or
        [long]$options.frames -gt 120 -or [long]$options['interval-ms'] -lt 16 -or [long]$options['interval-ms'] -gt 10000){throw 'Capture bounds: 100-10000 ms, 1-120 frames, interval 16-10000 ms, eight megapixels'}
    # Reuse the exact saved inventory identity; observing pixels does not require
    # enabling control and must not invalidate a concurrent lease's observations.
    $context=Get-NativeDesktopContext; $state=Read-NativeDesktopState $context
    $matches=@($state.windows | Where-Object { $_.id -eq [long]$options.window -and $_.identity -ceq $options.identity })
    $age=(Get-NativeDesktopTime)-[long]$state.window_observed
    if($state.generation -cne $options.generation -or $matches.Count -ne 1 -or $age -lt 0 -or $age -gt 60 -or
        $matches[0].width -ne [int]$options.width -or $matches[0].height -ne [int]$options.height){throw 'Capture requires a fresh matching window inventory and geometry'}
    $parts=$options.identity.Split(':')
    if($parts.Count -lt 3 -or $parts[0] -notmatch '\A[1-9][0-9]*\z' -or $parts[1] -notmatch '\A[1-9][0-9]{0,18}\z'){throw 'Unsupported window identity'}
    $tool=Get-NativeEvidenceTool 'Capture.Window'
    $directory=New-NativeEvidenceBundle $options.output
    $caller=Get-NativeEvidenceCaller
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $receipt=@{schema_version=1;kind='windows-gpu-window-capture';status='incomplete';backend='windows-graphics-capture';started_utc=[DateTime]::UtcNow.ToString('o');qpc_anchor=[Diagnostics.Stopwatch]::GetTimestamp();qpc_frequency=[Diagnostics.Stopwatch]::Frequency;session=$context.Session;
        target=@{window=[long]$options.window;identity=$options.identity};frames=@();errors=@();helper_sha256=(Get-FileHash -LiteralPath $tool).Hash.ToLowerInvariant();source_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'Capture.Window.cpp')).Hash.ToLowerInvariant();
        limits=@{milliseconds=[int]$options.milliseconds;frames=[int]$options.frames;interval_ms=[int]$options['interval-ms'];pixels=8388608;png_bytes=16MB;bundle_bytes=64MB};pixel_format='BGRA8';color='SDR output; HDR fidelity unqualified';hdr=$null;observation_id=$null;source_frame_loss=$null;project_events=$null;cleanup_confirmed=$false}
    Write-NativeEvidenceJson (Join-Path $directory 'manifest.json') $receipt 262144
    try {
        $nativeArgs=@($options.window,$parts[0],([long]$parts[1]-504911232000000000L).ToString(),$caller.pid.ToString(),$caller.filetime.ToString(),$directory,$options.milliseconds,$options.frames,$options['interval-ms'],$options.width,$options.height)
        $result=Invoke-NativeEvidenceProcess $tool $nativeArgs 15 $directory
        $receipt.cleanup_confirmed=$true; $receipt.helper=$result.Receipt
        $path=Join-Path $directory 'frames.jsonl'
        if(Test-Path -LiteralPath $path){
            Initialize-NativeEvidenceFileOwner $path
            if((Get-Item -LiteralPath $path).Length -gt 128KB){throw 'Frame journal exceeds bound'}
            foreach($line in Get-Content -LiteralPath $path){
                if($receipt.frames.Count -ge 120){throw 'Frame count exceeds bound'}
                $frame=$line | ConvertFrom-Json
                if($frame.sequence -ne $receipt.frames.Count -or $frame.artifact -cne ('frame-{0:D4}.png' -f $receipt.frames.Count)){throw 'Invalid frame journal identity'}
                $image=Join-Path $directory $frame.artifact;Initialize-NativeEvidenceFileOwner $image
                $frame | Add-Member NoteProperty sha256 (Get-FileHash -LiteralPath $image).Hash.ToLowerInvariant()
                $receipt.frames+=,$frame
            }
        }
        if($options.ContainsKey('events')){$receipt.project_events=Read-NativeProjectEvents $options.events}
        if($result.ExitCode -ne 0 -or -not $result.Receipt.complete -or $receipt.frames.Count -eq 0 -or $result.Receipt.frames -ne $receipt.frames.Count){throw 'GPU capture incomplete; inspect helper and retained frames'}
        $receipt.status='complete'
    } catch {$receipt.errors+= $_.Exception.Message}
    finally {
        $receipt.elapsed_seconds=$clock.Elapsed.TotalSeconds;$receipt.finished_utc=[DateTime]::UtcNow.ToString('o')
        Write-NativeEvidenceJson (Join-Path $directory 'manifest.json') $receipt 262144
    }
    return @{ok=($receipt.status -eq 'complete');result=$receipt;output=$directory}
}
