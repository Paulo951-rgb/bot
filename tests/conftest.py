"""Shared test fixtures."""
import sys
from pathlib import Path

# ensure project root on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from story_puzzle_solver.config import Config
from story_puzzle_solver.simulation.fixture_generator import FixtureGenerator


@pytest.fixture
def config(tmp_path):
    cfg = Config.load()
    cfg.state_dir = tmp_path / ".state"
    cfg.log_dir = tmp_path / ".logs"
    cfg.simulation = True
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def fixtures_dir():
    return Path("fixtures")


@pytest.fixture
def generator(fixtures_dir):
    return FixtureGenerator(fixtures_dir, seed=7)


@pytest.fixture
def scenario(generator):
    return generator.competition_scenario()
