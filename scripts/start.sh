#!/usr/bin/env bash
# Story Puzzle Solver — launch script (rule 68)
set -e
exec python -m story_puzzle_solver.app.cli "$@"
