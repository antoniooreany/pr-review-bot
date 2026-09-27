# Test that PowerShell can read a credential stored in Windows CredMan
# under target pr-review-bot:OPENAI_KEY.
#
# Usage (after running setup_credentials.ps1):
#   PS> .\scripts\test_credman.ps1
#
# Returns exit 0 if credential exists and is readable. Exits non-zero
# with diagnostic message otherwise.

$ErrorActionPreference = "Stop"
$target = "pr-review-bot:OPENAI_KEY"

Write-Host "Testing Windows Credential Manager for target: $target"

try {
    $stored = Get-StoredCredential -Target $target -ErrorAction Stop
} catch {
    Write-Host "FAIL: cannot read credential — $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Run scripts/setup_credentials.ps1 first to store the key."
    exit 1
}

if (-not $stored) {
    Write-Host "FAIL: Get-StoredCredential returned null" -ForegroundColor Red
    exit 1
}

try {
    $password = $stored.GetNetworkCredential().Password
} catch {
    Write-Host "FAIL: cannot extract password — $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrEmpty($password)) {
    Write-Host "FAIL: credential exists but password is empty" -ForegroundColor Red
    exit 1
}

$length = $password.Length
$password = $null
[System.GC]::Collect()

Write-Host "PASS: credential readable, key length=$length chars" -ForegroundColor Green
exit 0
