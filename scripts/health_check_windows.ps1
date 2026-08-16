# ============================================================
# Story Puzzle Solver — health check (rule 20 / §20)
# Run on Windows:  powershell -ExecutionPolicy Bypass -File scripts\health_check_windows.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$ok = 0; $fail = 0

function Check($name, $condition, $fix) {
    if ($condition) { Write-Host "[OK]   $name" -ForegroundColor Green; $script:ok++ }
    else { Write-Host "[FAIL] $name" -ForegroundColor Red; Write-Host "       -> $fix" -ForegroundColor Yellow; $script:fail++ }
}

# Python
$py = (Get-Command python -ErrorAction SilentlyContinue)
Check "Python" ($py -ne $null) "Installez Python 3.9+ depuis https://python.org (cochez 'Add to PATH')."

# Tesseract
$tes = (Get-Command tesseract -ErrorAction SilentlyContinue)
Check "Tesseract" ($tes -ne $null) "Installez Tesseract (UB Mannheim build) et ajoutez-le au PATH."

# FFmpeg
$ff = (Get-Command ffmpeg -ErrorAction SilentlyContinue)
Check "FFmpeg" ($ff -ne $null) "Installez FFmpeg (ffmpeg.org) ou via 'winget install Gyan.FFmpeg'."

# Python deps + OCR
try {
    $null = python -c "import cv2, numpy, PIL, scipy, pytesseract; print('ok')" 2>&1
    $depsOk = $LASTEXITCODE -eq 0
} catch { $depsOk = $false }
Check "Modules Python (cv2, numpy, PIL, scipy, pytesseract)" $depsOk "pip install -r requirements.txt"

# OCR self-test (tesseract callable)
try {
    $ocrOut = python -c "import pytesseract, cv2, numpy; img=numpy.full((40,200,3),255,numpy.uint8); cv2.putText(img,'1234',(10,30),cv2.FONT_HERSHEY_SIMPLEX,1,(0,0,0),2); print(pytesseract.image_to_string(img, config='--psm 7 -c tessedit_char_whitelist=0123456789').strip())" 2>&1
    $ocrOk = $ocrOut -match "1234"
} catch { $ocrOk = $false }
Check "OCR (Tesseract reconnaît des chiffres)" $ocrOk "Verifiez que tesseract est dans le PATH et pytesseract installe."

# Clipboard (Set-Clipboard present on Windows PowerShell)
$clipOk = $true
try { Set-Clipboard -Value "sps_test"; $got = Get-Clipboard; $clipOk = ($got -eq "sps_test") } catch { $clipOk = $false }
Check "Clipboard (Set-Clipboard)" $clipOk "PowerShell >= 5.1 requis (integre sur Windows 10/11)."

# Notification (toast availability — informational, not blocking)
$notifOk = $true
Write-Host "[INFO] Notification toast: test via 'python -m story_puzzle_solver.app.cli start --simulation' (verifie visuellement)." -ForegroundColor Cyan

# Configuration (.env optionnel mais coherent)
$cfgOk = $true
if (Test-Path ".env") {
    try { Get-Content ".env" | Out-Null } catch { $cfgOk = $false }
}
Check "Configuration (.env)" $cfgOk "Corrigez ou creez .env (voir .env.example)."

# Storage dirs writable
$storOk = $true
try { New-Item -ItemType Directory -Force -Path ".state", ".logs" | Out-Null; $t = ".state\.wtest"; Set-Content $t "x"; Remove-Item $t } catch { $storOk = $false }
Check "Stockage (.state/.logs inscriptible)" $storOk "Verifiez les permissions du dossier projet."

# Playwright (optional, for --source browser)
try {
    $null = python -c "import playwright; print('ok')" 2>&1
    $pwOk = $LASTEXITCODE -eq 0
} catch { $pwOk = $false }
Check "Playwright (mode navigateur, optionnel)" $pwOk "pip install playwright && playwright install chromium"

# Browser target username
$sn = $env:SNAP_TARGET_USERNAME
if ([string]::IsNullOrWhiteSpace($sn)) {
    $snOk = $false
} else { $snOk = $true }
Check "SNAP_TARGET_USERNAME (mode navigateur)" $snOk "set SNAP_TARGET_USERNAME=benoit  (CMD) ou  $env:SNAP_TARGET_USERNAME='benoit'  (PowerShell), ou .env"

# Source adapter
Write-Host "[INFO] Source adapter: simulation (defaut), folder (npm run start:folder), browser (npm run start:browser)." -ForegroundColor Cyan

Write-Host ""
Write-Host "Resume: $ok OK, $fail FAIL" -ForegroundColor $(if ($fail -eq 0) {"Green"} else {"Red"})
if ($fail -gt 0) { exit 1 } else { exit 0 }
