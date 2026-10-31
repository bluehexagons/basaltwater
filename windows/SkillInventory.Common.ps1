<# Shared native inventory reader and read-only bundle comparison. No provider execution. #>
function Read-NativeSkillInventory([string]$Path) {
    Assert-NativeSkillPath $Path
    $files = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Present = $false; Complete = $null; Files = $files }
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-Item -LiteralPath $Path).Length -gt 1MB) {
        throw "Invalid skill inventory: $Path"
    }
    $inventory = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $inventory -or -not $inventory.PSObject.Properties['Version'] -or
        ($inventory.Version -isnot [int] -and $inventory.Version -isnot [long]) -or
        $inventory.Version -ne 1) { throw "Unsupported skill inventory: $Path" }
    if (-not $inventory.PSObject.Properties['Files'] -or $inventory.Files -isnot [pscustomobject] -or
        ($inventory.PSObject.Properties['Complete'] -and $inventory.Complete -isnot [bool])) {
        throw "Invalid skill inventory: $Path"
    }
    foreach ($property in $inventory.Files.PSObject.Properties) {
        if ($property.Name -notmatch '\A[A-Za-z0-9_.\\-]+\z' -or
            @($property.Name -split '\\' | Where-Object {
                $_ -in @('', '.', '..', '.basaltwater-windows-files.json') -or $_ -match '\.\z|\A(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|\z)'
            }).Count -or
            ($property.Value -isnot [string] -and $property.Value -isnot [array]) -or
            @($property.Value).Count -eq 0 -or
            @($property.Value | Where-Object { $_ -isnot [string] -or $_ -notmatch '\A[0-9A-Fa-f]{64}\z' }).Count) {
            throw "Invalid skill inventory: $Path"
        }
        $files[$property.Name] = @($property.Value)
    }
    return [pscustomobject]@{ Present = $true; Files = $files;
        Complete = $(if ($inventory.PSObject.Properties['Complete']) { $inventory.Complete } else { $null }) }
}

function Assert-NativeSkillAccess([string]$Path) {
    Assert-NativeSkillPath $Path
    # Check only named bundle resources and their ancestors, never enumerate a
    # personal catalog. Native ACLs, not POSIX modes, establish write authority.
    $trusted = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        'S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $acl = Get-Acl -LiteralPath $cursor
            if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw "Unsafe skill owner: $cursor" }
            foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
                # Creating a sibling at a drive/profile ancestor does not grant
                # replacement of this existing path. Deletion/ACL rights do.
                $writes = if ($cursor -eq [IO.Path]::GetFullPath($Path)) {
                    [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
                } else { [Security.AccessControl.FileSystemRights]'Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership' }
                if ($rule.AccessControlType -eq 'Allow' -and
                    -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
                    $rule.IdentityReference.Value -notin $trusted -and
                    ($rule.FileSystemRights -band $writes)) {
                    throw "Unsafe skill write access: $cursor"
                }
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
}

function Get-NativeSkillComparison([string]$Source, [string]$Destination) {
    try {
        Assert-NativeSkillAccess $Destination
        Assert-NativeSkillAccess $Source
        $inventoryPath = Join-Path $Destination '.basaltwater-windows-files.json'
        Assert-NativeSkillAccess $inventoryPath
        $inventory = Read-NativeSkillInventory $inventoryPath
        $entry = Join-Path $Source 'SKILL.md'
        Assert-NativeSkillAccess $entry
        if (-not (Test-Path -LiteralPath $entry -PathType Leaf) -or
            (Get-Content -LiteralPath $entry -Raw) -notmatch 'managed-by: basaltwater') { throw 'Invalid managed skill source' }
        $expected = @{}
        foreach ($item in Get-ChildItem -LiteralPath $Source -Recurse -Force) {
            $relative = $item.FullName.Substring($Source.TrimEnd('\').Length + 1)
            if ($relative -match '(?:\A|\\)__pycache__(?:\\|\z)' -or $item.Extension -in @('.pyc','.pyo')) { continue }
            Assert-NativeSkillAccess $item.FullName
            if (-not $item.PSIsContainer) { $expected[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash }
        }
        $missing = $false; $different = $false; $personal = $false
        foreach ($relative in @(@($expected.Keys) + @($inventory.Files.Keys) | Select-Object -Unique)) {
            $path = Join-Path $Destination $relative
            Assert-NativeSkillAccess $path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                if (Test-Path -LiteralPath $path) { throw 'Expected skill file is a directory' }
                if ($expected.ContainsKey($relative)) { $missing = $true }
                continue
            }
            $digest = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
            if (-not $inventory.Files.ContainsKey($relative) -or $digest -notin $inventory.Files[$relative]) { $personal = $true }
            if (-not $expected.ContainsKey($relative) -or $digest -ne $expected[$relative]) { $different = $true }
        }
        if (-not $inventory.Present -and (Test-Path -LiteralPath $Destination)) { $personal = $true }
        $state = if ($personal) { 'personal' } elseif ($inventory.Complete -ne $true -or $missing) { 'incomplete' } elseif ($different) { 'different' } else { 'current' }
        return [pscustomobject]@{ State = $state; Present = (Test-Path -LiteralPath $Destination);
            Completion = $inventory.Complete; Detail = $(if ($null -eq $inventory.Complete) { 'Completion unknown (legacy or absent inventory).' } else { 'Compared known entrypoint and supporting resources.' }) }
    } catch { return [pscustomobject]@{ State = 'unsafe'; Present = $null; Completion = $null; Detail = $_.Exception.Message } }
}

function Get-NativeSkillHealth($Saved) {
    Assert-NativeSkillAccess $Saved.Source
    Assert-NativeSkillAccess (Join-Path $Saved.Source 'windows\agent-skills.json')
    $names = @(Get-NativeAgentSkillNames $Saved.Source)
    $checks = @(foreach ($catalog in @('.agents\skills', '.claude\skills')) {
        $required = $catalog -eq '.agents\skills' -or ($Saved.PSObject.Properties['AgentTool'] -and 'claude' -in $Saved.AgentTool)
        foreach ($name in $names) {
            $destination = Join-Path $env:USERPROFILE "$catalog\$name"
            $result = Get-NativeSkillComparison (Join-Path $Saved.Source "common\agent_skills\$name") $destination
            [pscustomobject]@{ Name = $name; Catalog = $catalog; Required = [bool]$required; Path = $destination;
                State = $result.State; Present = $result.Present; Completion = $result.Completion; Detail = $result.Detail }
        }
    })
    [pscustomobject]@{ Schema = 1; Platform = 'windows'; Source = $Saved.Source; Revision = $Saved.Revision;
        SessionLoaded = 'unknown'; Healthy = @($checks | Where-Object {
            $_.State -ne 'current' -and ($_.Required -or $_.Present -ne $false)
        }).Count -eq 0; Checks = $checks }
}
