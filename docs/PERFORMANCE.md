# Speed and scale

One command reproduces everything on this page:

```bash
python -m gridsignal.perf --before        # 10,000 and 100,000 agents, before and after
```

Add `--sizes 10000`, `--repeats 9` or `--json perf.json` to narrow or widen it. No key, no
network, no device round trips: every figure below is in-process compute on a **simulated**
fleet priced from real cached ERCOT settlement prices (LZ_HOUSTON, 2023-09-06 scarcity day).
It measures the software, not a field deployment: real latency would add gateway, cellular
and inverter response time that this repository does not model.

## What is timed

| Stage | Work |
| --- | --- |
| build fleet | construct every simulated device and commit the dispatch plan |
| detect incident | raise an incident from the current fleet snapshot |
| recover after approval | recompute the whole allocation once a human approves |
| publish signed cards | project every device into a capability card, HMAC-sign it, verify and register it |
| heartbeat sweep | every agent checks in and re-signs its card |
| negotiate one call | one call for capacity: collect bids, rank, award, human approval |

Each stage is repeated (default five times) and reported as p50 and p95. Memory is the peak
Python heap (`tracemalloc`) for one whole pass at that scale.

## Results

Measured on the development machine (2 vCPU x86_64, 7 GiB RAM, CPython 3.11), 5 repeats.
"Before" is the code as it stood at commit `ed4fec2`, kept in `src/gridsignal/perf_before.py`
and swapped back in during the same run, so both columns come off the same machine.

### 10,000 batteries (10,053 agents, peak heap 15.0 MiB)

| Stage | before p50 ms | after p50 ms | after p95 ms | speedup | throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| build fleet | 177.9 | 99.8 | 106.7 | 1.78x | 100,241 devices/s |
| detect incident | 1.2 | 1.2 | 1.2 | 0.98x | 8,403,361 devices/s |
| recover after approval | 115.4 | 35.9 | 36.2 | 3.21x | 278,396 devices/s |
| publish signed cards | 290.1 | 169.0 | 188.0 | 1.72x | 59,496 cards/s |
| heartbeat sweep | 126.2 | 69.5 | 70.4 | 1.82x | 144,689 cards/s |
| negotiate one call | 27.1 | 25.9 | 27.5 | 1.04x | 302,005 bids/s |
| **end to end (p50 sum)** | **737.7** | **401.3** | | **1.84x** | 7,834 bids, 100% of the call covered |

### 100,000 batteries (100,053 agents, peak heap 152.7 MiB)

| Stage | before p50 ms | after p50 ms | after p95 ms | speedup | throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| build fleet | 1,611.5 | 828.9 | 840.6 | 1.94x | 120,639 devices/s |
| detect incident | 8.1 | 8.0 | 27.6 | 1.01x | 12,453,300 devices/s |
| recover after approval | 1,144.8 | 364.9 | 367.9 | 3.14x | 274,040 devices/s |
| publish signed cards | 2,955.2 | 1,785.8 | 1,832.1 | 1.65x | 56,026 cards/s |
| heartbeat sweep | 1,270.5 | 688.5 | 691.5 | 1.85x | 145,324 cards/s |
| negotiate one call | 306.1 | 302.2 | 306.6 | 1.01x | 259,247 bids/s |
| **end to end (p50 sum)** | **7,296.2** | **3,978.3** | | **1.83x** | 78,334 bids, 100% of the call covered |

Every stage is linear in fleet size: 10x the agents costs about 10x the time (recovery
35.9 ms to 364.9 ms, heartbeat 69.5 ms to 688.5 ms) and about 10x the heap (15.0 MiB to
152.7 MiB, roughly 1.5 KiB per agent). Nothing here is quadratic.

## The hot paths, and what was done to them

Profiled with `cProfile` over card registration, the heartbeat sweep and the post-approval
reallocation at 100,000 agents. Three things dominated:

1. **`json.encoder.iterencode`, 1 call per signature.** Every card was serialised to
   canonical JSON to be signed, and again to be verified — so twice per card per heartbeat.
   Replaced with a fixed-order join on ASCII record and unit separators
   (`src/gridsignal/mesh/cards.py`). The separators are control characters, so they cannot
   appear in an agent id, zone, controller or capability name and no value can be shifted
   into a neighbouring field without changing the signature. `tests/test_perf.py` asserts
   every field still signs, and that the old and new serialisations separate the same cards.
2. **`dataclasses.replace`, once per card per heartbeat.** `replace()` re-runs field
   introspection and `__init__` validation on a frozen dataclass; at 100,000 agents that is
   the single largest allocation cost in the sweep. `AgentCard.signed` now constructs the
   card field by field.
3. **Headroom recomputed three times per device per allocation.** `_allocate_dispatch`
   called `_headroom_kw` to filter the pool, again to total it, and again to split the
   target; each call re-derived the event window from the clock and the member's reserve.
   The allocation now measures each device once into a dict, and the event window is cached
   against the simulated clock (`src/gridsignal/control_room/engine.py`). The reserve is
   still held back inside `discharge_headroom_kw`, unchanged: this is a caching change, and
   `tests/test_perf.py` asserts every device is assigned the same kW as before.

`negotiate one call` and `detect incident` were already cheap and were left alone; there is
no honest speedup to claim there.

## The guard in CI

`tests/test_perf.py` runs on every push:

- **Absolute:** one whole pass at 2,000 devices must finish inside 6 s (p50 of 3 repeats).
  Loose on purpose — shared runners vary several-fold — so it fires on an accidental O(n^2),
  not on a noisy neighbour.
- **Relative:** the same stages are re-timed in the same process against
  `gridsignal.perf_before`, and signing, heartbeat and reallocation must each stay at least
  1.15x faster than the code they replaced. Being a ratio measured on the same machine in
  the same run, it does not care how fast the runner is — it only fails if an optimisation
  is undone.

CI also runs `python -m gridsignal.perf --sizes 10000 --repeats 3 --json perf.json` and
uploads `perf.json`, so a run's numbers can be compared with this page.

## Limits

- Simulated fleet, simulated agents, in-process messaging. No radio, no gateway, no inverter.
- Single process, single core. The mesh is deliberately not parallelised: determinism and a
  replayable trace matter more here than another 2x.
- Numbers move with the machine. Compare the ratios, or re-run the one command.
