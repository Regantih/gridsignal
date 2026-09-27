"""Record real Jev answers for every bundled scenario so judges can run without a key.

    AI_GATEWAY_API_KEY=... python -m gridsignal.jev.record

Needs ``AI_GATEWAY_API_KEY`` or ``TYPESAFE_API_KEY`` in the environment; the key itself
is never written. Use ``--refresh`` to re-ask questions that are already recorded.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from gridsignal.jev import rules
from gridsignal.jev.client import FIXTURE_DIR, JevClient, transport_from_env
from gridsignal.mesh.scenarios import available_scenarios, load_scenario


def record(paths: list[Path] | None = None, refresh: bool = False) -> list[Path]:
    from gridsignal.simulate import run_scenario

    if transport_from_env() is None:
        raise SystemExit("set AI_GATEWAY_API_KEY or TYPESAFE_API_KEY to record Jev answers")
    written: list[Path] = []
    for path in paths if paths is not None else available_scenarios():
        scenario = load_scenario(path)
        client = JevClient.for_scenario(scenario.slug, refresh=refresh, fallback=rules.answers)
        result = run_scenario(scenario, jev=client)
        written.append(client.fixtures.path if client.fixtures else FIXTURE_DIR)
        print(f"{scenario.slug}: {result.jev_label}, {len(client.fixtures or [])} recorded")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-ask already recorded questions")
    args = parser.parse_args(argv)
    record(refresh=args.refresh)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
