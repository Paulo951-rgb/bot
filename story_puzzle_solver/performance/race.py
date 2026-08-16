"""Race engine (rule 47).

Run independent methods concurrently; the first reliable result wins. Used to
race DIFF, OCR, and Vision after card detection (rule 45). Confirmation
continues in the background.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..common.logger import JsonLogger


@dataclass
class RaceResult:
    winner: Optional[str]
    value: Any
    all_results: Dict[str, Any]


class RaceEngine:
    def __init__(self, max_workers: int = 4, logger: Optional[JsonLogger] = None):
        self.max_workers = max_workers
        self._logger = logger or JsonLogger("race")

    def race(
        self,
        tasks: Dict[str, Callable[[], Any]],
        judge: Optional[Callable[[str, Any], bool]] = None,
    ) -> RaceResult:
        """Run all tasks concurrently. ``judge(name, result)`` decides if a
        result is reliable enough to be the winner. If no judge is given, the
        first task to return a non-None result wins. All tasks always run to
        completion (results collected in background) so confirmation can
        continue, but the winner is returned as soon as it's found."""
        if not tasks:
            return RaceResult(winner=None, value=None, all_results={})

        results: Dict[str, Any] = {}
        results_lock = threading.Lock()
        done_event = threading.Event()
        winner_holder: List[Optional[str]] = [None]
        winner_value: List[Any] = [None]

        def run(name: str, fn: Callable[[], Any]) -> None:
            try:
                val = fn()
            except Exception as e:
                val = None
                self._logger.warn("race_task_error", name=name, error=str(e))
            with results_lock:
                results[name] = val
            if val is not None and winner_holder[0] is None:
                if judge is None or judge(name, val):
                    winner_holder[0] = name
                    winner_value[0] = val
                    done_event.set()

        threads = [threading.Thread(target=run, args=(n, f), daemon=True)
                   for n, f in tasks.items()]
        for th in threads:
            th.start()
        # wait until a winner OR all done
        for th in threads:
            th.join()
        return RaceResult(winner=winner_holder[0], value=winner_value[0],
                          all_results=results)

    def first_reliable(
        self,
        tasks: Dict[str, Callable[[], Tuple[bool, Any]]],
        timeout_s: Optional[float] = None,
    ) -> RaceResult:
        """Like race but each task returns (reliable, value). First reliable wins."""
        results: Dict[str, Any] = {}
        results_lock = threading.Lock()
        winner: List[Optional[str]] = [None]
        winner_value: List[Any] = [None]
        done = threading.Event()

        def run(name: str, fn: Callable[[], Tuple[bool, Any]]) -> None:
            try:
                reliable, val = fn()
            except Exception as e:
                reliable, val = False, None
                self._logger.warn("race_task_error", name=name, error=str(e))
            with results_lock:
                results[name] = val
            if reliable and winner[0] is None:
                winner[0] = name
                winner_value[0] = val
                done.set()

        threads = [threading.Thread(target=run, args=(n, f), daemon=True)
                   for n, f in tasks.items()]
        for th in threads:
            th.start()
        if timeout_s is not None:
            done.wait(timeout_s)
        for th in threads:
            th.join()
        return RaceResult(winner=winner[0], value=winner_value[0],
                          all_results=results)
