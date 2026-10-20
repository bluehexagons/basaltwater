<# Manual CI checks for Windows PowerShell 5.1 and native artifact jobs. #>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-True([bool]$condition, [string]$message) {
    if (-not $condition) { throw $message }
}

function Assert-Throws([scriptblock]$action, [string]$pattern) {
    $caught = $null
    try { & $action | Out-Null } catch { $caught = $_ }
    if (-not $caught) { throw "Expected failure matching: $pattern" }
    if ($caught.Exception.Message -notmatch $pattern) {
        throw "Expected '$pattern'; received: $($caught.Exception.Message)"
    }
}

function Get-Receipts {
    $directory = Join-Path $env:LOCALAPPDATA 'Basaltwater\jobs'
    if (Test-Path -LiteralPath $directory) {
        Get-ChildItem -LiteralPath $directory -Filter receipt.json -Recurse
    }
}

function Assert-NewReceipt([int]$before, [string]$state) {
    $receipts = @(Get-Receipts)
    Assert-True ($receipts.Count -eq $before + 1) "Expected one new job receipt"
    $latest = $receipts | Sort-Object CreationTimeUtc -Descending | Select-Object -First 1
    $record = Get-Content -LiteralPath $latest.FullName -Raw | ConvertFrom-Json
    Assert-True ($record.State -eq $state) "Expected receipt state '$state', got '$($record.State)'"
    return $record
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$temporary = Join-Path $env:RUNNER_TEMP ('basaltwater-windows-' + [guid]::NewGuid().ToString('N'))
$checkout = Join-Path $temporary 'checkout with spaces'
$credential = Join-Path $temporary 'token.dpapi'
$originalLocalAppData = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $temporary -Force | Out-Null

try {
    foreach ($file in @('install.ps1', 'windows\Invoke-BasaltwaterJob.ps1',
                        'windows\Manage-Wsl.ps1', 'windows\Save-BasaltwaterToken.ps1',
                        'windows\Start-WslWorker.ps1', 'windows\examples\Smoke-Artifact.ps1')) {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $repo $file), [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) "PowerShell syntax errors in $file`: $errors"
    }

    $env:LOCALAPPDATA = Join-Path $temporary 'profile'
    New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force | Out-Null

    # Exercise bootstrap helpers without enabling WSL or changing a real distro.
    $tokens = $null
    $errors = $null
    $installerAst = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repo 'install.ps1'), [ref]$tokens, [ref]$errors)
    foreach ($name in @('Invoke-Checked', 'Select-Distro', 'Ensure-UbuntuPython',
                        'Initialize-DistroSelection', 'Ensure-Distro', 'Ensure-UbuntuConfiguration')) {
        $definition = $installerAst.Find({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
        }, $true)
        Assert-True ($null -ne $definition) "Missing installer helper: $name"
        Invoke-Expression $definition.Extent.Text
    }
    function Save-State($value) { $script:CapturedDistro = $value.Distro }
    function global:wsl.exe {
        param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
        $global:LASTEXITCODE = 0
        $global:BootstrapCalls += ,$Arguments
        if (($Arguments -join ' ') -eq '--list --online') {
            $catalog = "NAME FRIENDLY NAME`nUbuntu-24.04 Ubuntu 24.04 LTS`nUbuntu-26.04 Ubuntu 26.04 LTS`nUbuntu-26.10-Preview Ubuntu preview`n"
            return ($catalog.ToCharArray() -join ([string][char]0))
        }
        Assert-True (($Arguments -join ' ') -match '--user root --exec /usr/bin/') 'Bootstrap must use Ubuntu root/system executables'
        if ($Arguments -contains '/usr/bin/python3') {
            $global:LASTEXITCODE = if (-not $global:BootstrapPythonInstalled) { 127 } elseif ($global:BootstrapOldPython) { 2 } else { 0 }
            return
        }
        if ($Arguments -contains 'install') { $global:BootstrapPythonInstalled = $true }
        if ($global:BootstrapPackageFailure) { $global:LASTEXITCODE = 17 }
    }
    try {
        $state = [pscustomobject]@{ Distro = '' }
        $global:BootstrapCalls = @()
        $global:BootstrapPythonInstalled = $false
        $global:BootstrapOldPython = $false
        $global:BootstrapPackageFailure = $false
        Select-Distro
        Assert-True ($state.Distro -eq 'Ubuntu-26.04') 'Stable selection failed with null-padded catalog/preview name'
        Assert-True ($CapturedDistro -eq $state.Distro) 'Selected distro was not saved'
        $global:BootstrapCalls = @()
        Ensure-UbuntuPython
        Assert-True ($global:BootstrapPythonInstalled) 'Fresh bootstrap did not install missing Python'
        Assert-True ($global:BootstrapCalls.Count -eq 4) 'Missing Python should trigger probe, apt update/install, and verification'
        $global:BootstrapCalls = @()
        Ensure-UbuntuPython
        Assert-True ($global:BootstrapCalls.Count -eq 1) 'Existing Python should skip apt bootstrap'
        $global:BootstrapCalls = @()
        $global:BootstrapOldPython = $true
        Assert-Throws { Ensure-UbuntuPython } 'Python 3.11 or newer'
        Assert-True ($global:BootstrapCalls.Count -eq 1) 'Unsupported Python must stop before apt or source preparation'
        $global:BootstrapOldPython = $false
        $global:BootstrapPythonInstalled = $false
        $global:BootstrapPackageFailure = $true
        Assert-Throws { Ensure-UbuntuPython } 'package index failed'
    } finally {
        Remove-Item Function:\wsl.exe
        Remove-Item Function:\Save-State
        Remove-Item Function:\Select-Distro
        Remove-Item Function:\Ensure-UbuntuPython
        Remove-Variable BootstrapCalls, BootstrapPythonInstalled, BootstrapOldPython, BootstrapPackageFailure -Scope Global -ErrorAction SilentlyContinue
    }

    # Recover state using serialized snapshots, with every WSL operation mocked.
    function Save-State($value) {
        if ($script:InterruptOwnershipSave -and $value.DistroOwned) {
            throw 'simulated interrupted ownership save'
        }
        $script:SavedSetup = $value | ConvertTo-Json
    }
    function global:wsl.exe {
        param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
        $global:LASTEXITCODE = 0
        $script:RecoveryCalls += ,$Arguments
        switch ($Arguments[0]) {
            '--list' {
                if ($Arguments -contains '--quiet') {
                    if ($script:DistroInstalled) { return 'Ubuntu-26.04' }
                } else { return 'Ubuntu-26.04 Ubuntu 26.04 LTS' }
                return
            }
            '--install' { $script:DistroInstalled = $true; return }
            '--terminate' {
                if ($script:RestartFailure) { throw 'simulated interrupted restart' }
                Assert-True ($Arguments[1] -eq $state.Distro) 'Restart targeted another distro'
                return
            }
        }
        if ($Arguments -contains 'configure') {
            $saved = $script:SavedSetup | ConvertFrom-Json
            Assert-True $saved.RestartRequired 'Restart intent was not saved before configure'
            return $script:ConfigureResult
        }
        if ($Arguments -contains '/proc/1/comm') { return $script:PidOne }
        throw "Unexpected WSL recovery operation: $Arguments"
    }
    try {
        $DistroName = ''
        $script:InterruptOwnershipSave = $false
        $state = [pscustomobject]@{ Distro = 'Ubuntu-26.04'; LinuxUser = 'agent'; DistroLocation = '' }
        $script:RecoveryCalls = @()
        $script:DistroInstalled = $true
        Initialize-DistroSelection
        Assert-Throws { Ensure-Distro } 'not explicitly selected'
        Assert-True ($script:RecoveryCalls.Count -eq 1) 'Legacy state mutated an unconfirmed distro'
        $DistroName = 'Ubuntu-24.04'
        Assert-Throws { Initialize-DistroSelection } 'must match'
        $DistroName = $state.Distro
        Initialize-DistroSelection
        Ensure-Distro
        Assert-True ($state.DistroSelection -eq 'explicit') 'Explicit legacy recovery was not saved'

        $DistroName = ''
        $state.DistroSelection = 'automatic'
        Assert-Throws { Ensure-Distro } 'not explicitly selected'
        $script:DistroInstalled = $false
        Save-State $state
        $script:InterruptOwnershipSave = $true
        Assert-Throws { Ensure-Distro } 'interrupted ownership save'
        $state = $script:SavedSetup | ConvertFrom-Json
        Assert-Throws { Ensure-Distro } 'not explicitly selected'
        $script:InterruptOwnershipSave = $false
        $script:DistroInstalled = $false
        Ensure-Distro
        $state = $script:SavedSetup | ConvertFrom-Json
        Assert-True $state.DistroOwned 'Successful installation did not persist ownership'
        $script:RecoveryCalls = @()
        Ensure-Distro
        Assert-True ($script:RecoveryCalls.Count -eq 1) 'Owned distro was installed again on resume'

        $script:ConfigureResult = 'CHANGED'
        $script:PidOne = 'init'
        $script:RestartFailure = $true
        Assert-Throws { Ensure-UbuntuConfiguration '/source/wsl_prepare.py' } 'interrupted restart'
        $state = $script:SavedSetup | ConvertFrom-Json
        Assert-True $state.RestartRequired 'Interrupted restart lost recovery intent'
        $script:RestartFailure = $false
        $script:ConfigureResult = 'UNCHANGED'
        $script:PidOne = 'systemd'
        $script:RecoveryCalls = @()
        Ensure-UbuntuConfiguration '/source/wsl_prepare.py'
        Assert-True (@($script:RecoveryCalls | Where-Object { $_[0] -eq '--terminate' }).Count -eq 1) 'Resume skipped pending restart after UNCHANGED'
        Assert-True (-not $state.RestartRequired) 'Successful restart did not clear intent'
        $script:RecoveryCalls = @()
        Ensure-UbuntuConfiguration '/source/wsl_prepare.py'
        Assert-True (@($script:RecoveryCalls | Where-Object { $_[0] -eq '--terminate' }).Count -eq 0) 'Unchanged ready distro restarted unnecessarily'
        $script:PidOne = 'init'
        $script:RecoveryCalls = @()
        Ensure-UbuntuConfiguration '/source/wsl_prepare.py'
        Assert-True (@($script:RecoveryCalls | Where-Object { $_[0] -eq '--terminate' }).Count -eq 1) 'Legacy stale PID1 was not restarted'
    } finally {
        foreach ($name in @('wsl.exe', 'Save-State', 'Invoke-Checked', 'Initialize-DistroSelection',
                            'Ensure-Distro', 'Ensure-UbuntuConfiguration')) {
            Remove-Item "Function:\$name"
        }
    }

    function global:Get-CimInstance {
        param([string]$ClassName)
        if ($ClassName -ne 'Win32_OperatingSystem') { throw 'Unexpected CIM query' }
        return [pscustomobject]@{ ProductType = 1; BuildNumber = 26100 }
    }
    function global:Get-PSDrive {
        param([string]$Name)
        return [pscustomobject]@{ Free = 100GB }
    }
    try {
        $plan = & (Join-Path $repo 'install.ps1') -Plan -BuildServer -AgentTool gh,codex 6>&1 | Out-String
        Assert-True ($plan -match 'Plan: install WinGet/WSL') 'Installer plan did not complete'
        Assert-Throws {
            & (Join-Path $repo 'install.ps1') -Plan -DistroName Debian
        } 'official stable Ubuntu WSL name'
        Assert-Throws {
            & (Join-Path $repo 'install.ps1') -Plan -AgentTool unsupported
        } 'Unsupported Ubuntu agent tool'
        Assert-Throws {
            & (Join-Path $repo 'install.ps1') -Plan -DistroLocation 'C:relative'
        } 'absolute Windows drive path'
        $runnerPlan = & (Join-Path $repo 'install.ps1') -Plan -CIRepository owner/repo -CIAllowedUser owner 6>&1 | Out-String
        Assert-True ($runnerPlan -match 'native Windows login tasks') 'Runner plan did not include Windows tasks'
        Assert-Throws {
            & (Join-Path $repo 'install.ps1') -Plan -CIRepository owner/repo -CIAllowedUser owner -CIRegisterWithGh -CIPromptToken
        } 'Choose RegisterWithGh'
        Assert-Throws {
            & (Join-Path $repo 'install.ps1') -Plan -UpdateSource
        } 'UpdateSource requires Resume'
    } finally {
        Remove-Item Function:\Get-CimInstance
        Remove-Item Function:\Get-PSDrive
    }

    & git.exe clone --quiet --no-local $repo $checkout
    Assert-True ($LASTEXITCODE -eq 0) 'Could not create the temporary Git checkout'
    New-Item -ItemType Directory -Path (Join-Path $checkout 'ci') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $checkout 'ci\fail.ps1') -Value 'exit 23'
    Set-Content -LiteralPath (Join-Path $checkout 'ci\missing.ps1') -Value 'Write-Output no-artifact'
    Set-Content -LiteralPath (Join-Path $checkout 'ci\slow.ps1') -Value 'Start-Sleep -Seconds 6'
    Set-Content -LiteralPath (Join-Path $checkout 'ci\large-log.ps1') -Value "[Console]::Out.Write('x' * (50MB + 1))"
    & git.exe -C $checkout add -- ci
    Assert-True ($LASTEXITCODE -eq 0) 'Could not stage native job fixtures'
    & git.exe -C $checkout -c user.name=BasaltwaterCI -c user.email=ci@example.invalid `
        commit --quiet -m 'Add native job fixtures'
    Assert-True ($LASTEXITCODE -eq 0) 'Could not commit native job fixtures'
    $revision = (& git.exe -C $checkout rev-parse HEAD | Select-Object -Last 1).Trim()
    $secure = ConvertTo-SecureString 'ci-only-token' -AsPlainText -Force
    ConvertFrom-SecureString $secure | Set-Content -LiteralPath $credential -Encoding ASCII
    $runner = Join-Path $repo 'windows\Invoke-BasaltwaterJob.ps1'
    $uri = 'https://upload.invalid/artifact'

    $global:BasaltwaterUploadCount = 0
    $global:BasaltwaterBadDigest = $false
    $global:BasaltwaterUploadStatus = 200
    function global:Invoke-WebRequest {
        param([switch]$UseBasicParsing, [string]$Method, [uri]$Uri,
              [string]$InFile, [string]$ContentType, [hashtable]$Headers,
              [int]$TimeoutSec, [int]$MaximumRedirection)
        if ($Method -ne 'Put' -or $Uri.Scheme -ne 'https') { throw 'Unexpected upload request' }
        if (-not $PSBoundParameters.ContainsKey('MaximumRedirection') -or $MaximumRedirection -ne 0) {
            throw 'Upload redirects must be disabled'
        }
        if ($Headers.Authorization -ne 'Bearer ci-only-token') { throw 'Upload authentication was lost' }
        $hash = (Get-FileHash -LiteralPath $InFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Headers['X-Artifact-SHA256'] -ne $hash) { throw 'Upload digest did not match artifact' }
        $global:BasaltwaterUploadCount++
        $acknowledged = if ($global:BasaltwaterBadDigest) { '0' * 64 } else { $hash }
        return [pscustomobject]@{ StatusCode = $global:BasaltwaterUploadStatus;
            Headers = @{ 'X-Artifact-SHA256' = $acknowledged } }
    }
    try {
        $before = @(Get-Receipts).Count
        & $runner -SourceDirectory $checkout -Revision $revision `
            -Script 'windows\examples\Smoke-Artifact.ps1' -Artifact 'out\wsl-smoke.txt' `
            -UploadUri $uri -CredentialFile $credential | Out-Null
        Assert-NewReceipt $before 'uploaded' | Out-Null
        Assert-True ($global:BasaltwaterUploadCount -eq 1) 'Successful job did not upload'
        Remove-Item -LiteralPath (Join-Path $checkout 'out\wsl-smoke.txt')

        Set-Content -LiteralPath (Join-Path $checkout 'ci\untracked.ps1') -Value 'exit 0'
        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\untracked.ps1' `
                -Artifact 'out\untracked.txt' -UploadUri $uri -CredentialFile $credential
        } 'not tracked by the requested revision'
        Assert-True (@(Get-Receipts).Count -eq $before) 'Untracked script created a job receipt'

        Set-Content -LiteralPath (Join-Path $checkout 'ci\fail.ps1') -Value 'exit 24'
        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\fail.ps1' `
                -Artifact 'out\changed.txt' -UploadUri $uri -CredentialFile $credential
        } 'differs from the requested revision'
        Assert-True (@(Get-Receipts).Count -eq $before) 'Modified script created a job receipt'
        & git.exe -C $checkout restore -- ci/fail.ps1
        Assert-True ($LASTEXITCODE -eq 0) 'Could not restore native job fixture'

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\fail.ps1' `
                -Artifact 'out\failed.txt' -UploadUri $uri -CredentialFile $credential
        } 'exited with code 23'
        Assert-NewReceipt $before 'failed' | Out-Null
        Assert-True ($global:BasaltwaterUploadCount -eq 1) 'Failed job attempted upload'

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\missing.ps1' `
                -Artifact 'out\missing.txt' -UploadUri $uri -CredentialFile $credential
        } 'produced no artifact'
        Assert-NewReceipt $before 'failed' | Out-Null
        Assert-True ($global:BasaltwaterUploadCount -eq 1) 'Missing artifact attempted upload'

        $global:BasaltwaterBadDigest = $true
        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision `
                -Script 'windows\examples\Smoke-Artifact.ps1' -Artifact 'out\wsl-smoke.txt' `
                -UploadUri $uri -CredentialFile $credential
        } 'did not confirm the artifact SHA-256'
        Assert-NewReceipt $before 'failed' | Out-Null
        $global:BasaltwaterBadDigest = $false
        Remove-Item -LiteralPath (Join-Path $checkout 'out\wsl-smoke.txt')

        $global:BasaltwaterUploadStatus = 302
        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision `
                -Script 'windows\examples\Smoke-Artifact.ps1' -Artifact 'out\wsl-smoke.txt' `
                -UploadUri $uri -CredentialFile $credential
        } 'non-success status'
        Assert-NewReceipt $before 'failed' | Out-Null
        $global:BasaltwaterUploadStatus = 200
        Remove-Item -LiteralPath (Join-Path $checkout 'out\wsl-smoke.txt')

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision `
                -Script 'windows\examples\Smoke-Artifact.ps1' -Artifact 'out\wsl-smoke.txt' `
                -UploadUri 'https://user:secret@upload.invalid/artifact' -CredentialFile $credential
        } 'must not contain credentials'
        Assert-True (@(Get-Receipts).Count -eq $before) 'Credential-bearing URL created a job receipt'

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script '..\outside.ps1' `
                -Artifact 'out\invalid.txt' -UploadUri $uri -CredentialFile $credential
        } 'Invalid job-relative path'
        Assert-True (@(Get-Receipts).Count -eq $before) 'Invalid path created a job receipt'

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\slow.ps1' `
                -Artifact 'out\slow.txt' -UploadUri $uri -CredentialFile $credential `
                -TimeoutSeconds 1
        } 'exceeded 1 seconds'
        Assert-NewReceipt $before 'timed-out' | Out-Null
        Assert-True ($global:BasaltwaterUploadCount -eq 3) 'Timeout attempted upload'

        $before = @(Get-Receipts).Count
        Assert-Throws {
            & $runner -SourceDirectory $checkout -Revision $revision -Script 'ci\large-log.ps1' `
                -Artifact 'out\large-log.txt' -UploadUri $uri -CredentialFile $credential
        } 'exceeded 50 MiB of logs'
        Assert-NewReceipt $before 'failed' | Out-Null
        Assert-True ($global:BasaltwaterUploadCount -eq 3) 'Oversized log attempted upload'
    } finally {
        Remove-Item Function:\Invoke-WebRequest
        Remove-Variable BasaltwaterUploadCount, BasaltwaterBadDigest, BasaltwaterUploadStatus -Scope Global -ErrorAction SilentlyContinue
    }

    & (Join-Path $PSScriptRoot 'Run-GitHubRunnerTests.ps1')
    if (-not $?) { throw 'GitHub runner offline qualification failed' }
    Write-Host 'Native Windows qualification passed.'
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
