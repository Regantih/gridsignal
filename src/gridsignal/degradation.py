"""Degradation-aware dispatch: price the wear of a cycle before taking it.

Every kWh a battery pushes through itself costs a little of the pack. The signal
policy in :mod:`gridsignal.dam` plans a day from the day-ahead curve without that
cost, so it will happily take a spread of a few dollars a megawatt-hour that a cell
chemist would refuse. This module puts a dollar on the cycle and gates the plan on it:

* **The wear cost is an assumption of this repo, and a simple one.** A pack costs
  ``replacement_usd_per_kwh`` to replace and is modelled as delivering ``cycle_life``
  equivalent full cycles before it is retired, so one kWh of throughput costs
  ``replacement_usd_per_kwh / cycle_life`` dollars. Nothing here is a Base Power price,
  a warranty, or a measured degradation curve — the numbers below are stated so they
  can be argued with, and changed in one place.
* **The gate is a spread test, decided before the interval settles.** An export is
  taken only when the price expected for it beats what the energy cost to buy —
  the day-ahead charge window, grossed up for round-trip losses — plus the wear of
  moving it. Exports that fail the test become holds, and when a day has no export
  worth taking, its charge is dropped too: that is a cycle the battery never spends.

Reproduce every number below with::

    python -m gridsignal.degradation
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gridsignal import backtest, dam, detect, forecast, holdout
from gridsignal.control_room.models import UnitType
from gridsignal.fleet import BASE_CORE_KWH, BASE_CORE_POWER_KW
from gridsignal.prices import PriceTrace
from gridsignal.signals import Signal


@dataclass(frozen=True)
class WearModel:
    """What one kWh of throughput costs this kind of battery. All assumptions.

    ``replacement_usd_per_kwh`` and ``cycle_life`` are modelling assumptions of this
    repo, chosen to be defensible and easy to change, not vendor figures: a smaller
    legacy pack is modelled as the more expensive and shorter-lived of the two, and
    the larger Base Core-style unit (40 kWh / 20 kW, per public interview, not an
    official specification) as the cheaper and longer-lived one.
    """

    unit_type: UnitType
    label: str
    usable_kwh: float
    power_kw: float
    replacement_usd_per_kwh: float
    cycle_life: int

    @property
    def wear_usd_per_kwh(self) -> float:
        """Dollars of pack life spent moving one kWh through the battery."""
        return round(self.replacement_usd_per_kwh / self.cycle_life, 5)

    @property
    def wear_usd_per_mwh(self) -> float:
        """The same cost in the units a price curve is quoted in."""
        return round(self.wear_usd_per_kwh * 1000.0, 2)

    @property
    def assumption(self) -> str:
        return (
            f"${self.replacement_usd_per_kwh:,.0f}/kWh to replace over "
            f"{self.cycle_life:,} equivalent full cycles = "
            f"${self.wear_usd_per_mwh:,.2f}/MWh of throughput (assumption)"
        )


#: The two simulated hardware generations, priced. Both rows are assumptions.
WEAR_MODELS: tuple[WearModel, ...] = (
    WearModel(
        unit_type=UnitType.LEGACY,
        label="legacy unit",
        usable_kwh=backtest.DEFAULT_KWH,
        power_kw=backtest.DEFAULT_POWER_KW,
        replacement_usd_per_kwh=400.0,
        cycle_life=4_000,
    ),
    WearModel(
        unit_type=UnitType.BASE_CORE,
        label="Base Core-style unit",
        usable_kwh=BASE_CORE_KWH,
        power_kw=BASE_CORE_POWER_KW,
        replacement_usd_per_kwh=300.0,
        cycle_life=6_000,
    ),
)


def model_for(unit_type: UnitType) -> WearModel:
    return next(m for m in WEAR_MODELS if m.unit_type is unit_type)


def required_export_mwh(plan: pd.DataFrame, model: WearModel, efficiency: float) -> float:
    """The price an export has to beat to be worth taking, in $/MWh.

    What the energy cost to buy — the mean price of the day's planned charge window,
    grossed up for the round trip — plus the wear of moving it. Every input is known
    from the day-ahead curve before the trade day starts, so the gate has no lookahead.
    """
    charging = plan["planned"] == Signal.CHARGE.value
    charge_mwh = float(plan.loc[charging, "dam_mwh"].mean()) if charging.any() else 0.0
    return round(charge_mwh / efficiency + model.wear_usd_per_mwh, 2)


def gate_plan(
    plan: pd.DataFrame,
    model: WearModel,
    efficiency: float = backtest.ROUND_TRIP_EFFICIENCY,
) -> pd.DataFrame:
    """Gate the plan on wear: the cycle first, then each export on its own margin.

    Two decisions, because they have different costs. Taking a cycle at all has to
    pay for the energy *and* the wear, so a day whose best export cannot clear
    ``required_export_mwh`` is dropped entirely — charge included. Once the cycle is
    taken the energy is bought, and each export is judged only against the wear of
    moving it; anything cheaper than that is held for a better interval.

    The price an export is judged on is the one available before it settles: its
    day-ahead hour, or the last settled print where that is higher (the scarcity case
    the day-ahead curve never saw). Returns a copy with ``signal`` and ``reason``
    rewritten and a ``wear_gate`` column naming what the gate did.
    """
    gated = plan.reset_index(drop=True).copy()
    floor = required_export_mwh(gated, model, efficiency)
    expected = gated[["dam_mwh", "observed_mwh"]].astype(float).max(axis=1)

    exporting = gated["signal"] == Signal.EXPORT.value
    gated["wear_gate"] = ""
    if not exporting.any():
        return gated

    if expected[exporting].max() < floor:
        # Not one export on the day covers the round trip plus the wear, so the charge
        # is not worth taking either: this is the cycle the battery saves.
        dropped = exporting | (gated["signal"] == Signal.CHARGE.value)
        note = pd.Series(
            f"no export clears the ${floor:,.2f}/MWh round trip plus wear: skip the cycle",
            index=gated.index[dropped],
        )
    else:
        # The cycle is worth taking. Each export is then a marginal decision on energy
        # already bought: sell wherever the price beats the wear of moving it, and hold
        # the rest for a better interval rather than grinding the pack for cents.
        dropped = exporting & (expected < model.wear_usd_per_mwh)
        note = (
            f"under the ${model.wear_usd_per_mwh:,.2f}/MWh wear cost: "
            + expected[dropped].round(2).astype(str)
            + "/MWh expected, hold the energy instead"
        )

    gated.loc[dropped, "wear_gate"] = note
    gated.loc[dropped, "reason"] = gated.loc[dropped, "wear_gate"]
    gated.loc[dropped, "signal"] = Signal.HOLD.value
    return gated


@dataclass(frozen=True)
class WearResult:
    """One day, one battery type, with the wear taken out of the dollars."""

    date: str
    unit_type: UnitType
    gated: bool
    gross_uplift_usd: float
    wear_usd: float
    cycles: float
    exported_kwh: float

    @property
    def net_uplift_usd(self) -> float:
        """Uplift over the naive schedule after the modelled wear of both."""
        return round(self.gross_uplift_usd - self.wear_usd, 2)


def score_day(
    trace: PriceTrace,
    model: WearModel,
    gated: bool,
    serve_home: bool = True,
) -> WearResult:
    """Score one held-out or tuning day for one battery type, with or without the gate.

    The wear charged is the *difference* in throughput between the signal policy and
    the naive schedule, so the uplift and the wear are measured against the same
    baseline: a policy that cycles less than the naive one is credited for it.
    """
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    plan = dam.signals_for(detections, prob, trace.dam)
    if gated:
        plan = gate_plan(plan, model)
    ledger = backtest.value_captured(
        plan,
        trace.frame,
        kwh=model.usable_kwh,
        power_kw=model.power_kw,
        serve_home=serve_home,
    )
    summary = backtest.summarize(ledger)
    naive_charged = float(
        pd.Series([0.0, *ledger["naive_soc_kwh"]]).diff().dropna().clip(lower=0).sum()
    )
    extra_throughput = summary.charged_kwh - naive_charged
    return WearResult(
        date=trace.date,
        unit_type=model.unit_type,
        gated=gated,
        gross_uplift_usd=summary.uplift_usd,
        wear_usd=round(extra_throughput * model.wear_usd_per_kwh, 2),
        cycles=round(summary.charged_kwh / model.usable_kwh, 3),
        exported_kwh=summary.exported_kwh,
    )


@dataclass(frozen=True)
class SplitResult:
    """One battery type on one split of days, with and without the wear gate."""

    split: str
    model: WearModel
    days: int
    ungated: tuple[WearResult, ...]
    gated: tuple[WearResult, ...]

    @staticmethod
    def _mean(results: tuple[WearResult, ...], attr: str) -> float:
        if not results:
            return 0.0
        return round(sum(float(getattr(r, attr)) for r in results) / len(results), 2)

    @property
    def gross_usd(self) -> float:
        return self._mean(self.ungated, "gross_uplift_usd")

    @property
    def wear_usd(self) -> float:
        return self._mean(self.ungated, "wear_usd")

    @property
    def net_usd(self) -> float:
        return self._mean(self.ungated, "net_uplift_usd")

    @property
    def gated_net_usd(self) -> float:
        return self._mean(self.gated, "net_uplift_usd")

    @property
    def cycles(self) -> float:
        return round(sum(r.cycles for r in self.ungated), 2)

    @property
    def gated_cycles(self) -> float:
        return round(sum(r.cycles for r in self.gated), 2)

    @property
    def cycles_saved(self) -> float:
        return round(self.cycles - self.gated_cycles, 2)

    @property
    def days_won(self) -> int:
        return sum(1 for r in self.gated if r.net_uplift_usd > 0)

    @property
    def days_won_ungated(self) -> int:
        return sum(1 for r in self.ungated if r.net_uplift_usd > 0)

    @property
    def verdict(self) -> str:
        """Whether the gate paid for itself on this split, in one line."""
        delta = round(self.gated_net_usd - self.net_usd, 2)
        direction = "earns" if delta >= 0 else "costs"
        return (
            f"{self.split}, {self.model.label}: the wear gate {direction} "
            f"${abs(delta):,.2f} per battery per day and saves "
            f"{self.cycles_saved:,.2f} of {self.cycles:,.2f} equivalent full cycles"
        )


def evaluate(split: str, model: WearModel) -> SplitResult:
    """Score every day of ``split`` ("tuning" or "held-out") for one battery type."""
    traces = holdout.load_tuning() if split == "tuning" else holdout.load_holdout()
    return SplitResult(
        split=split,
        model=model,
        days=len(traces),
        ungated=tuple(score_day(t, model, gated=False) for t in traces),
        gated=tuple(score_day(t, model, gated=True) for t in traces),
    )


def evaluate_all() -> list[SplitResult]:
    return [evaluate(split, model) for split in ("tuning", "held-out") for model in WEAR_MODELS]


def report() -> str:
    results = evaluate_all()
    lines = [
        "Degradation-aware dispatch (simulated fleet, real cached ERCOT prices)",
        "",
        "Modelled wear, per battery type — assumptions of this repo, not vendor data:",
    ]
    for model in WEAR_MODELS:
        lines.append(f"  {model.label:<22}{model.usable_kwh:>5,.0f} kWh  {model.assumption}")
    lines += [
        "",
        f"{'split':<10}{'battery':<22}{'gross $':>9}{'wear $':>8}{'net $':>8}"
        f"{'net, gated $':>14}{'cycles':>8}{'gated':>7}{'saved':>7}{'days won':>12}",
    ]
    for result in results:
        lines.append(
            f"{result.split:<10}{result.model.label:<22}{result.gross_usd:>9,.2f}"
            f"{result.wear_usd:>8,.2f}{result.net_usd:>8,.2f}{result.gated_net_usd:>14,.2f}"
            f"{result.cycles:>8,.2f}{result.gated_cycles:>7,.2f}{result.cycles_saved:>7,.2f}"
            f"{result.days_won_ungated:>7} -> {result.days_won} of {result.days}"
        )
    lines += [""]
    for result in results:
        lines.append(f"  {result.verdict}")
    lines += [
        "",
        "Dollars are per battery per day against the naive fixed schedule; wear is charged",
        "on the throughput the policy adds over that schedule, so cycling less is credited.",
    ]
    return "\n".join(lines)


def main() -> None:
    print(report())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
