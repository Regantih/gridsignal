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

## Over a real transport: separate processes, sockets, packet loss

Everything above is in-process compute. This section is not: the coordinator runs in one OS
process and the agents in two more, and the only thing they share is a TCP connection on
`127.0.0.1`. Newline-delimited JSON frames, and each capability card is HMAC-signed in the
agent process and verified in the coordinator process, so a forged card is refused across
the wire rather than inside one interpreter.

Agents are **multiplexed**: every frame names the agent it is for, so 32 sockets carry
10,000 agents (312 each) instead of needing 10,000 file descriptors. The soft
`RLIMIT_NOFILE` is raised to the hard limit at startup and the socket count is planned
against whatever the box actually allows, so a laptop with `ulimit -n 256` runs the same
benchmark as a server — fewer sockets if it must, a printed line if the round still cannot
fit, never a traceback. `tests/test_transport.py` runs the benchmark in a child process
with both `RLIMIT_NOFILE` limits lowered to 256 and asserts a clean 100% result.

```bash
python -m gridsignal.transport                        # 1,000 agents (the default)
python -m gridsignal.transport --full                 # also 10,000 agents
python -m gridsignal.transport --agents 1000 --drop 0.05
python -m gridsignal.transport --agents 1000 --forged 5
```

One sample is the whole round trip an operator waits on: call for capacity broadcast ->
signed bid -> ranked award -> the agent's acknowledgement.

Measured on a 2 vCPU Linux box with `ulimit -n 256` (the tight case), 32 sockets:

| agents | drop | p50 ms | p95 ms | max ms | frames/s | re-sends | covered | finished |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0% | 27.9 | 28.9 | 29.0 | 18,431 | 0 | 100% | 1,000 of 1,000 |
| 10,000 | 0% | 222.6 | 231.9 | 232.9 | 109,315 | 0 | 100% | 10,000 of 10,000 |
| 1,000 | 5% | 1,536.6 | 2,291.3 | 3,046.1 | 1,760 | 195 | 100% | 999 of 1,000 |
| 10,000 | 5% | 3,177.6 | 3,939.2 | 5,446.6 | 14,268 | 2,086 | 100% | 10,000 of 10,000 |

Wall clock for the commands themselves on that box: **0.81 s** for the default 1,000-agent
run and **1.48 s** for `--full` (1,000 and 10,000), both at `ulimit -n 256`.

Read the ratios, not the milliseconds: they are this box (2 vCPU), and detect-to-award over
loopback at 10,000 agents costs 222.6 ms against 25.9 ms for the same negotiation in one
process, about 9x — the transport, not the ranking, is the bill. Multiplexing is most of
that gap: one socket per agent cost 816.9 ms at 10,000 and could not open at all under a
laptop's descriptor limit.

Under 5% loss, thrown-away frames are re-sent: the call still clears 100% at both sizes, and
what it costs is the tail (p95 28.9 ms -> 2,291.3 ms at 1,000 agents). The honest failure is
in the last column — one agent in the lossy 1,000-agent run had four acknowledgements dropped
and was never confirmed, so the coordinator ends the round believing it is uncommitted. A
field system needs a durable re-send queue, not four attempts.

**Local loopback, not a WAN.** No cellular link, no gateway, no inverter, no internet path:
these are the software's own costs under a real socket and a real process boundary.

## Limits

- Simulated fleet, simulated agents. The transport benchmark above is local loopback only.
- Everything outside that section is in-process messaging. No radio, no gateway, no inverter.
- The product itself runs the mesh in one process, deliberately not parallelised:
  determinism and a replayable trace matter more here than another 2x.
- Four re-send attempts, in memory. About one agent per lossy run goes unconfirmed.
- Numbers move with the machine. Compare the ratios, or re-run the one command.
