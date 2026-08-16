#!/usr/bin/env bash
# Story Puzzle Solver — install script (rule 67)
set -e
echo "=== Installation de Story Puzzle Solver ==="

# Python deps
echo "[1/3] Installation des dépendances Python…"
pip install -r requirements.txt

# System tools check
echo "[2/3] Vérification des outils système…"
python -m story_puzzle_solver.app.cli check

# Generate fixtures for simulation
echo "[3/3] Génération des fixtures de simulation…"
python -c "
from pathlib import Path
from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator
fg = FixtureGenerator(Path('fixtures'), seed=7)
sc = fg.competition_scenario()
print(f'  {len(sc)} stories générées dans fixtures/')
"

echo ""
echo "✓ Installation terminée."
echo "Lancez avec : npm run start   (ou  python -m story_puzzle_solver.app.cli start --simulation)"
