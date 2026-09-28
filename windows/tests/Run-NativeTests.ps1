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

    Write-Host 'Native Windows qualification passed.'
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
