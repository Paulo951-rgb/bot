"""CLI launcher + dependency checks (rules 68, 69, 70, 84).

Commands:
  python -m story_puzzle_solver.app.cli start [--simulation]
  python -m story_puzzle_solver.app.cli check
  python -m story_puzzle_solver.app.cli run-simulation
  python -m story_puzzle_solver.app.cli competition-test
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from ..config import Config, RunMode
from ..pipeline import PuzzlePipeline
from ..source import SimulationStorySource
from ..simulation.fixture_generator import FixtureGenerator


def check_dependencies() -> int:
    """Rule 69: verify all deps at startup; clear error if missing."""
    print("Vérification des dépendances…")
    errors = []
    # python version
    import sys as _sys
    if _sys.version_info < (3, 9):
        errors.append(f"Python {_sys.version} — 3.9+ requis")
    else:
        print(f"  ✓ Python {_sys.version.split()[0]}")
    # core libs
    for mod in ("cv2", "numpy", "PIL", "scipy"):
        try:
            __import__(mod)
            print(f"  ✓ {mod}")
        except ImportError:
            errors.append(f"Module {mod} manquant — pip install {mod.replace('cv2','opencv-python-headless').replace('PIL','pillow')}")
    # tesseract
    import shutil
    if shutil.which("tesseract"):
        print("  ✓ tesseract")
    else:
        errors.append("❌ tesseract manquant — installez tesseract-ocr")
    # ffmpeg (for video)
    if shutil.which("ffmpeg") or shutil.which("ffprobe"):
        print("  ✓ ffmpeg")
    else:
        print("  ⚠ ffmpeg non trouvé (vidéo non supportée)")
    if errors:
        print("\nErreurs:")
        for e in errors:
            print(f"  {e}")
        print("\nCorrigez les erreurs ci-dessus avant de lancer la compétition.")
        return 1
    print("\n✓ Toutes les dépendances sont présentes.")
    return 0


def run_simulation(config: Config, headless: bool = True) -> dict:
    """Process the full competition scenario and report results."""
    config.ensure_dirs()
    fg = FixtureGenerator(Path("fixtures"), seed=7)
    scenario = fg.competition_scenario()
    src = SimulationStorySource(scenario, download_latency_ms=config.simulation_latency_ms,
                                jitter_ms=config.simulation_jitter_ms)
    src.connect()
    pipe = PuzzlePipeline(config)
    pipe.prewarm()
    items = src.poll()
    tmp = Path(tempfile.mkdtemp())
    total_notif = 0
    results = []
    for it in items:
        dest = tmp / (it.story_id + Path(it.media_path).suffix)
        p = src.get_media(it, dest)
        t0 = time.time()
        r = pipe.process(it, p)
        total_notif += r.notifications
        results.append({"story": it.story_id, "kind": r.media_kind,
                        "card": r.card_detected, "notif": r.notifications,
                        "latency_ms": round((time.time() - t0) * 1000),
                        "media_to_result_ms": round(r.media_to_result_ms)})
    fe = pipe.fast_entry()
    pipe.save_state()
    return {"results": results, "total_notifications": total_notif,
            "fast_entry": fe, "metrics": pipe.metrics.snapshot()}


def cmd_start(args) -> int:
    config = Config.load()
    config.ensure_dirs()
    from .server import DashboardServer
    pipe = Pipeline = PuzzlePipeline(config)
    pipe.prewarm()
    source = None
    if config.simulation or args.simulation:
        fg = FixtureGenerator(Path("fixtures"), seed=7)
        sc = fg.competition_scenario()
        source = SimulationStorySource(sc, download_latency_ms=config.simulation_latency_ms)
        source.connect()
    srv = DashboardServer(pipe, source=source, host=args.host, port=args.port,
                          poll_interval_ms=config.poll_interval_ms)
    print(f"Dashboard: http://{args.host}:{args.port}")
    srv.start(block=True)
    return 0


def cmd_run_simulation(args) -> int:
    config = Config.load()
    config.simulation = True
    report = run_simulation(config)
    print("\n=== Résultats de la simulation ===")
    for r in report["results"]:
        print(f"  {r['story']}: {r['kind']} carte={'oui' if r['card'] else 'non'} "
              f"notif={r['notif']} latence={r['latency_ms']}ms")
    fe = report["fast_entry"]
    print(f"\nNUMÉRO: {fe['number']['display']}")
    print(f"NOM: {fe['name']['display']}")
    print(f"CVV: {fe['cvv']['display']}")
    print(f"Notifications totales: {report['total_notifications']}")
    m = report["metrics"]
    mr = m.get("media_to_result_ms", {})
    print(f"Média→Résultat p50: {mr.get('p50', 0):.0f}ms  p90: {mr.get('p90', 0):.0f}ms")
    return 0


def cmd_test(args) -> int:
    """Rule 58: run all tests, show PASS/FAIL/SKIPPED."""
    import subprocess
    print("Exécution des tests…")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"])
    if r.returncode == 0:
        print("\n✓ TOUS LES TESTS CRITIQUES ONT RÉUSSI")
    else:
        print("\n✗ CERTAINS TESTS ONT ÉCHOUÉ")
    return r.returncode


def cmd_competition_test(args) -> int:
    """Rule 81: simulate the full D-day timeline."""
    print("=== TEST JOUR J (simulation complète) ===")
    config = Config.load()
    config.simulation = True
    report = run_simulation(config)
    fe = report["fast_entry"]
    ok = (not fe["number"]["partial"] and "?" not in fe["number"]["display"])
    print(f"\nNUMÉRO final: {fe['number']['display']}")
    print(f"Complet: {'✓ OUI' if ok else '✗ NON (partiel)'}")
    print(f"Notifications: {report['total_notifications']}")
    if ok and report["total_notifications"] > 0:
        print("\n✓ TEST JOUR J RÉUSSI")
        return 0
    print("\n✗ TEST JOUR J ÉCHOUÉ")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="story-puzzle-solver",
                                     description="Story Puzzle Solver")
    sub = parser.add_subparsers(dest="cmd")

    p_start = sub.add_parser("start", help="Lancer la surveillance + dashboard")
    p_start.add_argument("--simulation", action="store_true", help="Utiliser la source simulée")
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=8765)
    p_start.set_defaults(func=cmd_start)

    p_sim = sub.add_parser("run-simulation", help="Exécuter la simulation complète")
    p_sim.set_defaults(func=cmd_run_simulation)

    p_ct = sub.add_parser("competition-test", help="Test du jour J (règle 81)")
    p_ct.set_defaults(func=cmd_competition_test)

    p_check = sub.add_parser("check", help="Vérifier les dépendances (règle 69)")
    p_check.set_defaults(func=lambda a: check_dependencies())

    p_test = sub.add_parser("test", help="Exécuter tous les tests (règle 58)")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
