# ============================================================
# Story Puzzle Solver — Windows setup (rule 20 / §20 / §67)
# Run:  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# ============================================================
$ErrorActionPreference = "Stop"

Write-Host "=== Story Puzzle Solver — installation Windows ===" -ForegroundColor Cyan

# 1. Python deps
Write-Host "[1/4] Dependances Python..." -ForegroundColor Yellow
python -m pip install --upgrade pip
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "ECHEC: pip install" -ForegroundColor Red; exit 1 }

# 2. Tesseract check
Write-Host "[2/4] Tesseract..." -ForegroundColor Yellow
$tes = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tes) {
    Write-Host "Tesseract absent. Installez-le:" -ForegroundColor Yellow
    Write-Host "  Option A: winget install UB-Mannheim.TesseractOCR" -ForegroundColor Yellow
    Write-Host "  Option B: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Yellow
    Write-Host "Puis ajoutez le dossier d'installation au PATH." -ForegroundColor Yellow
} else { Write-Host "Tesseract OK." -ForegroundColor Green }

# 3. FFmpeg check (video support)
Write-Host "[3/4] FFmpeg (video)..." -ForegroundColor Yellow
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff) {
    Write-Host "FFmpeg absent (video non supportee). Installez:" -ForegroundColor Yellow
    Write-Host "  winget install Gyan.FFmpeg" -ForegroundColor Yellow
} else { Write-Host "FFmpeg OK." -ForegroundColor Green }

# 4. Simulation fixtures + health check
Write-Host "[4/4] Fixtures + health check..." -ForegroundColor Yellow
python -c "from pathlib import Path; from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator; FixtureGenerator(Path('fixtures'), seed=7).competition_scenario(); print('fixtures OK')"
powershell -ExecutionPolicy Bypass -File scripts\health_check_windows.ps1

Write-Host ""
Write-Host "=== Installation terminee ===" -ForegroundColor Cyan
Write-Host "Lancement simulation :  npm run start" -ForegroundColor Green
Write-Host "Test du jour J       :  npm run competition-test" -ForegroundColor Green
