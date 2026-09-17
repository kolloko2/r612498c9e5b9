$ErrorActionPreference = "Stop"

$privateDirectory = Join-Path $PSScriptRoot "private"
$target = Join-Path $privateDirectory "test.env"
if (Test-Path -LiteralPath $target) {
    throw "Refusing to overwrite existing directory test environment: $target"
}

New-Item -ItemType Directory -Path $privateDirectory -Force | Out-Null
function New-TestSecret {
    [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
}

$lines = @(
    "DIRECTORY_ADMIN_PASSWORD=$(New-TestSecret)"
    "DIRECTORY_BIND_PASSWORD=$(New-TestSecret)"
    "DIRECTORY_TEST_PASSWORD=$(New-TestSecret)"
)
[IO.File]::WriteAllLines($target, $lines, [Text.UTF8Encoding]::new($false))

if ($IsWindows -or $env:OS -eq "Windows_NT") {
    & icacls.exe $target "/inheritance:r" "/grant:r" "${env:USERNAME}:(R,W)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict ACL on $target"
    }
} elseif (Get-Command chmod -ErrorAction SilentlyContinue) {
    & chmod 600 $target
}

Write-Host "Created ignored directory test environment at $target"
