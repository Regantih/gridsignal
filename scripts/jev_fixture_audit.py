"""Report which recorded Jev answers the current code still finds.

    python scripts/jev_fixture_audit.py

A fixture is keyed by a hash of the simulated state it was asked about, so any change
to that state silently drops the recorded answer and the run falls back to the rules.
This prints a hit/miss count per fixture file so that never passes unnoticed.
"""

from __future__ import annotations

from collections import Counter

from gridsignal.jev.client import FixtureStore

HITS: Counter[str] = Counter()
MISSES: Counter[str] = Counter()
_get = FixtureStore.get


def _counted_get(self: FixtureStore, key: str):  # type: ignore[no-untyped-def]
    found = _get(self, key)
    (HITS if found is not None else MISSES)[self.name] += 1
    return found


def main() -> int:
    FixtureStore.get = _counted_get  # type: ignore[method-assign]
    from gridsignal import deliverability_report, demo_numbers, drills, judgment_report
    from gridsignal.jev import evaluate

    demo_numbers.report()
    judgment_report.build()
    drills.run()
    evaluate.evaluate()
    evaluate.fixture_deltas()
    deliverability_report.run()

    for name in sorted(set(HITS) | set(MISSES)):
        print(f"{name:40s} hit {HITS[name]:4d}  miss {MISSES[name]:4d}")
    return 1 if MISSES else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
