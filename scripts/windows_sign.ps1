param(
    [Parameter(Mandatory = $true)]
    [string[]] $Path
)

$ErrorActionPreference = "Stop"

$certPath = $env:WINDOWS_CODESIGN_CERT_PATH
$certPassword = $env:WINDOWS_CODESIGN_CERT_PASSWORD
$certSha1 = $env:WINDOWS_CODESIGN_CERT_SHA1
$timestampUrl = $env:WINDOWS_CODESIGN_TIMESTAMP_URL
$description = $env:WINDOWS_CODESIGN_DESCRIPTION

if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    $timestampUrl = "http://timestamp.digicert.com"
}

if ([string]::IsNullOrWhiteSpace($description)) {
    $description = "SoloKeys GUI"
}

if ([string]::IsNullOrWhiteSpace($certPath) -and [string]::IsNullOrWhiteSpace($certSha1)) {
    Write-Host "Windows code signing is not configured; skipping."
    exit 0
}

function Find-SignTool {
    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kitsRoot) {
        $candidate = Get-ChildItem -Path $kitsRoot -Recurse -Filter "signtool.exe" |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    throw "signtool.exe not found. Install the Windows SDK or add signtool.exe to PATH."
}

$signTool = Find-SignTool
$certArgs = @()

if (-not [string]::IsNullOrWhiteSpace($certSha1)) {
    $certArgs += @("/sha1", $certSha1)
} else {
    if (-not (Test-Path $certPath)) {
        throw "WINDOWS_CODESIGN_CERT_PATH does not exist: $certPath"
    }
    $certArgs += @("/f", $certPath)
    if (-not [string]::IsNullOrWhiteSpace($certPassword)) {
        $certArgs += @("/p", $certPassword)
    }
}

foreach ($item in $Path) {
    $resolved = Resolve-Path -LiteralPath $item -ErrorAction Stop
    foreach ($target in $resolved) {
        Write-Host "Signing $($target.Path)"
        & $signTool sign `
            /fd SHA256 `
            /tr $timestampUrl `
            /td SHA256 `
            /d $description `
            @certArgs `
            $target.Path
        if ($LASTEXITCODE -ne 0) {
            throw "signtool.exe failed for $($target.Path)"
        }

        & $signTool verify /pa /v $target.Path
        if ($LASTEXITCODE -ne 0) {
            throw "Signature verification failed for $($target.Path)"
        }
    }
}
