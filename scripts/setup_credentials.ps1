# Setup script for pr-review-bot secrets via Windows Credential Manager.
# Reads API key securely (hidden input), stores in OS-encrypted Vault.
#
# Usage (PowerShell as Administrator):
#   PS> .\scripts\setup_credentials.ps1
#
# What it does:
#   1. Prompts for MiniMax API key (hidden)
#   2. Stores under target pr-review-bot:OPENAI_KEY
#   3. Verifies storage with cmdkey /list
#   4. Tests retrieval via the same path the bot uses
#
# The bot (T29 get_secret) reads via:
#   Get-StoredCredential -Target 'pr-review-bot:OPENAI_KEY'
# so the target name MUST be exactly 'pr-review-bot:OPENAI_KEY'.

$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$msg)
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Test-Windows {
    if ($env:OS -notmatch "Windows") {
        Write-Host "ERROR: this script must run on Windows." -ForegroundColor Red
        exit 1
    }
}

# ── Step 1: Welcome ───────────────────────────────────────────────────

Test-Windows
Write-Host "==============================================================="
Write-Host " pr-review-bot credential setup (Windows Credential Manager)"
Write-Host "==============================================================="
Write-Host ""
Write-Host "Target store : pr-review-bot:OPENAI_KEY"
Write-Host "Storage      : Windows Vault (DPAPI-encrypted)"
Write-Host ""

# ── Step 2: Read key securely ──────────────────────────────────────────

$secureKey = Read-Host -Prompt "Enter MiniMax API key (hidden)" -AsSecureString
if ($secureKey.Length -eq 0) {
    Write-Host "ERROR: empty key." -ForegroundColor Red
    exit 1
}

# ── Step 3: Convert SecureString to plain (only in memory) ─────────────

# cmdkey only accepts /user:<plaintext>. There's no PowerShell API to
# write a SecureString to Vault directly. So we marshal the BSTR, use it,
# zero it.
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
}
finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
}

if ([string]::IsNullOrWhiteSpace($plainKey)) {
    Write-Host "ERROR: failed to read secure string." -ForegroundColor Red
    exit 1
}

# ── Step 4: Store in CredMan ───────────────────────────────────────────

Write-Step "Storing in Windows Credential Manager..."
$target = "pr-review-bot:OPENAI_KEY"
# IMPORTANT: use /pass: (not /user:). The bot reads via
# Get-StoredCredential().GetNetworkCredential().Password, which returns
# the PASSWORD field. Storing in /user: puts the key in the username
# field — bot would read empty string.
cmdkey /generic:$target /user:MiniMax /pass:$plainKey
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: cmdkey failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# Clear plain text from PowerShell memory ASAP
$plainKey = $null
[System.GC]::Collect()

# ── Step 5: Verify storage ─────────────────────────────────────────────

Write-Step "Verifying storage with cmdkey /list..."
$listOutput = cmdkey /list
if ($listOutput -match [regex]::Escape($target)) {
    Write-Host "  OK: $target is stored" -ForegroundColor Green
} else {
    Write-Host "  WARN: cmdkey /list did not show $target (might still work, just invisible)" -ForegroundColor Yellow
}

# ── Step 6: Test retrieval via PowerShell (same path as bot) ───────────

Write-Step "Testing retrieval via PowerShell (same path the bot uses)..."
# Ensure CredentialManager module is loaded (provides Get-StoredCredential).
# Windows PowerShell 5.1 has it as a snap-in; PowerShell Core via PSGallery.
$credModuleLoaded = $false
try {
    if ($PSVersionTable.PSVersion.Major -lt 6) {
        # Windows PowerShell — try snap-in first
        if (-not (Get-Command -Name Get-StoredCredential -ErrorAction SilentlyContinue)) {
            Add-PSSnapin Microsoft.PowerShell.CredentialManagement -ErrorAction Stop
        }
    } else {
        # PowerShell Core — load if installed (don't auto-install, requires PSGallery access)
        Import-Module CredentialManager -ErrorAction SilentlyContinue
    }
    $credModuleLoaded = $true
} catch {
    Write-Host "  WARN: CredentialManager module not available: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "        Install manually: Install-Module CredentialManager -Scope CurrentUser" -ForegroundColor Yellow
    Write-Host "        Bot will fall back to cmdkey.exe /list for verification." -ForegroundColor Yellow
}

if ($credModuleLoaded) {
    try {
        $stored = Get-StoredCredential -Target $target -ErrorAction Stop
        if ($stored) {
            $password = $stored.GetNetworkCredential().Password
            if ($password.Length -gt 0) {
                Write-Host "  OK: bot can retrieve key (length=$($password.Length))" -ForegroundColor Green
            } else {
                Write-Host "  ERROR: retrieved empty password" -ForegroundColor Red
                exit 1
            }
            $password = $null
            [System.GC]::Collect()
        } else {
            Write-Host "  ERROR: Get-StoredCredential returned null" -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
} else {
    # Fallback verification: just confirm cmdkey sees the credential
    $listOutput = cmdkey /list | Out-String
    if ($listOutput -match [regex]::Escape($target)) {
        Write-Host "  PARTIAL: cmdkey sees credential, but bot cannot retrieve without CredentialManager module." -ForegroundColor Yellow
        Write-Host "          Install CredentialManager module before running the bot:" -ForegroundColor Yellow
        Write-Host "          Install-Module CredentialManager -Scope CurrentUser" -ForegroundColor Yellow
    } else {
        Write-Host "  ERROR: cmdkey /list does not show $target" -ForegroundColor Red
        exit 1
    }
}

# ── Done ─────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==============================================================="
Write-Host " Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Stored credential: $target"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Start the bot:"
Write-Host "       docker compose -f docker-compose.yml \"
Write-Host "                        -f deploy/docker-compose.prod.yml up -d"
Write-Host "  2. Verify it reads the key:"
Write-Host "       docker compose exec slack-notifier python -c \"
Write-Host "         'from notifier import get_secret; print(\"OK len=\"+str(len(get_secret(\"OPENAI_KEY\"))))'"
Write-Host ""
Write-Host "To rotate the key later, just run this script again."
Write-Host "==============================================================="
