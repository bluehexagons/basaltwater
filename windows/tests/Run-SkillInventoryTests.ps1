<# Native bundle transactions and read-only freshness; all catalogs are disposable. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_ }
    if (-not $caught -or $caught.Exception.Message -notmatch $Pattern) { throw "Expected $Pattern; received $caught" }
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $repo 'windows\AgentWorkstation.Common.ps1')
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('basaltwater-skills-' + [guid]::NewGuid().ToString('N'))
$savedProfile = $env:USERPROFILE
$writer = ${function:Write-NativeSkillInventory}
try {
    $env:USERPROFILE = Join-Path $temporary 'account'
    $sourceRoot = Join-Path $temporary 'source'
    $source = Join-Path $sourceRoot 'common\agent_skills\basaltwater-example'
    New-Item -ItemType Directory -Path $source, (Join-Path $sourceRoot 'windows') -Force | Out-Null
    Set-Content (Join-Path $sourceRoot 'windows\agent-skills.json') '{"version":1,"skills":["basaltwater-example"]}'
    Set-Content (Join-Path $source 'SKILL.md') "metadata:`n  managed-by: basaltwater"
    Set-Content (Join-Path $source 'guide.md') 'v1'
    $saved = [pscustomobject]@{ Source = $sourceRoot; Revision = 'a' * 40; AgentTool = @('codex') }
    $report = Get-NativeSkillHealth $saved
    Assert-True (-not $report.Healthy -and $report.Checks.Count -eq 2) 'Missing required catalog passed'
    foreach ($catalog in @('.agents\skills','.claude\skills')) {
        $destination = Join-Path $env:USERPROFILE "$catalog\basaltwater-example"
        $inventory = Join-Path $destination '.basaltwater-windows-files.json'
        Install-NativeSkill $source $destination
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'current') 'Clean install was not current'
        $before = (Get-FileHash $inventory).Hash
        Install-NativeSkill $source $destination
        Assert-True ((Get-FileHash $inventory).Hash -eq $before) 'Idempotent inventory changed'
        foreach ($name in @('.basaltwater-windows-files.json', 'unrecordable name.md')) {
            $invalidSource = Join-Path $source $name
            Set-Content -LiteralPath $invalidSource -Value '{}'
            try {
                $freshDestination = Join-Path $env:USERPROFILE "$catalog\fresh-invalid"
                Assert-Throws { Install-NativeSkill $source $freshDestination } 'Invalid skill inventory resource path'
                Assert-True (-not (Test-Path -LiteralPath $freshDestination)) 'Invalid source created a fresh catalog'
                Assert-Throws { Install-NativeSkill $source $destination -Plan } 'Invalid skill inventory resource path'
                Assert-Throws { Install-NativeSkill $source $destination } 'Invalid skill inventory resource path'
                Assert-True ((Get-FileHash $inventory).Hash -eq $before) 'Invalid source changed the destination checkpoint'
                Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'unsafe') 'Unrecordable source resource passed comparison'
            } finally { Remove-Item -LiteralPath $invalidSource }
        }
        $record = Get-Content $inventory -Raw | ConvertFrom-Json
        $record.PSObject.Properties.Remove('Complete')
        $record | ConvertTo-Json -Depth 5 | Set-Content $inventory
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'incomplete') 'Legacy inventory claimed verified completion'
        Install-NativeSkill $source $destination

        # Fail after final file delivery/final inventory, before completion.
        function Write-NativeSkillInventory([string]$path, [hashtable]$files, [switch]$Complete) {
            if ($Complete) { throw 'completion interruption' }
            & $writer $path $files
        }
        Assert-Throws { Install-NativeSkill $source $destination } 'completion interruption'
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'incomplete') 'Matching bytes promoted unfinished delivery'
        Set-Item Function:\Write-NativeSkillInventory $writer
        Install-NativeSkill $source $destination
        Set-Content (Join-Path $source 'guide.md') 'v2'
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'different') 'Managed old reference was not different'
        & {
            function Copy-Item($LiteralPath, $Destination, [switch]$Force) { throw 'file interruption' }
            Assert-Throws { Install-NativeSkill $source $destination } 'file interruption'
        }
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'incomplete') 'Copy interruption lost completion state'
        Install-NativeSkill $source $destination
        Remove-Item (Join-Path $destination 'guide.md')
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'incomplete') 'Missing supporting file passed'
        Install-NativeSkill $source $destination
        Set-Content (Join-Path $destination 'guide.md') 'personal edit'
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'personal') 'Edited supporting file was not personal'
        Assert-Throws { Install-NativeSkill $source $destination } 'personal or modified'
        Set-Content (Join-Path $destination 'guide.md') 'v2'
        # Legacy tracked bytecode is retired only when it still matches.
        Set-Content (Join-Path $destination 'old.pyc') 'unchanged cache'
        Set-Content (Join-Path $destination 'edited.pyo') 'old cache'
        $record = Get-Content $inventory -Raw | ConvertFrom-Json
        foreach ($name in @('old.pyc','edited.pyo')) {
            $record.Files | Add-Member NoteProperty $name @((Get-FileHash (Join-Path $destination $name)).Hash)
        }
        $record | ConvertTo-Json -Depth 5 | Set-Content $inventory
        Set-Content (Join-Path $destination 'edited.pyo') 'personal cache'
        New-Item -ItemType Directory -Path (Join-Path $source '__pycache__') -Force | Out-Null
        Set-Content (Join-Path $source '__pycache__\cache.pyc') 'generated'
        Install-NativeSkill $source $destination
        Assert-True (-not (Test-Path (Join-Path $destination 'old.pyc')) -and
            (Get-Content (Join-Path $destination 'edited.pyo')) -eq 'personal cache') 'Stale bytecode cleanup violated ownership'
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'current') 'Generated caches affected freshness'
        $valid = Get-Content $inventory -Raw
        foreach ($bad in @('{', '{"Version":2,"Files":{}}', '{"Version":true,"Files":{}}',
            '{"Version":1,"Files":[]}', '{"Version":1,"Files":null}',
            '{"Version":1,"Complete":"true","Files":{}}',
            '{"Version":1,"Files":{"..\\outside":["bad"]}}',
            '{"Version":1,"Files":{"CON":["bad"]}}', '{"Version":1,"Files":{"guide.md":[null]}}')) {
            Set-Content $inventory $bad
            Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'unsafe') 'Malformed inventory was trusted'
            Assert-Throws { Install-NativeSkill $source $destination } 'inventory|JSON|Invalid'
        }
        Set-Content $inventory $valid
        $before = (Get-FileHash $inventory).Hash
        $null = Get-NativeSkillComparison $source $destination
        Assert-True ((Get-FileHash $inventory).Hash -eq $before) 'Diagnostic wrote inventory'
        $acl = Get-Acl $destination
        $unsafeAcl = Get-Acl $destination
        $everyone = [Security.Principal.SecurityIdentifier]::new('S-1-1-0')
        $unsafeAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($everyone, 'Write', 'Allow'))
        try {
            Set-Acl $destination $unsafeAcl
            Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'unsafe') 'Broad write ACL was trusted'
        } finally { Set-Acl $destination $acl }
        Remove-Item $inventory
        Assert-True ((Get-NativeSkillComparison $source $destination).State -eq 'personal') 'Uninventoried bundle was not personal'
        # Equal unowned bytes remain adoptable by explicit setup, as before.
        Install-NativeSkill $source $destination
        Set-Content (Join-Path $source 'guide.md') 'v1'
        Install-NativeSkill $source $destination
    }
    # Missing optional Claude catalog is informational, not a host failure.
    $claude = Join-Path $env:USERPROFILE '.claude'
    $resolved = [IO.Path]::GetFullPath($claude)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath($temporary) + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture deletion' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
    $report = Get-NativeSkillHealth $saved
    Assert-True ($report.Healthy -and $report.Revision -eq $saved.Revision -and $report.SessionLoaded -eq 'unknown') 'Optional catalog or provenance was wrong'
    $junction = Join-Path $temporary 'junction'
    New-Item -ItemType Junction -Path $junction -Target $source | Out-Null
    try { Assert-True ((Get-NativeSkillComparison $source $junction).State -eq 'unsafe') 'Junction was trusted' }
    finally { [IO.Directory]::Delete($junction) }
    foreach ($path in @('relative', 'C:\file:stream', '\\server\share\skill')) {
        Assert-Throws { Assert-NativeSkillPath $path } 'absolute local'
    }
    Write-Host 'Native skill inventory/freshness tests passed.'
} finally {
    Set-Item Function:\Write-NativeSkillInventory $writer
    $env:USERPROFILE = $savedProfile
    $resolved = [IO.Path]::GetFullPath($temporary)
    $root = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe skill test cleanup' }
    if (Test-Path $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
