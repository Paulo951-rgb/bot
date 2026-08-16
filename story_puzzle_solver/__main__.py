"""Entry point: ``python -m story_puzzle_solver``."""
import sys

from .app.cli import main

if __name__ == "__main__":
    sys.exit(main())
