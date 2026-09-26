"""Full-fleet scarcity replay: 10,000 batteries, three faults at the price peak.

One scene, one question: on the real ERCOT scarcity day bundled in ``data/processed``, what
does orchestration protect when the fleet is hit at the worst possible minute? Three faults
land inside the peak window — a gateway ring goes dark, a wave of homes stops reporting, and
agents whose cards do not verify try to bid capacity they do not have — and the run measures
the dollars that survive against the counterfactual where nobody reassigns anything.

Everything except the prices is simulated: the devices, the faults and the spoofing. The prices
are real cached ERCOT settlement point prices.

    python -m gridsignal.replay                 # 10,000 devices on the scarcity day
    python -m gridsignal.replay --devices 1000
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import OWNERS
from gridsignal.control_room.models import Role
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.mesh.build import card_for, register_fleet
from gridsignal.mesh.registry import AgentRegistry
from gridsignal.prices import energy_value_usd, load_scenario

FLEET_SIZE = 10_000
#: Share of the fleet whose telemetry goes stale in the wave. Simulated.
STALE_SHARE = 0.03
#: Agents that re-publish an edited card at the peak, claiming capacity they do not have.
SPOOFED_AGENTS = 12
#: The extra kW each spoofed card claims over what its battery really has. Simulated.
SPOOFED_CLAIM_KW = 40.0


@dataclass(frozen=True)
class Fault:
    """One injected fault and the committed kW it took out of the commitment."""

    name: str
    detail: str
    kw: float


@dataclass(frozen=True)
class ReplayResult:
    """What the replay measured. Dollars are priced at the real settlement price."""

    fleet_size: int
    dispatchable: int
    date: str
    location: str
    price_mwh: float
    window_hours: float
    target_kw: float
    committed_kw_before: float
    committed_kw_after: float
    faults: tuple[Fault, ...]
    kw_lost: float
    dollars_at_risk: float
    dollars_recovered: float
    rejected_cards: int
    phantom_kw_rejected: float
    fault_minutes: float
    runtime_s: float

    @property
    def dollars_unprotected(self) -> float:
        """What orchestration did not get back: the honest remainder."""
        return round(self.dollars_at_risk - self.dollars_recovered, 2)

    @property
    def protected_usd_per_fault_minute(self) -> float:
        """Dollars recovered per simulated minute the fleet spent degraded."""
        if self.fault_minutes <= 0:
            return 0.0
        return round(self.dollars_recovered / self.fault_minutes, 2)

    @property
    def recovered_share(self) -> float:
        if self.dollars_at_risk <= 0:
            return 0.0
        return self.dollars_recovered / self.dollars_at_risk


def _spoof(engine: ControlRoomEngine, count: int) -> tuple[int, float]:
    """Re-publish edited cards for ``count`` batteries and see them refused.

    The registry holds the signing key, so a card edited after signing fails its HMAC and
    never reaches the coordinator. Returns the rejections and the kW they falsely claimed.
    """
    registry = AgentRegistry()
    hours = max(engine.remaining_hours(), 0.25)
    register_fleet(registry, engine.devices, hours, reserve_fraction=engine.reserve_fraction)
    liars = [d for d in engine.mine if d.is_dispatchable][:count]
    phantom = 0.0
    for device in liars:
        honest = card_for(device, hours, engine.reserve_fraction)
        registry.register(registry.sign(honest))
        registry.tamper(
            device.device_id,
            kw_available=honest.capability("kw_available") + SPOOFED_CLAIM_KW,
        )
        phantom += SPOOFED_CLAIM_KW
    rejected = sum(1 for d in liars if registry.status(d.device_id).value == "rejected")
    return rejected, round(phantom, 2)


def run(fleet_size: int = FLEET_SIZE, scenario: str = "scarcity") -> ReplayResult:
    """Replay the scarcity day at fleet scale with the peak faults injected."""
    started = time.perf_counter()
    trace = load_scenario(scenario)
    engine = ControlRoomEngine(price_trace=trace, fleet_size=fleet_size)
    fault_start = engine.snapshot().now
    committed_before = engine.snapshot().committed_kw
    dispatchable = len([d for d in engine.mine if d.is_dispatchable])

    incident = engine.trigger_device_failure(FOCUS_DEVICE_ID)
    stale_kw = engine.inject_stale_telemetry(STALE_SHARE)
    rejected, phantom_kw = _spoof(engine, SPOOFED_AGENTS)

    price_mwh = engine.remaining_price_mwh()
    hours = engine.remaining_hours()
    kw_lost = round(stale_kw + incident.lost_kw, 2)
    dollars_at_risk = energy_value_usd(kw_lost, hours, price_mwh)

    # The one human step. Nothing above this line changed a dispatch.
    engine.approve_recovery(OWNERS[Role.FLEET_OPERATOR])
    committed_after = engine.snapshot().committed_kw
    recovered = round(committed_after - (committed_before - kw_lost), 2)

    return ReplayResult(
        fleet_size=fleet_size,
        dispatchable=dispatchable,
        date=trace.date,
        location=trace.location,
        price_mwh=price_mwh,
        window_hours=round(hours, 3),
        target_kw=engine.grid_event.target_kw,
        committed_kw_before=committed_before,
        committed_kw_after=committed_after,
        faults=(
            Fault(
                "stale telemetry wave",
                f"{int(STALE_SHARE * 100)}% of homes stop reporting inside the peak window",
                stale_kw,
            ),
            Fault(
                "gateway outage",
                f"the gateway firmware ring behind {FOCUS_DEVICE_ID} goes dark",
                incident.lost_kw,
            ),
            Fault(
                "spoofed agents",
                f"{rejected} edited cards claim {phantom_kw:,.0f} kW they do not have",
                0.0,
            ),
        ),
        kw_lost=kw_lost,
        dollars_at_risk=dollars_at_risk,
        # Capped at what was at risk: reassignment can round a few kW past the lost
        # capacity, and claiming more than was lost would be a lie.
        dollars_recovered=min(
            max(energy_value_usd(recovered, hours, price_mwh), 0.0), dollars_at_risk
        ),
        rejected_cards=rejected,
        phantom_kw_rejected=phantom_kw,
        fault_minutes=round((engine.snapshot().now - fault_start).total_seconds() / 60.0, 2),
        runtime_s=round(time.perf_counter() - started, 2),
    )


def summary(result: ReplayResult) -> str:
    """One line a demo can read out loud."""
    return (
        f"{result.fleet_size:,} simulated batteries on the real ERCOT {result.location} "
        f"{result.date} scarcity day: {result.kw_lost:,.0f} kW lost to three faults at the peak "
        f"(${result.dollars_at_risk:,.0f} at risk), one approval recovered "
        f"${result.dollars_recovered:,.0f} in {result.fault_minutes:.1f} simulated minutes "
        f"= ${result.protected_usd_per_fault_minute:,.0f} protected per minute of fault; "
        f"{result.runtime_s:.1f}s wall clock"
    )


def lines(result: ReplayResult) -> list[str]:
    """The detail behind the headline, for the CLI and the Control Room scene."""
    out = [
        f"peak window: {result.window_hours:.2f} h at ${result.price_mwh:,.2f}/MWh "
        f"(real cached ERCOT settlement prices)",
        f"commitment: {result.committed_kw_before:,.0f} kW before the faults, "
        f"{result.committed_kw_after:,.0f} kW after recovery, "
        f"target {result.target_kw:,.0f} kW",
    ]
    out += [f"fault — {f.name}: {f.detail} ({f.kw:,.0f} kW dropped)" for f in result.faults]
    out += [
        f"no orchestration: the {result.kw_lost:,.0f} kW stays lost for the rest of the window, "
        f"${result.dollars_at_risk:,.0f} gone",
        f"with orchestration: ${result.dollars_recovered:,.0f} recovered after one human "
        f"approval ({result.recovered_share:.0%} of what was at risk), "
        f"${result.dollars_unprotected:,.0f} not recovered",
        f"spoofing: {result.rejected_cards} cards rejected on signature, "
        f"{result.phantom_kw_rejected:,.0f} kW of claimed capacity never entered the plan",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--devices", type=int, default=FLEET_SIZE)
    parser.add_argument("--scenario", default="scarcity")
    args = parser.parse_args(argv)
    result = run(args.devices, args.scenario)
    print(summary(result))
    for line in lines(result):
        print(f"  {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
