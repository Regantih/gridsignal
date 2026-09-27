"""Record Jev's answers to the safety pack v2 so the tests need no key.

    python scripts/record_judgment_fixtures.py

Asks the live model once per held-out drill and once per simulated override episode and
writes the replies into ``data/jev_fixtures``. Everything downstream — tests, the report
CLI, the Control Room — replays those recordings, so the repository stays keyless and
offline. Re-run this only when the question pack or the situations change.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.jev import judgment, overrides
from gridsignal.jev.client import FIXTURE_DIR, JevClient, Source
from gridsignal.jev.incident import judge_incident
from gridsignal.jev.overrides import FLEET_SIZES, PRICE_DAYS
from gridsignal.judgment_report import (
    HOLDOUT_FIXTURE,
    OVERRIDE_FIXTURE,
    drill_situation,
)
from gridsignal.mesh.scenarios import HOLDOUT_DIR
from gridsignal.prices import load_scenario

load_price_scenario = load_scenario

ATTEMPTS = 6
BACKOFF_S = 4.0
#: The gateway is flaky rather than strictly rate limited, so a few shards in parallel
#: finish far sooner than one serial pass. Each shard records into its own copy of the
#: fixture file and the copies are merged at the end.
SHARDS = 4

PACK_QUESTIONS = judgment.PACK.questions()


def _shard(name: str, situations: list[judgment.Situation], directory: Path) -> int:
    client = JevClient.for_scenario(name, directory=directory, fallback=judgment.rules_answers)
    live = 0
    for situation in situations:
        for attempt in range(ATTEMPTS):
            try:
                response = client.ask(situation.as_state(), PACK_QUESTIONS)
            except Exception as error:  # noqa: BLE001 - transient gateway errors
                print(f"  {situation.label}: {type(error).__name__}, retrying")
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            live += int(response.source is Source.LIVE)
            print(f"  {situation.label}: {response.source.value}")
            break
        client.flush()
    return live


def _merge(name: str, shard_dirs: list[Path]) -> None:
    """Fold every shard's entries back into the one fixture file the repo ships."""
    target = FIXTURE_DIR / f"{name}.json"
    payload: dict[str, object] = {}
    entries: dict[str, object] = {}
    if target.exists():
        payload = json.loads(target.read_text(encoding="utf-8"))
        entries = dict(payload.get("entries") or {})
    for directory in shard_dirs:
        path = directory / f"{name}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        payload = {**raw, **payload}
        entries.update(dict(raw.get("entries") or {}))
    payload["entries"] = dict(sorted(entries.items()))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record(name: str, situations: list[judgment.Situation]) -> int:
    """Ask once per situation, in a few parallel shards, and write one fixture file."""
    if len(situations) <= SHARDS:
        return _shard(name, situations, FIXTURE_DIR)
    root = Path(tempfile.mkdtemp(prefix=f"{name}-shards-"))
    dirs = []
    for index in range(SHARDS):
        directory = root / str(index)
        directory.mkdir()
        source = FIXTURE_DIR / f"{name}.json"
        if source.exists():
            shutil.copy(source, directory / f"{name}.json")
        dirs.append(directory)
    chunks = [situations[index::SHARDS] for index in range(SHARDS)]
    with ThreadPoolExecutor(max_workers=SHARDS) as pool:
        futures = [
            pool.submit(_shard, name, chunk, directory)
            for chunk, directory in zip(chunks, dirs, strict=True)
        ]
        live = sum(future.result() for future in futures)
    _merge(name, dirs)
    shutil.rmtree(root, ignore_errors=True)
    return live


def record_control_room() -> int:
    """The verdicts the Control Room itself shows, one per fleet size and price day."""
    live = 0
    for day, size in product(PRICE_DAYS, FLEET_SIZES):
        engine = ControlRoomEngine(price_trace=load_price_scenario(day), fleet_size=size)
        incident = engine.trigger_device_failure()
        for attempt in range(ATTEMPTS):
            try:
                _, response, _ = judge_incident(engine, incident)
            except Exception as error:  # noqa: BLE001 - transient gateway errors
                print(f"  {day}/{size}: {type(error).__name__}, retrying")
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            live += int(response.source is Source.LIVE)
            print(f"  {day}/{size}: {response.source.value}")
            break
    return live


def main() -> int:
    drills = [drill_situation(path) for path in sorted(HOLDOUT_DIR.glob("*.yaml"))]
    print(f"held-out drills ({len(drills)}):")
    live = record(HOLDOUT_FIXTURE, drills)

    episodes = [episode.situation for episode in overrides.episodes()]
    print(f"simulated override episodes ({len(episodes)}):")
    live += record(OVERRIDE_FIXTURE, episodes)

    print("Control Room screens:")
    live += record_control_room()

    print(f"recorded {live} live answers into {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
