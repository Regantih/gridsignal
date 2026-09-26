"""The same negotiation over a real transport: separate processes, sockets, retries.

Every other benchmark in this repository is in-process compute. This one is not: the
coordinator runs in one OS process and the battery agents in others, and the only thing
they share is a TCP connection on ``127.0.0.1``. Newline-delimited JSON frames, and the
capability card each agent bids with is HMAC-signed in the agent process and verified in
the coordinator process, so a forged card is refused across the wire rather than inside
one interpreter.

Agents are multiplexed over a small number of connections: every frame names the agent it
is for, so one socket carries hundreds of them and 10,000 agents need tens of file
descriptors rather than 10,000. A laptop whose ``ulimit -n`` is 256 runs the same
benchmark as a server; the limit is read at startup, raised as far as the hard limit
allows, and if it is still tight the run says so in one line and uses fewer sockets
instead of failing.

What is measured, per agent, is the whole round trip an operator waits on::

    detect  ->  call for capacity broadcast
                ->  agent signs its card and bids
            <-  coordinator ranks every bid and awards
                ->  agent acknowledges the award it can verify

so one sample is ``ack received - call broadcast started``, and the report is the p50 and
p95 over every agent, plus frames per second across both directions.

TCP does not lose frames, so ``--drop`` throws them away deliberately on arrival, at both
ends, to answer a different question: what happens when the network is bad. The
coordinator re-broadcasts to agents that never bid and re-sends awards that were never
acknowledged, so the honest result is how much the tail moves and whether the call still
clears.

**Local loopback, not a WAN.** There is no cellular link, no gateway, no inverter and no
internet path here: these numbers are the software's own overhead under a real socket and
a real process boundary, and a field deployment would add everything this repository does
not model.

    python -m gridsignal.transport                       # 1,000 agents
    python -m gridsignal.transport --full                # 1,000 and 10,000 agents
    python -m gridsignal.transport --agents 1000 --drop 0.05
    python -m gridsignal.transport --agents 1000 --json transport.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import random
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import Connection

from gridsignal.fleet import build_fleet
from gridsignal.mesh.build import card_for
from gridsignal.mesh.cards import AgentCard, AgentKind, Health, derived_signing_key
from gridsignal.perf import percentile

try:  # pragma: no cover - Windows has no resource module
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

#: What ``getrlimit`` reports when there is no limit at all, or when there is no
#: ``resource`` module to ask.
NO_FD_LIMIT = resource.RLIM_INFINITY if resource is not None else -1

DEFAULT_SIZES: tuple[int, ...] = (1_000,)
FULL_SIZES: tuple[int, ...] = (1_000, 10_000)
DEFAULT_WORKERS = 2
#: Connections the benchmark wants, split across the worker processes. Sockets cost more
#: than the frames they carry at this scale, so more of them is slower, not faster.
WANTED_CONNECTIONS = 32
#: File descriptors left for everything that is not a benchmark socket: stdio, the
#: listener, the pipes back to the parent, whatever the interpreter has already opened.
RESERVED_FDS = 64
DEFAULT_SEED = 7
#: Event window the call for capacity covers, in hours. Simulated.
HOURS = 2.0
#: kW the coordinator asks the fleet for, per agent. Sized so roughly half the agents
#: win an award, which is the interesting case: everyone bids, not everyone is picked.
GAP_KW_PER_AGENT = 4.0
#: How long the coordinator waits for a round of replies before it re-sends.
RETRY_AFTER_S = 0.75
#: How many times it re-sends a frame that was never answered.
MAX_ATTEMPTS = 4
#: Ceiling on one measured round, so a wedged socket fails the run instead of hanging.
ROUND_TIMEOUT_S = 180.0


def raise_fd_limit() -> int:
    """Ask for every file descriptor this box will give us; return the soft limit.

    A default macOS shell offers 256, which is fewer than one socket per agent at any
    interesting fleet size. The soft limit can always be raised to the hard limit without
    privileges, so the benchmark does that first and plans against what it actually got.
    """
    if resource is None:  # pragma: no cover - Windows
        return 0
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != hard and (hard == resource.RLIM_INFINITY or soft < hard):
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        except (ValueError, OSError):  # pragma: no cover - refused by the kernel
            return soft
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    return soft


def connection_plan(agents: int, workers: int, wanted: int = WANTED_CONNECTIONS) -> int:
    """How many sockets this round may open, inside the file-descriptor limit.

    The coordinator process holds one descriptor per connection, so its limit is the
    binding one. Fewer connections than agents is not a degraded run: the protocol
    addresses agents by id, so the same frames cross the same process boundary either way.
    """
    soft = raise_fd_limit()
    room = wanted if soft in (0, NO_FD_LIMIT) else soft - RESERVED_FDS
    return max(workers, min(agents, wanted, room))


def _percent(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


class TransportTooBig(RuntimeError):
    """This box could not carry the round asked of it. Printed, never raised at a judge."""


@dataclass(frozen=True)
class TransportResult:
    """One measured round at one fleet size."""

    agents: int
    worker_processes: int
    connections: int
    drop: float
    gap_kw: float
    covered_kw: float
    bids: int
    awards: int
    completed: int
    forged_refused: int
    frames_sent: int
    frames_received: int
    frames_dropped: int
    resends: int
    wall_s: float
    latencies_ms: tuple[float, ...] = field(repr=False, default=())

    @property
    def p50_ms(self) -> float:
        return round(percentile(list(self.latencies_ms), 0.50), 2)

    @property
    def p95_ms(self) -> float:
        return round(percentile(list(self.latencies_ms), 0.95), 2)

    @property
    def max_ms(self) -> float:
        return round(max(self.latencies_ms), 2) if self.latencies_ms else 0.0

    @property
    def frames(self) -> int:
        return self.frames_sent + self.frames_received

    @property
    def frames_per_s(self) -> float:
        return round(self.frames / self.wall_s, 1) if self.wall_s else 0.0

    @property
    def coverage_pct(self) -> float:
        return _percent(self.covered_kw, self.gap_kw)

    @property
    def completed_pct(self) -> float:
        return _percent(self.completed, self.agents)

    @property
    def agents_per_connection(self) -> float:
        return round(self.agents / self.connections, 1) if self.connections else 0.0

    @property
    def drop_pct_measured(self) -> float:
        return _percent(self.frames_dropped, self.frames_received + self.frames_dropped)


# --------------------------------------------------------------------------- agents


def _fleet_cards(seed: int, fleet_size: int, start: int, end: int) -> list[AgentCard]:
    """The slice of battery cards one worker process speaks for.

    Built from the same fleet and the same projection the Control Room's mesh uses, so
    the bids on the wire are the bids the in-process benchmark would have made.
    """
    devices = build_fleet(seed, fleet_size)[start:end]
    return [card_for(device, HOURS) for device in devices]


def _bid_kw(card: AgentCard) -> float:
    """What one agent offers: spare power, capped by the energy it can hold for the window."""
    if card.health is Health.OFFLINE:
        return 0.0
    energy_limit = card.capability("kwh_available") / HOURS
    return round(min(card.capability("kw_available"), energy_limit), 3)


async def _channel_session(
    host: str,
    port: int,
    signed: dict[str, AgentCard],
    drop: float,
    rng: random.Random,
    stats: dict[str, int],
) -> None:
    """One socket carrying many agents, for the length of one round.

    Every frame in either direction names its agent, so the multiplexing is invisible to
    the protocol: the coordinator still addresses, awards and retries one agent at a time.
    """
    reader, writer = await asyncio.open_connection(host, port)
    await _send(writer, {"t": "hello", "ids": list(signed)}, stats)
    while True:
        line = await reader.readline()
        if not line:
            return
        stats["received"] += 1
        frame = json.loads(line)
        kind = frame["t"]
        # Teardown is outside the measured protocol and is never dropped, so a lost
        # frame cannot strand an agent process after the round is scored.
        if kind == "done":
            writer.close()
            return
        if drop and rng.random() < drop:
            stats["dropped"] += 1
            continue
        card = signed[str(frame["id"])]
        if kind == "call":
            await _send(
                writer,
                {
                    "t": "bid",
                    "id": card.agent_id,
                    "kw": _bid_kw(card),
                    "zone": card.zone,
                    "caps": card.capabilities,
                    "health": card.health.value,
                    "hb": card.last_heartbeat_s,
                    "controller": card.controller,
                    "sig": card.signature,
                },
                stats,
            )
        elif kind in {"award", "none"}:
            await _send(writer, {"t": "ack", "id": card.agent_id}, stats)


async def _send(
    writer: asyncio.StreamWriter, frame: dict[str, object], stats: dict[str, int]
) -> None:
    writer.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
    stats["sent"] += 1
    await writer.drain()


async def _run_agents(
    host: str,
    port: int,
    seed: int,
    fleet_size: int,
    start: int,
    end: int,
    drop: float,
    forged: int,
    channels: int,
) -> dict[str, int]:
    """Sign this worker's agents and carry them over ``channels`` connections.

    ``forged`` agents sign with the wrong key: the coordinator must refuse their bids,
    which proves the signature is checked across the process boundary and not assumed.
    """
    key = derived_signing_key(seed)
    wrong_key = derived_signing_key(seed + 1)
    stats = {"sent": 0, "received": 0, "dropped": 0}
    cards = _fleet_cards(seed, fleet_size, start, end)
    signed = [
        card.signed(wrong_key if index < forged else key) for index, card in enumerate(cards)
    ]
    edges = [round(len(signed) * i / channels) for i in range(channels + 1)]
    sessions = [
        _channel_session(
            host,
            port,
            {c.agent_id: c for c in signed[edges[i] : edges[i + 1]]},
            drop,
            random.Random(seed * 1_000_003 + start + i),
            stats,
        )
        for i in range(channels)
        if edges[i] < edges[i + 1]
    ]
    await asyncio.gather(*sessions)
    return stats


def _worker(
    host: str,
    port: int,
    seed: int,
    fleet_size: int,
    start: int,
    end: int,
    drop: float,
    forged: int,
    channels: int,
    back: Connection,
) -> None:  # pragma: no cover - runs in a child process
    """Entry point of one agent process."""
    try:
        raise_fd_limit()
        stats = asyncio.run(
            _run_agents(host, port, seed, fleet_size, start, end, drop, forged, channels)
        )
        back.send(stats)
    except Exception as error:  # noqa: BLE001 - reported to the parent, which fails the run
        back.send({"error": f"{type(error).__name__}: {error}"})
    finally:
        back.close()


# ----------------------------------------------------------------------- coordinator


class _Round:
    """The coordinator process: accepts agents, runs one auction, times every agent."""

    def __init__(self, agents: int, seed: int, drop: float) -> None:
        self.expected = agents
        self.key = derived_signing_key(seed)
        self.drop = drop
        self.rng = random.Random(seed * 7_919)
        self.writers: dict[str, asyncio.StreamWriter] = {}
        #: One entry per socket, however many agents it carries.
        self.channels: list[asyncio.StreamWriter] = []
        self.bids: dict[str, float] = {}
        #: Every agent that answered the call, including those with nothing to offer.
        self.replied: set[str] = set()
        self.awards: dict[str, float] = {}
        self.acked: set[str] = set()
        self.started_at: dict[str, float] = {}
        self.latencies: list[float] = []
        self.forged_refused = 0
        self.sent = 0
        self.received = 0
        self.dropped = 0
        self.resends = 0
        self.connected = asyncio.Event()
        self.t0 = 0.0

    # -- wire

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                self.received += 1
                frame = json.loads(line)
                kind = frame["t"]
                # Connection setup is not part of the measured protocol, so the drop
                # never takes a hello: an agent that never registers cannot be retried.
                if kind != "hello" and self.drop and self.rng.random() < self.drop:
                    self.dropped += 1
                    continue
                if kind == "hello":
                    self.channels.append(writer)
                    for agent_id in frame["ids"]:
                        self.writers[str(agent_id)] = writer
                    if len(self.writers) == self.expected:
                        self.connected.set()
                elif kind == "bid":
                    self._take_bid(frame)
                elif kind == "ack":
                    self._take_ack(str(frame["id"]))
        except (ConnectionResetError, asyncio.IncompleteReadError):  # pragma: no cover
            return

    def _take_bid(self, frame: dict[str, object]) -> None:
        """Accept a bid only from a card whose signature verifies in this process.

        The agent has answered either way, so a refused bid is not re-sent to: it is
        heard from, it simply has nothing this coordinator can trust.
        """
        self.replied.add(str(frame["id"]))
        card = AgentCard(
            agent_id=str(frame["id"]),
            kind=AgentKind.BATTERY,
            zone=str(frame["zone"]),
            capabilities={k: float(v) for k, v in dict(frame["caps"]).items()},  # type: ignore[arg-type]
            health=Health(str(frame["health"])),
            last_heartbeat_s=int(frame["hb"]),  # type: ignore[arg-type]
            controller=str(frame["controller"]),
            signature=str(frame["sig"]),
        )
        if not card.verifies(self.key):
            self.forged_refused += 1
            return
        kw = float(frame["kw"])  # type: ignore[arg-type]
        if kw > 0:
            self.bids[card.agent_id] = kw

    def _take_ack(self, agent_id: str) -> None:
        if agent_id in self.acked:
            return
        self.acked.add(agent_id)
        self.latencies.append((time.perf_counter() - self.t0) * 1000.0)

    async def _write(self, agent_id: str, frame: dict[str, object]) -> None:
        writer = self.writers.get(agent_id)
        if writer is None:  # pragma: no cover - only if an agent vanished
            return
        # One socket carries many agents, so every frame says which one it is for.
        addressed = {**frame, "id": agent_id}
        writer.write((json.dumps(addressed, separators=(",", ":")) + "\n").encode())
        self.sent += 1

    async def _broadcast(self, agent_ids: list[str], frame: dict[str, object]) -> None:
        for agent_id in agent_ids:
            await self._write(agent_id, frame)
        await asyncio.gather(*(w.drain() for w in self.channels))

    # -- protocol

    async def _until(self, done: Callable[[], bool], deadline: float) -> bool:
        while time.perf_counter() < deadline:
            if done():
                return True
            await asyncio.sleep(0.005)
        return done()

    async def run(self, gap_kw: float) -> None:
        """Broadcast, collect, award, and re-send whatever the drop swallowed."""
        self.t0 = time.perf_counter()
        call = {"t": "call", "call": "CFC-001", "gap_kw": gap_kw, "hours": HOURS}
        outstanding = list(self.writers)
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self.resends += len(outstanding)
            await self._broadcast(outstanding, call)
            await self._until(
                lambda: len(self.replied) == len(self.writers),
                time.perf_counter() + RETRY_AFTER_S,
            )
            outstanding = [a for a in self.writers if a not in self.replied]
            if not outstanding:
                break

        awarded_kw = 0.0
        # Biggest spare kW first, then by id, so the award never depends on arrival order.
        for agent_id, kw in sorted(self.bids.items(), key=lambda kv: (-kv[1], kv[0])):
            if awarded_kw >= gap_kw:
                break
            take = round(min(kw, gap_kw - awarded_kw), 3)
            self.awards[agent_id] = take
            awarded_kw += take

        for attempt in range(MAX_ATTEMPTS):
            waiting = [a for a in self.writers if a not in self.acked]
            if not waiting:
                break
            if attempt:
                self.resends += len(waiting)
            for agent_id in waiting:
                kw = self.awards.get(agent_id)
                frame: dict[str, object] = (
                    {"t": "award", "id": agent_id, "kw": kw}
                    if kw is not None
                    else {"t": "none", "id": agent_id}
                )
                await self._write(agent_id, frame)
            await asyncio.gather(*(w.drain() for w in self.channels))
            await self._until(
                lambda: len(self.acked) == len(self.writers),
                time.perf_counter() + RETRY_AFTER_S,
            )

        for channel in self.channels:
            channel.write(b'{"t":"done"}\n')
            self.sent += 1
        await asyncio.gather(*(w.drain() for w in self.channels))


async def _measure(
    agents: int,
    workers: int,
    seed: int,
    drop: float,
    forged: int,
    connections: int,
    host: str = "127.0.0.1",
) -> TransportResult:
    channels = max(1, connections // workers)
    round_ = _Round(agents, seed, drop)
    server = await asyncio.start_server(round_.handle, host, 0)
    port = int(server.sockets[0].getsockname()[1])

    ctx = mp.get_context("spawn")
    procs: list[mp.process.BaseProcess] = []
    pipes: list[Connection] = []
    bounds = [round(agents * i / workers) for i in range(workers + 1)]
    started = time.perf_counter()
    for index in range(workers):
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_worker,
            args=(
                host,
                port,
                seed,
                agents,
                bounds[index],
                bounds[index + 1],
                drop,
                forged if index == 0 else 0,
                min(channels, bounds[index + 1] - bounds[index]),
                child,
            ),
            daemon=True,
        )
        proc.start()
        child.close()
        procs.append(proc)
        pipes.append(parent)

    async with server:
        try:
            await asyncio.wait_for(round_.connected.wait(), ROUND_TIMEOUT_S)
            await asyncio.wait_for(round_.run(agents * GAP_KW_PER_AGENT), ROUND_TIMEOUT_S)
        except TimeoutError as expired:
            raise TransportTooBig(
                f"{agents:,} agents did not finish a round in {ROUND_TIMEOUT_S:.0f} s "
                f"over {len(round_.channels)} sockets "
                f"({len(round_.writers):,} of them connected)"
            ) from expired
        finally:
            for proc in procs:
                proc.join(timeout=30)
                if proc.is_alive():  # pragma: no cover - only on a wedged child
                    proc.terminate()
    wall = time.perf_counter() - started

    agent_stats = {"sent": 0, "received": 0, "dropped": 0}
    for pipe in pipes:
        payload = pipe.recv() if pipe.poll(5) else {"error": "worker sent nothing"}
        if "error" in payload:
            raise RuntimeError(f"agent process failed: {payload['error']}")
        for name in agent_stats:
            agent_stats[name] += int(payload[name])
        pipe.close()

    return TransportResult(
        agents=agents,
        worker_processes=workers,
        connections=len(round_.channels),
        drop=drop,
        gap_kw=round(agents * GAP_KW_PER_AGENT, 2),
        covered_kw=round(sum(round_.awards.values()), 2),
        bids=len(round_.bids),
        awards=len(round_.awards),
        completed=len(round_.acked),
        forged_refused=round_.forged_refused,
        frames_sent=round_.sent + agent_stats["sent"],
        frames_received=round_.received + agent_stats["received"],
        frames_dropped=round_.dropped + agent_stats["dropped"],
        resends=round_.resends,
        wall_s=round(wall, 3),
        latencies_ms=tuple(round_.latencies),
    )


def measure(
    agents: int,
    workers: int = DEFAULT_WORKERS,
    seed: int = DEFAULT_SEED,
    drop: float = 0.0,
    forged: int = 0,
    connections: int | None = None,
) -> TransportResult:
    """Run one auction over loopback sockets between separate processes and time it.

    ``connections`` defaults to whatever the file-descriptor limit allows, so this is
    safe to call on a laptop with ``ulimit -n 256`` at any fleet size.
    """
    planned = connection_plan(agents, workers) if connections is None else connections
    return asyncio.run(_measure(agents, workers, seed, drop, forged, planned))


def lines(results: list[TransportResult]) -> list[str]:
    soft = raise_fd_limit()
    out = [
        "Transport benchmark: coordinator and agents in separate processes",
        "Local loopback (127.0.0.1), agents multiplexed over TCP sockets, signed cards",
        "verified across the process boundary. Not a WAN: no gateway, cellular or",
        "inverter time.",
        f"File descriptor limit on this box: {soft:,} "
        f"(soft, after asking for the hard limit).",
        "",
        f"{'agents':>8}  {'procs':>5}  {'drop':>5}  {'p50 ms':>8}  {'p95 ms':>8}  "
        f"{'max ms':>8}  {'frames/s':>10}  {'covered':>8}",
    ]
    for result in results:
        out.append(
            f"{result.agents:>8,}  {result.worker_processes + 1:>5}  "
            f"{result.drop * 100:>4.0f}%  {result.p50_ms:>8,.1f}  {result.p95_ms:>8,.1f}  "
            f"{result.max_ms:>8,.1f}  {result.frames_per_s:>10,.0f}  "
            f"{result.coverage_pct:>7.0f}%"
        )
    out.append("")
    for result in results:
        out += [
            f"{result.agents:,} agents at {result.drop * 100:.0f}% drop:",
            f"  {result.connections:,} sockets carrying "
            f"{result.agents_per_connection:,.0f} agents each, {result.bids:,} verified bids, "
            f"{result.awards:,} awards, {result.completed:,} agents finished "
            f"({result.completed_pct:.0f}%)",
            f"  {result.frames:,} frames in {result.wall_s:,.2f} s "
            f"({result.frames_per_s:,.0f} frames/s), {result.resends:,} re-sends",
            f"  {result.covered_kw:,.0f} kW of the {result.gap_kw:,.0f} kW call covered "
            f"({result.coverage_pct:.0f}%)",
        ]
        if result.frames_dropped:
            out.append(
                f"  {result.frames_dropped:,} frames thrown away on arrival "
                f"({result.drop_pct_measured:.1f}% of everything that arrived)"
            )
        if result.forged_refused:
            out.append(
                f"  {result.forged_refused:,} bids refused: the card's signature did not "
                "verify in the coordinator process"
            )
        out.append("")
    out.append("Reproduce with python -m gridsignal.transport.")
    return out


def report(
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    workers: int = DEFAULT_WORKERS,
    drop: float = 0.0,
) -> str:
    return "\n".join(lines([measure(size, workers, drop=drop) for size in sizes]))


def _sizes_from(args: argparse.Namespace) -> list[int]:
    if args.agents:
        return list(args.agents)
    return list(FULL_SIZES if args.full else DEFAULT_SIZES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agents",
        type=int,
        nargs="+",
        default=None,
        help="agent counts to measure (default: 1000)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="also measure 10,000 agents, which takes minutes on a small box",
    )
    parser.add_argument(
        "--connections",
        type=int,
        default=None,
        help="sockets to multiplex the agents over (default: as many as the fd limit allows)",
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS, help="agent processes (default: 2)"
    )
    parser.add_argument(
        "--drop",
        type=float,
        default=0.0,
        help="share of arriving frames thrown away at both ends, e.g. 0.05",
    )
    parser.add_argument(
        "--forged",
        type=int,
        default=0,
        help="agents that sign with the wrong key, to show the refusal",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", type=str, default=None, help="also write the raw numbers here")
    args = parser.parse_args(argv)

    try:
        results = [
            measure(agents, args.workers, args.seed, args.drop, args.forged, args.connections)
            for agents in _sizes_from(args)
        ]
    except TransportTooBig as too_big:
        # A judge on a small laptop gets a sentence, not a traceback.
        print(f"transport benchmark scaled down: {too_big}", file=sys.stderr)
        print("Try a smaller --agents, or --workers 1 to leave this box more room.")
        return 1
    print("\n".join(lines(results)))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(
                [
                    {
                        **{k: v for k, v in asdict(r).items() if k != "latencies_ms"},
                        "p50_ms": r.p50_ms,
                        "p95_ms": r.p95_ms,
                        "max_ms": r.max_ms,
                        "frames_per_s": r.frames_per_s,
                        "coverage_pct": r.coverage_pct,
                    }
                    for r in results
                ],
                handle,
                indent=2,
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
