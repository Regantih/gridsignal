"""End-to-end run: ingest -> detect -> forecast -> signals -> backtest."""

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="Run the GridSignal pipeline")
    p.add_argument("--start", default="2026-08-01")
    p.add_argument("--end", default="2026-09-24")
    args = p.parse_args()
    print(f"GridSignal pipeline: {args.start} to {args.end} (steps not implemented yet)")


if __name__ == "__main__":
    main()
