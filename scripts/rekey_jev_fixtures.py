"""Re-key recorded Jev answers after a change to the simulated state they describe.

    python scripts/rekey_jev_fixtures.py [--write]

A fixture is keyed by a hash of the state the question was asked about, so a change of a
few cents in a dispatch plan drops every recorded answer and the run silently falls back
to the rules. Re-recording needs an API key and a network call; the recorded answers are
still the answers to the same questions, so this maps each old key onto the new one
instead of inventing an answer.

The mapping comes from replaying every path that asks Jev twice, each in its own process:
once with the previous allocation, once with the current one. The replay with the current
allocation answers a missed key with the recorded answer the previous run got at the same
point, so both runs take the same decisions and ask the same questions in the same order;
pair *n* of one is then pair *n* of the other. The answers themselves are never touched.
``scripts/jev_fixture_audit.py`` checks the result: every fixture found before is found
again.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import cast

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device
from gridsignal.jev.client import FIXTURE_DIR, FixtureStore, JevResponse

_get = FixtureStore.get
Ask = tuple[str, str, dict[str, object] | None]


def _legacy_share(
    self: ControlRoomEngine, pool: list[Device], target: float, headroom: dict[str, float]
) -> float:
    """The allocation as it rounded before: every share rounded on its own."""
    total_headroom = sum(headroom[d.device_id] for d in pool)
    if total_headroom <= 0 or target <= 0:
        return 0.0
    share = min(target, total_headroom)
    for device in pool:
        device.assigned_kw = round(share * headroom[device.device_id] / total_headroom, 2)
    return round(sum(d.assigned_kw for d in pool), 2)


def collect(legacy: bool, replay: dict[str, list[dict[str, object] | None]]) -> list[Ask]:
    """Every (fixture, key, answer) asked by the reports, in the order they are asked."""
    seen: list[Ask] = []
    asked: dict[str, int] = defaultdict(int)

    def _watched_get(store: FixtureStore, key: str) -> JevResponse | None:
        index = asked[store.name]
        asked[store.name] += 1
        entry = store.entries.get(key)
        if entry is None:
            recorded = replay.get(store.name, [])
            if index < len(recorded) and recorded[index] is not None:
                # Answer it as the previous run was answered, so the two runs stay in step.
                store.entries[key] = cast(dict[str, object], recorded[index])
        seen.append((store.name, key, store.entries.get(key)))
        return _get(store, key)

    FixtureStore.get = _watched_get  # type: ignore[method-assign]
    if legacy:
        ControlRoomEngine._share = _legacy_share  # type: ignore[method-assign]

    from gridsignal import deliverability_report, demo_numbers, drills, judgment_report
    from gridsignal.jev import evaluate

    demo_numbers.report()
    judgment_report.build()
    drills.run()
    evaluate.evaluate()
    evaluate.fixture_deltas()
    deliverability_report.run()
    return seen


def _in_subprocess(legacy: bool, replay: dict[str, list[dict[str, object] | None]]) -> list[Ask]:
    """Each pass runs alone: the reports cache their results within a process."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "asks.json"
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--collect",
            "legacy" if legacy else "current",
            "--out",
            str(out),
        ]
        subprocess.run(argv, input=json.dumps(replay), text=True, check=True)
        return [(name, key, entry) for name, key, entry in json.loads(out.read_text())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the fixture files")
    parser.add_argument("--collect", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--out", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.collect is not None:
        replay = json.loads(sys.stdin.read() or "{}")
        asks = collect(args.collect == "legacy", replay)
        Path(str(args.out)).write_text(json.dumps(asks), encoding="utf-8")
        return 0

    before = _in_subprocess(legacy=True, replay={})
    answers: dict[str, list[dict[str, object] | None]] = defaultdict(list)
    for name, _key, entry in before:
        answers[name].append(entry)
    after = _in_subprocess(legacy=False, replay=dict(answers))

    old_keys: dict[str, list[str]] = defaultdict(list)
    new_keys: dict[str, list[str]] = defaultdict(list)
    for name, key, _entry in before:
        old_keys[name].append(key)
    for name, key, _entry in after:
        new_keys[name].append(key)

    renames: dict[str, dict[str, str]] = defaultdict(dict)
    for name, olds in sorted(old_keys.items()):
        news = new_keys.get(name, [])
        if len(olds) != len(news):
            print(f"{name:34s} {len(olds)} asks before, {len(news)} after: skipped")
            continue
        for old, new in zip(olds, news, strict=True):
            if old != new:
                renames[name][old] = new

    for name, mapping in sorted(renames.items()):
        path = FIXTURE_DIR / f"{name}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = dict(raw.get("entries") or {})
        moved = {mapping.get(key, key): entry for key, entry in entries.items()}
        changed = sum(1 for key in entries if key in mapping)
        print(f"{name:34s} {changed:4d} of {len(entries):4d} answers re-keyed")
        if args.write and changed:
            raw["entries"] = dict(sorted(moved.items()))
            path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.write:
        print("dry run; pass --write to rewrite the fixtures")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
