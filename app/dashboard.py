"""GridSignal Control Room: simulation-only operator dashboard for a battery fleet.

Run with:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from gridsignal import (
    ancillary,
    congestion,
    degradation,
    drills,
    holdout,
    home,
    ingest,
    insight,
    install,
    judgment_report,
    member,
    pipeline,
    replay,
    rollout,
    why,
)
from gridsignal.backtest import BacktestSummary
from gridsignal.control_room import ControlRoomEngine, workflow
from gridsignal.control_room.engine import OWNERS
from gridsignal.control_room.models import (
    Device,
    DeviceStatus,
    Incident,
    IncidentStatus,
    Role,
    Severity,
    TaskStatus,
)
from gridsignal.fleet import (
    FLEET_SIZE,
    FLEET_SIZES,
    FOCUS_DEVICE_ID,
    UTILITY_PARTNER,
    settlement_zone,
)
from gridsignal.jev import evaluate as jev_evaluate
from gridsignal.jev import incident as jev_incident
from gridsignal.jev.client import JevResponse, Source
from gridsignal.jev.judgment import Action, PrincipleType, Verdict
from gridsignal.jev.policy import ApprovalDecision, ApprovalPolicy
from gridsignal.jev.questions import BACKUP_RISK, ROOT_CAUSE, TRUST_PREFIX
from gridsignal.mesh.cards import CardStatus
from gridsignal.mesh.scenarios import available_scenarios as available_chaos_scenarios
from gridsignal.prices import (
    DEFAULT_SCENARIO,
    available_scenarios,
    energy_value_usd,
    load_scenario,
)
from gridsignal.signals import Signal
from gridsignal.simulate import RunResult, run_file

st.set_page_config(page_title="GridSignal Control Room", layout="wide", page_icon="⚡")

#: The operator whose click approves an award. Nothing in YAML can stand in for them.
OPERATOR = OWNERS[Role.FLEET_OPERATOR]

STATUS_COLOR = {
    DeviceStatus.ONLINE: "#16a34a",
    DeviceStatus.DEGRADED: "#f59e0b",
    DeviceStatus.OFFLINE: "#dc2626",
    DeviceStatus.UNAVAILABLE: "#6b7280",
}
STATUS_LABEL = {
    DeviceStatus.ONLINE: "Online",
    DeviceStatus.DEGRADED: "Degraded",
    DeviceStatus.OFFLINE: "Offline",
    DeviceStatus.UNAVAILABLE: "Quarantined",
}
SEVERITY_COLOR = {
    Severity.LOW: "#0ea5e9",
    Severity.MEDIUM: "#f59e0b",
    Severity.HIGH: "#dc2626",
    Severity.CRITICAL: "#7f1d1d",
}
TASK_BADGE = {
    TaskStatus.BLOCKED: ("Blocked", "#6b7280"),
    TaskStatus.OPEN: ("Open", "#dc2626"),
    TaskStatus.IN_PROGRESS: ("In progress", "#f59e0b"),
    TaskStatus.DONE: ("Done", "#16a34a"),
}

#: One type scale, one spacing step and one set of surface colours for every view, so a
#: card in the Member App is built from the same parts as a card in the Control Room.
#: Muted text is #a3b1c6 rather than the grey it started as: on the #11161f card that is
#: a 7.7:1 contrast ratio, past WCAG AA for small text with room to spare.
CSS = """
<style>
:root {
  --gs-surface: #11161f; --gs-line: #263041;
  --gs-ink: #e6edf6; --gs-body: #c3cfe0; --gs-muted: #a3b1c6; --gs-accent: #38bdf8;
  --gs-step: .5rem; --gs-radius: 12px;
}
.block-container {padding-top: 2rem; max-width: 1500px;}
.gs-card {background: var(--gs-surface); border: 1px solid var(--gs-line);
          border-radius: var(--gs-radius); padding: 1rem 1.15rem;
          margin-bottom: calc(var(--gs-step) * 1.7);}
.gs-pill {display:inline-block; padding: 2px 10px; border-radius: 999px;
          font-size: 0.74rem; font-weight: 600; color: #fff;
          margin-right: calc(var(--gs-step) * .5);}
.gs-kicker {text-transform: uppercase; letter-spacing: .08em; font-size: .7rem;
            font-weight: 600; color: var(--gs-muted);
            margin: 0 0 calc(var(--gs-step) * .5);}
.gs-title {font-size: 1.02rem; font-weight: 650; color: var(--gs-ink);
           margin-bottom: calc(var(--gs-step) * .7);}
.gs-body {font-size: .87rem; color: var(--gs-body); line-height: 1.5;}
.gs-sim {background: #1b2537; border-left: 4px solid var(--gs-accent); border-radius: 8px;
         padding: .65rem .9rem; font-size: .85rem; color: #cfe0f2; line-height: 1.5;}
.gs-insight {border-color: var(--gs-accent);
             background: linear-gradient(180deg,#132030 0%,var(--gs-surface) 100%);}
.gs-huge {font-size: 3.4rem; font-weight: 700; color: var(--gs-accent); line-height: 1.1;
          font-variant-numeric: tabular-nums;}
.gs-lead {font-size: 1.05rem; font-weight: 600; color: var(--gs-ink);
          margin-bottom: calc(var(--gs-step) * .9);}
/* A term a first-time reader would not know, with its meaning on hover. */
.gs-term {border-bottom: 1px dotted var(--gs-muted); cursor: help;}
/* Seven metrics share one row, so the default value size truncates mid-number. */
[data-testid="stMetricValue"] {font-size: 1.75rem; font-variant-numeric: tabular-nums;}
[data-testid="stMetricLabel"] p {font-size: .8rem; color: var(--gs-muted);}
[data-testid="stMetricDelta"] {font-size: .78rem; font-variant-numeric: tabular-nums;}
[data-testid="stCaptionContainer"] p {color: var(--gs-muted); line-height: 1.5;}
</style>
"""

#: Every term in the product a member or a first-time judge would have to look up, and
#: the sentence that explains it. A metric whose label uses one of these carries the
#: explanation as a tooltip; prose underlines it with `term()`.
GLOSSARY: dict[str, str] = {
    "headroom": (
        "The kW a battery can still offer after its member's backup reserve and "
        "everything it has already promised."
    ),
    "backup reserve": (
        "Stored energy held back for the member's own home; the fleet may never sell it."
    ),
    "reserve floor": (
        "The least state of charge every battery must keep for its member's own home."
    ),
    "state of charge": "How full the battery is, as a share of its usable capacity.",
    "day-ahead": (
        "ERCOT's market that clears the day before delivery, so the price is known in advance."
    ),
    "held-out": (
        "Days the policy was never tuned on, scored once, so the result is not hindsight."
    ),
    "ancillary": (
        "Capacity ERCOT pays for standing ready (Reg Up, Reg Down, RRS, ECRS, Non-Spin) "
        "rather than for energy delivered."
    ),
    "congestion": (
        "When the wires cannot carry the cheapest power, zones settle at different prices; "
        "the gap to the hub average is the congestion basis."
    ),
    "probation": ("A newly installed battery may not take awards until it passes a health check."),
    "quarantined": "Taken out of dispatch by the operator until it can be trusted again.",
    "canary": "A small first ring of devices a new build runs on before the rest of the fleet.",
    "equivalent full cycle": (
        "One full charge and discharge worth of throughput, however it was spread out."
    ),
    "wear cost": (
        "What one MWh through the battery costs in pack life — an assumption of this repo."
    ),
    "uplift": "Dollars the policy earned above the simple baseline, per battery per day.",
    "offerable": (
        "kW the fleet could sell right now: spare power above every member's reserve and "
        "everything already promised."
    ),
    "clock baseline": (
        "Charge 01:00-05:00 and discharge 17:00-21:00 every day, ignoring the price."
    ),
    "mesh": ("The batteries, gateways and zone coordinators that bid to each other as agents."),
    "tenant": ("A block of batteries a partner utility dispatches; this fleet may never bid them."),
    "ring": "One stage of a firmware rollout: lab, 1% canary, 10%, 50%, then the rest.",
    "spike": "An interval whose price jumps far above the recent rolling baseline.",
}


# Plotting every marker of a 10,000-device fleet is slow and unreadable, so the map
# thins healthy devices out and always keeps everything that is not online.
MAP_MARKERS = 400
GRID_TILES = 48

VIEWS = ("Control Room", "Member App", "Grid Signals", "Agent Mesh", "Why")
CARD_COLOR = {
    CardStatus.VERIFIED: "#16a34a",
    CardStatus.STALE: "#f59e0b",
    CardStatus.REJECTED: "#dc2626",
}
JEV_COLOR = {
    Source.LIVE: "#16a34a",
    Source.FIXTURE: "#38bdf8",
    Source.FALLBACK: "#f59e0b",
}
JEV_LABEL = {
    Source.LIVE: "Jev live",
    Source.FIXTURE: "Jev (recorded answer)",
    Source.FALLBACK: "rules (Jev offline)",
}
SIGNAL_COLOR = {
    Signal.CHARGE.value: "#38bdf8",
    Signal.HOLD.value: "#6b7280",
    Signal.EXPORT.value: "#f59e0b",
}


@st.cache_data(show_spinner=False)
def signals_run(scenario: str) -> pipeline.PipelineResult:
    """Detect -> forecast -> signal -> backtest for one bundled ERCOT day."""
    return pipeline.run(scenario)


@st.cache_data(show_spinner=False)
def jev_eval() -> jev_evaluate.EvalReport:
    """Rules-only vs Jev across every chaos scenario; replayed from fixtures."""
    return jev_evaluate.evaluate()


@st.cache_data(show_spinner=False)
def drill_report() -> drills.DrillReport:
    """Rules-only vs Jev on the held-out drills; replayed from fixtures."""
    return drills.run()


@st.cache_data(show_spinner=False)
def rollout_run(path: str) -> rollout.RolloutResult:
    """Replay one staged firmware rollout from its YAML scenario."""
    scenario = rollout.load_rollout(path)
    return rollout.run_rollout(scenario, trace=rollout.trace_path(scenario))


@st.cache_data(show_spinner=False)
def install_run(path: str) -> install.InstallResult:
    """Replay the install wave joining the mesh during a live event."""
    scenario = install.load_install(path)
    return install.run_install_wave(scenario, trace=install.trace_path(scenario))


@st.cache_data(show_spinner=False)
def ancillary_day() -> ancillary.DayValue:
    """The scarcity day split between energy and ancillary for one Base Core-style unit."""
    return ancillary.scarcity_day(ancillary.BASE_CORE)


@st.cache_data(show_spinner=False)
def ancillary_holdout() -> ancillary.SplitSummary:
    return ancillary.holdout_summary(ancillary.BASE_CORE)


@st.cache_data(show_spinner=False)
def ancillary_unrestricted() -> ancillary.SplitSummary:
    """The same days scored against all five products: a comparison, never the claim."""
    return ancillary.holdout_summary(ancillary.BASE_CORE, rules=ancillary.ALL_PRODUCTS)


@st.cache_data(show_spinner=False)
def replay_run(fleet_size: int) -> replay.ReplayResult:
    """The full-fleet scarcity replay: three faults at the peak, one approval."""
    return replay.run(fleet_size)


@st.cache_data(show_spinner=False)
def insight_run() -> list[insight.DayInsight]:
    """Day-ahead versus real-time value on every bundled ERCOT day."""
    return insight.analyze()


@st.cache_data(show_spinner=False)
def holdout_run() -> list[holdout.DayResult]:
    """Score the frozen policy on the bundled days it was never tuned on."""
    return holdout.evaluate()


@st.cache_data(show_spinner=False)
def holdout_grid_only() -> list[holdout.DayResult]:
    """The same frozen policy without household load, to price home-first dispatch."""
    return holdout.evaluate(serve_home=False)


@st.cache_data(show_spinner=False)
def holdout_as_first_scored() -> list[holdout.DayResult]:
    """The original scoring, which let an interval's own settled print decide it."""
    return holdout.evaluate(same_interval_price=True)


def engine() -> ControlRoomEngine:
    """One engine per (price scenario, fleet size); rebuilt when the operator switches."""
    key = (
        st.session_state.get("scenario", DEFAULT_SCENARIO),
        st.session_state.get("fleet_size", FLEET_SIZE),
    )
    if st.session_state.get("engine_key") != key:
        st.session_state.engine = ControlRoomEngine(
            price_trace=load_scenario(key[0]), fleet_size=key[1]
        )
        st.session_state.engine_key = key
    return st.session_state.engine


def prewarm() -> None:
    """Pay the first incident's one-off cost in the background, before anything is clicked.

    The incident screen quotes the blind safety score, which replays every held-out drill.
    Left lazy, the first 10,000-device fault spends seconds on it while an operator waits;
    warmed here it is ready by the time the fault is triggered.
    """
    if st.session_state.get("prewarmed"):
        return
    st.session_state.prewarmed = True
    threading.Thread(target=judgment_report.cached_build, daemon=True).start()


def pill(text: str, color: str) -> str:
    return f"<span class='gs-pill' style='background:{color}'>{text}</span>"


def money(amount: float, cents: bool = True) -> str:
    """Dollars, thousands-separated, with the minus sign outside the dollar sign."""
    body = f"{abs(amount):,.2f}" if cents else f"{abs(amount):,.0f}"
    return f"-${body}" if amount <= -0.005 else f"${body}"


def power(kw: float, decimals: int = 0) -> str:
    """Power. The fleet is priced in kW everywhere, so kW it stays at every scale."""
    return f"{kw:,.{decimals}f} kW"


def energy(kwh: float, decimals: int = 0) -> str:
    return f"{kwh:,.{decimals}f} kWh"


def hours(value: float) -> str:
    return f"{value:,.1f} h"


def seconds(value: float) -> str:
    return f"{value:,.0f} s"


def ratio(part: float, whole: float) -> str:
    """Counts out of counts read as words, never as a fraction to be misread as division."""
    return f"{part:,.0f} of {whole:,.0f}"


def explain(label: str) -> str | None:
    """The glossary sentence for the first term this label uses, if it uses one.

    Whole words only, so 'Powering your home' is not explained as a rollout ring.
    """
    lowered = label.lower()
    hits = []
    for word, text in GLOSSARY.items():
        found = re.search(rf"\b{re.escape(word)}s?\b", lowered)
        if found:
            hits.append((found.start(), text))
    return min(hits)[1] if hits else None


def term(word: str, key: str | None = None) -> str:
    """Inline jargon, underlined, with its plain-language meaning on hover."""
    meaning = GLOSSARY[key or word.lower()]
    return f"<span class='gs-term' title='{meaning}'>{word}</span>"


def caption(text: str, box: DeltaGenerator | None = None) -> None:
    """Small print, one style everywhere.

    Dollar signs are escaped: Streamlit reads a pair of them as LaTeX, which turns
    "earned $1.01 of $1.68" into two typeset symbols and loses the numbers.
    """
    (box or st).caption(text.replace("$", r"\$"))


def metric(
    box: DeltaGenerator,
    label: str,
    value: object,
    note: str | None = None,
    tooltip: str | None = None,
) -> None:
    """One metric, laid out the same way everywhere.

    The second line is always context ("per battery", "0 total"), never a movement, so it
    is a caption rather than a delta: no arrow and no green or red to read a trend into.
    Any jargon in the label arrives with its plain-language meaning attached.
    """
    box.metric(label, value, help=tooltip or explain(label))
    if note:
        caption(note, box)


def render_header() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    left, right = st.columns([3, 2])
    with left:
        st.title("⚡ GridSignal Control Room")
        caption("Distributed home-battery fleet orchestration — Texas (simulated)")
    with right:
        st.markdown(
            "<div class='gs-sim'><b>SIMULATION ONLY.</b> Deterministic mock fleet, priced with "
            "a cached real ERCOT settlement-price trace. "
            "No real devices, utilities or ERCOT systems are contacted. "
            "<b>A human operator approves every recovery action</b> — nothing is "
            "dispatched automatically.</div>",
            unsafe_allow_html=True,
        )


def covered(committed_kw: float, target_kw: float) -> bool:
    """Whether the fleet is delivering its commitment, read the way the banner reads it.

    One rounding decides the words, the colour and the percentage, so a banner that says
    0 kW at risk is never red and a banner that names missing kW is never green.
    """
    return round(committed_kw) >= round(target_kw)


def coverage_banner(committed_kw: float, target_kw: float) -> str:
    """What the fleet is actually delivering against what it promised.

    Delivered kW is capped at the target, so an allocation over-assigned by a rounding
    remainder never reads as 36,001 of 36,000.
    """
    target = round(target_kw)
    delivered = min(round(committed_kw), target)
    return f"Delivering {delivered:,} of {target:,} kW; {max(target - delivered, 0):,} kW at risk"


def coverage_pct_text(committed_kw: float, target_kw: float, coverage_pct: float) -> str:
    """Percent covered, held below 100% until the banner agrees every kW is there."""
    if not covered(committed_kw, target_kw):
        return f"{min(math.floor(coverage_pct), 99)}%"
    return f"{min(coverage_pct, 100):.0f}%"


def render_overview(eng: ControlRoomEngine) -> None:
    snap = eng.snapshot()
    ev = snap.grid_event
    cols = st.columns(7)
    metric(cols[0], "Fleet size", f"{snap.total_devices:,}")
    metric(
        cols[1],
        "Capacity available",
        energy(snap.available_capacity_kwh),
        note=f"{snap.committed_kw:,.0f} kW committed",
    )
    metric(cols[2], "Online", f"{snap.online:,}", note=f"{snap.degraded} degraded")
    metric(
        cols[3],
        "Offline / quarantined",
        f"{snap.offline:,} / {snap.unavailable:,}",
        note=(f"{FOCUS_DEVICE_ID} gateway ring" if snap.offline or snap.unavailable else "none"),
    )
    metric(
        cols[4],
        "Grid event",
        ev.status.title(),
        note=(
            f"{coverage_pct_text(snap.committed_kw, ev.target_kw, snap.coverage_pct)} "
            f"of {ev.target_kw:,.0f} kW"
        ),
    )
    metric(
        cols[5],
        "Open incidents",
        snap.open_incidents,
        note=f"{len(snap.incidents)} total",
    )
    window_value = energy_value_usd(ev.target_kw, ev.duration_hours, ev.price_mwh)
    metric(
        cols[6],
        f"{ev.zone} price (real)",
        f"${ev.price_mwh:,.0f}/MWh",
        note=f"window ${window_value:,.0f}",
    )

    banner = coverage_banner(snap.committed_kw, ev.target_kw)
    if covered(snap.committed_kw, ev.target_kw):
        st.success(banner)
    else:
        st.error(f"{banner}. Recovery plan needs operator approval.")


def render_home_first(eng: ControlRoomEngine) -> None:
    """Where the discharge goes: members' houses first, the grid gets the surplus."""
    snap = eng.snapshot()
    st.markdown("<div class='gs-kicker'>Home-first dispatch</div>", True)
    cols = st.columns(4)
    metric(cols[0], "Battery discharge", power(snap.discharge_kw))
    metric(
        cols[1],
        "Served to members' homes",
        power(snap.home_load_kw),
        note="simulated household load",
    )
    metric(
        cols[2],
        "Exported to the grid",
        power(snap.committed_kw),
        note=f"{snap.coverage_pct:.0f}% of target",
    )
    metric(
        cols[3],
        "Partner tenant",
        power(snap.partner_kw),
        note=UTILITY_PARTNER,
    )
    caption(
        "Exported kW is battery discharge minus the home's own load. The partner"
        " tenant's units are shown for visibility only — this mesh never bids, awards"
        " or reassigns a battery it does not control, and the kW shown for them is a"
        " simulated stand-in written by this repo, not the partner's schedule. It is"
        " held to the same member backup floor as everything else."
    )

    hours, price = eng.remaining_hours(), eng.grid_event.price_mwh
    left, right = st.columns(2)
    with left:
        rows = home.by_unit_type(eng.mine, hours, price, eng.reserve_fraction)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Unit type": r.unit_type,
                        "Homes": r.devices,
                        "Home load kW": r.home_load_kw,
                        "Export kW": r.export_kw,
                        "Revenue $": r.revenue_usd,
                        "Backup h": r.backup_hours,
                    }
                    for r in rows
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        caption(
            "Base Core-style units are modelled at 40 kWh / 20 kW per public interview, "
            "not an official specification."
        )
    with right:
        tenants = home.by_tenant(eng.devices)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Control authority": t.controller,
                        "Batteries": t.devices,
                        "Dispatchable": t.dispatchable,
                        "Export kW": t.export_kw,
                    }
                    for t in tenants
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        caption(
            "Simulated tenancy: in non-retail-choice territory the utility partner would "
            "dispatch these units and this control room never can. Their export here is a "
            "simulated stand-in, reserve-first like the rest, not a partner schedule."
        )


def render_surplus(eng: ControlRoomEngine) -> None:
    """Account for every spare kW: offered at this price, or held for a named reason."""
    st.markdown("<div class='gs-kicker'>Spare capacity</div>", True)
    offer = eng.surplus_offer()
    cols = st.columns(3)
    metric(cols[0], "Offerable now", power(offer.offerable_kw), tooltip=GLOSSARY["offerable"])
    metric(
        cols[1],
        "Worth at this price",
        money(offer.revenue_usd),
        note=f"${offer.net_usd:,.2f} after modelled wear",
    )
    metric(
        cols[2],
        "Held back for members",
        power(offer.idle_kw),
        tooltip=GLOSSARY["backup reserve"],
    )

    if offer.held:
        st.dataframe(
            pd.DataFrame(
                [{"Held kW": round(h.kw), "Why": h.reason} for h in offer.held],
            ),
            hide_index=True,
            use_container_width=True,
        )
    if st.button(
        f"Offer {offer.offerable_kw:,.0f} kW of spare capacity",
        disabled=offer.offerable_kw <= 0,
        key="offer_surplus",
    ):
        eng.offer_surplus()
        st.rerun()
    caption(
        f"At ${offer.price_mwh:,.2f}/MWh against a ${eng.offer_floor_usd_mwh():,.2f}/MWh "
        "wear floor. Feeder export caps and member reserve are simulated assumptions; "
        "reproduce with `python -m gridsignal.surplus`."
    )


@st.cache_data(show_spinner="Recomputing every number on the Why page…")
def why_page() -> why.WhyPage:
    """The Why screen, recomputed from the code that produces each number."""
    return why.build()


def render_why() -> None:
    """Problem, approach, evidence, limits — nothing on this screen is typed in."""
    page = why_page()
    st.markdown(
        "<div class='gs-lead'>Why GridSignal exists, what it does, and where it stops.</div>",
        unsafe_allow_html=True,
    )
    caption(
        "Every figure below was recomputed from the code when this page loaded. "
        "Reproduce the whole screen with `python -m gridsignal.why`."
    )
    for section in page.sections:
        st.divider()
        st.subheader(section.title)
        st.markdown(f"<div class='gs-body'>{section.lead}</div>", unsafe_allow_html=True)
        st.write("")
        for claim in section.claims:
            with st.container(border=True):
                left, right = st.columns([2, 3], gap="large")
                with left:
                    st.markdown(
                        f"<div class='gs-kicker'>{claim.label}</div>"
                        f"<div class='gs-lead'>{claim.value}</div>",
                        unsafe_allow_html=True,
                    )
                with right:
                    st.markdown(
                        f"<div class='gs-body'>{claim.detail}</div>", unsafe_allow_html=True
                    )
                    st.code(claim.command, language="bash")
                    if claim.live:
                        caption(
                            "Timed on this machine while the page loaded, so absolute "
                            "times move between machines; the ratio is what to read."
                        )
    st.divider()
    st.subheader("The limits")
    for limit in page.limits:
        st.markdown(f"<div class='gs-body'>• {limit}</div>", unsafe_allow_html=True)


def render_fleet_replay() -> None:
    """One scene: 10,000 batteries, the real scarcity day, three faults at the peak."""
    st.markdown("<div class='gs-kicker'>Full-fleet scarcity replay</div>", True)
    result = replay_run(replay.FLEET_SIZE)
    cols = st.columns(4)
    metric(cols[0], "Batteries replayed", f"{result.fleet_size:,}")
    metric(cols[1], "At risk at the peak", money(result.dollars_at_risk, cents=False))
    metric(
        cols[2],
        "Protected",
        money(result.dollars_recovered, cents=False),
        note=f"{result.spare_kw_at_fault:,.0f} kW spare headroom made it possible",
    )
    metric(
        cols[3],
        "Share of the dollars at risk",
        f"{result.recovered_share:.0%}",
        note=(
            f"priced over the {result.recovery_hours:.2f} h after the fix, "
            f"not the {result.fault_minutes:.1f} simulated min degraded"
        ),
    )
    st.dataframe(
        pd.DataFrame(
            [
                {"Fault at the peak": f.name, "What happens": f.detail, "kW dropped": round(f.kw)}
                for f in result.faults
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    caption(
        f"Real cached ERCOT {result.location} {result.date} settlement prices "
        f"(${result.price_mwh:,.0f}/MWh across the {result.window_hours:.2f} h peak window); "
        "the devices, the faults and the spoofed cards are simulated. Without orchestration "
        f"the {result.kw_lost:,.0f} kW stays lost for the rest of the window. "
        f"{result.rejected_cards} spoofed cards claiming {result.phantom_kw_rejected:,.0f} kW "
        f"were refused on signature. All of it came back only because "
        f"{result.spare_kw_at_fault:,.0f} kW of uncommitted, deliverable headroom was left in "
        f"the healthy fleet \u2014 {result.headroom_cover:.1f}x the {result.kw_lost:,.0f} kW "
        "lost. With a thinner fleet the recovery would be partial. "
        f"{result.runtime_s:.1f}s wall clock; reproduce with `python -m gridsignal.replay`."
    )


def render_reserve_policy(eng: ControlRoomEngine) -> None:
    """Raise the member reserve floor before a storm, and price what that costs."""
    st.markdown("<div class='gs-kicker'>Storm reserve policy</div>", True)
    fraction = st.select_slider(
        "Minimum member reserve",
        options=[0.20, 0.30, 0.40, 0.50],
        format_func=lambda f: f"{f:.0%} of capacity",
        key="reserve_fraction",
        label_visibility="collapsed",
    )
    if fraction != eng.reserve_fraction:
        eng.set_reserve_floor(float(fraction))
        st.rerun()

    outcome = eng.reserve_outcome(home.STORM_RESERVE_FRACTION)
    storm = f"{home.STORM_RESERVE_FRACTION:.0%}"
    # Two tiles to a row: three of these labels do not fit the side column and the
    # values were being clipped mid-number.
    top = st.columns(2)
    metric(top[0], "Reserve floor", f"{eng.reserve_fraction:.0%}", note=f"storm floor {storm}")
    metric(
        top[1],
        "Revenue given up",
        money(outcome.revenue_given_up_usd),
        note=(
            f"at {storm}: {outcome.committed_kw_after - outcome.committed_kw_before:,.0f} kW export"
        ),
    )
    bottom = st.columns(2)
    metric(
        bottom[0],
        "Backup protected",
        hours(outcome.backup_hours_after),
        note=f"at {storm}: {outcome.backup_hours_gained:+.1f} h per member",
    )
    caption(
        "Before a forecast storm the operator holds more energy back for members and "
        "sells less into the event. Both numbers come from the same simulated fleet."
    )


def map_devices(devices: list[Device]) -> list[Device]:
    """Thin a large fleet down for plotting, keeping every unhealthy device.

    The sample is taken zone by zone. A flat stride over the fleet list is not safe:
    devices are laid out round-robin across the five zones, so any stride that is a
    multiple of five draws every marker from one city and the map reads as one blob.
    """
    if len(devices) <= MAP_MARKERS:
        return devices
    by_zone: dict[str, list[Device]] = defaultdict(list)
    for device in devices:
        by_zone[device.zone].append(device)
    per_zone = max(MAP_MARKERS // len(by_zone), 1)
    keep: dict[str, Device] = {}
    for zone_devices in by_zone.values():
        step = max(len(zone_devices) // per_zone, 1)
        keep.update({d.device_id: d for d in zone_devices[::step][:per_zone]})
    keep.update({d.device_id: d for d in devices if d.status is not DeviceStatus.ONLINE})
    return list(keep.values())


def map_figure(frame: pd.DataFrame, tiles: bool = False) -> go.Figure:
    """The fleet plotted by position.

    The default draws the devices on their own coordinates and fetches nothing, so the
    Control Room renders on a machine with no internet. Tiled basemaps are opt-in.
    """
    color_map = {STATUS_LABEL[s]: c for s, c in STATUS_COLOR.items()}
    hover = {
        "Site": True,
        "Zone": True,
        "Assigned kW": True,
        "SoC %": True,
        "lat": False,
        "lon": False,
        "size": False,
    }
    if tiles:
        fig = px.scatter_map(
            frame,
            lat="lat",
            lon="lon",
            color="Status",
            size="size",
            size_max=20,
            color_discrete_map=color_map,
            hover_name="Device",
            hover_data=hover,
            zoom=4.5,
            center={"lat": 30.8, "lon": -97.5},
            height=430,
        )
        fig.update_layout(
            map_style="carto-darkmatter",
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            legend={"orientation": "h", "y": -0.05},
        )
        return fig

    fig = px.scatter(
        frame,
        x="lon",
        y="lat",
        color="Status",
        size="size",
        size_max=20,
        color_discrete_map=color_map,
        hover_name="Device",
        hover_data=hover,
        height=430,
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        legend={"orientation": "h", "y": -0.05},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0f1620",
        xaxis={"title": None, "showgrid": True, "gridcolor": "#1e2937", "zeroline": False},
        yaxis={
            "title": None,
            "showgrid": True,
            "gridcolor": "#1e2937",
            "zeroline": False,
            "scaleanchor": "x",
        },
    )
    return fig


def render_map(eng: ControlRoomEngine) -> None:
    snap = eng.snapshot()
    shown = map_devices(snap.devices)
    frame = pd.DataFrame(
        [
            {
                "Device": d.device_id,
                "Status": STATUS_LABEL[d.status],
                "Site": d.site,
                "Zone": d.zone,
                "lat": d.lat,
                "lon": d.lon,
                "Assigned kW": d.assigned_kw,
                "SoC %": round(d.state_of_charge * 100),
                "size": 26 if d.device_id == FOCUS_DEVICE_ID else 11,
            }
            for d in shown
        ]
    )
    use_tiles = bool(st.session_state.get("map_tiles", False))
    st.plotly_chart(map_figure(frame, tiles=use_tiles), use_container_width=True)
    st.checkbox(
        "Use online map tiles (needs internet)",
        key="map_tiles",
        help=(
            "Off by default: the fleet plots from its own simulated coordinates, so the "
            "Control Room works with no network at all."
        ),
    )

    if len(shown) < len(snap.devices):
        caption(
            f"Map shows {len(shown):,} of {len(snap.devices):,} devices: every unhealthy "
            "device plus a sample of healthy ones."
        )

    st.markdown("<div class='gs-kicker'>Device grid</div>", unsafe_allow_html=True)
    tiles = snap.devices[:GRID_TILES]
    per_row = 12
    for start in range(0, len(tiles), per_row):
        row = st.columns(per_row)
        for col, device in zip(row, tiles[start : start + per_row], strict=False):
            focus = device.device_id == FOCUS_DEVICE_ID
            border = "2px solid #f87171" if focus else "1px solid #263041"
            col.markdown(
                f"<div title='{device.site} — {STATUS_LABEL[device.status]} — "
                f"{device.assigned_kw:.1f} kW' style='background:{STATUS_COLOR[device.status]}22;"
                f"border:{border};border-radius:8px;padding:.35rem .2rem;text-align:center;'>"
                f"<div style='font-size:.62rem;color:#9fb0c6'>{device.device_id}</div>"
                f"<div style='height:6px;width:6px;border-radius:50%;margin:.25rem auto 0;"
                f"background:{STATUS_COLOR[device.status]}'></div></div>",
                unsafe_allow_html=True,
            )
    if len(tiles) < len(snap.devices):
        caption(f"First {len(tiles)} tiles of {len(snap.devices):,} simulated devices.")


def render_prices(eng: ControlRoomEngine) -> None:
    trace = eng.prices
    ev = eng.grid_event
    frame = trace.frame.rename(columns={"interval_start": "Interval", "spp": "$/MWh"})
    fig = px.line(frame, x="Interval", y="$/MWh", height=230)
    fig.update_traces(line={"color": "#38bdf8", "width": 2})
    fig.add_vrect(
        x0=ev.started_at,
        x1=ev.ends_at,
        fillcolor="#f59e0b",
        opacity=0.18,
        line_width=0,
        annotation_text="grid event",
        annotation_position="top left",
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
    caption(
        f"Real ERCOT {trace.market} settlement point prices, {trace.location}, {trace.date}. "
        f"Peak ${trace.peak_mwh:,.2f}/MWh, day average ${trace.mean_mwh:,.2f}/MWh. "
        "Cached locally as Parquet so the demo runs offline; the fleet itself is simulated."
    )


def render_incident(eng: ControlRoomEngine) -> None:
    snap = eng.snapshot()
    st.subheader("Active incident")
    if not snap.incidents:
        st.markdown(
            "<div class='gs-card'><div class='gs-title'>No active incidents</div>"
            "<div class='gs-body'>Fleet is stable and the dispatch plan is fully covered. "
            "Use <b>Trigger BAT-042 Failure</b> in Demo Controls to run the scenario.</div></div>",
            unsafe_allow_html=True,
        )
        return

    incident = snap.incidents[-1]
    status_color = {
        IncidentStatus.DETECTED: "#f59e0b",
        IncidentStatus.AWAITING_APPROVAL: "#dc2626",
        IncidentStatus.RECOVERING: "#0ea5e9",
        IncidentStatus.RESOLVED: "#16a34a",
    }[incident.status]
    st.markdown(
        f"<div class='gs-card'>"
        f"{pill(incident.severity.value.upper(), SEVERITY_COLOR[incident.severity])} "
        f"{pill(incident.status.value.replace('_', ' ').title(), status_color)} "
        f"<div class='gs-title' style='margin-top:.5rem'>{incident.incident_id} — "
        f"{incident.title}</div>"
        f"<div class='gs-body'><b>Root-cause hypothesis:</b> "
        f"{incident.root_cause_hypothesis}</div>"
        f"<div class='gs-body' style='margin-top:.4rem'><b>Operational impact:</b> "
        f"{incident.impact}</div>"
        f"<div class='gs-body' style='margin-top:.4rem'><b>Recommended action:</b> "
        f"{incident.recommended_action}</div>"
        f"<div class='gs-body' style='margin-top:.4rem'><b>Owner:</b> {incident.owner}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    render_jev(eng, incident)
    render_money(incident)
    render_approval(eng, incident)


def jev_answer_rows(response: JevResponse) -> list[dict[str, object]]:
    rows = []
    for question_id, answer in sorted(response.answers.items()):
        if question_id == ROOT_CAUSE:
            question = "Root cause"
        elif question_id == BACKUP_RISK:
            question = "Risk to member backup"
        else:
            question = f"{question_id[len(TRUST_PREFIX) :]} trustworthy?"
        top = sorted(answer.probabilities.items(), key=lambda kv: -kv[1])[:3]
        rows.append(
            {
                "question": question,
                "answer": answer.value.replace("_", " "),
                "confidence": round(answer.confidence, 2),
                "probabilities": ", ".join(f"{k} {v:.2f}" for k, v in top),
                "latency (ms)": round(response.latency_ms, 1),
            }
        )
    return rows


def gate_reading(decision: ApprovalDecision) -> str:
    """What the confidence gate read, said so it cannot be mistaken for an approval."""
    policy = ApprovalPolicy()
    verdict = "clears the bar" if decision.gate_clear else "below the bar"
    return (
        f"Jev's answers {verdict} (confidence ≥ {policy.confidence_threshold:.2f}, backup risk "
        f"≤ {policy.max_backup_risk:.2f}, under ${policy.dollar_cap:,.0f} at stake): "
        f"{decision.reason}. A human approves either way — Jev has no approve path."
    )


def render_jev_card(response: JevResponse, decision: ApprovalDecision) -> None:
    """Jev's answers, confidence and latency, and what the confidence gate read."""
    badges = " ".join(
        [
            pill(JEV_LABEL[response.source], JEV_COLOR[response.source]),
            pill(response.model, "#475569"),
            pill(f"{response.latency_ms:.0f} ms", "#475569"),
            pill("human approval required", "#dc2626"),
        ]
    )
    st.markdown(
        f"<div class='gs-card'><div class='gs-kicker'>Second opinion</div>{badges}"
        f"<div class='gs-body' style='margin-top:.5rem'>{gate_reading(decision)}</div></div>",
        unsafe_allow_html=True,
    )
    st.dataframe(pd.DataFrame(jev_answer_rows(response)), hide_index=True, use_container_width=True)


def principle_rows(verdict: Verdict) -> list[dict[str, object]]:
    """One row per operator principle, in the priority order the YAML pack fixes."""
    return [
        {
            "principle": row.principle.title,
            "weighs as": "hard veto" if row.principle.type is PrincipleType.VETO else "soft",
            "question": row.principle.question,
            "answer": row.answer,
            "probability safe": round(row.satisfied, 2),
            "Jev confidence": round(row.confidence, 2),
            # One type per column: a float next to an em dash makes Streamlit fall back
            # from Arrow and log a serialisation failure on every render.
            "weight": f"{row.weight:.2f}" if row.weight else "—",
        }
        for row in verdict.breakdown
    ]


ACTION_COLOR = {
    Action.ACT: "#16a34a",
    Action.ACT_AND_NOTIFY: "#f59e0b",
    Action.ASK_A_HUMAN: "#dc2626",
}


@st.cache_data(show_spinner=False)
def blind_pack() -> tuple[int, int, int]:
    """Blind score of the pre-committed safety pack: rules correct, Jev correct, questions."""
    report = judgment_report.cached_build()
    rules = report.blind[judgment_report.RULES]
    jev = report.blind[judgment_report.JEV]
    return rules.correct, jev.correct, rules.total


def render_principles(verdict: Verdict, decision: ApprovalDecision | None = None) -> None:
    """The one verdict on this incident: the principles, the gate reading and who approves.

    The gate reading lives in this card rather than a second one of its own, so the badge
    and the Approve button can never state different outcomes for the same incident.
    """
    badges = " ".join(
        [
            pill(verdict.action.value, ACTION_COLOR[verdict.action]),
            pill(JEV_LABEL[verdict.source], JEV_COLOR[verdict.source]),
            pill(f"score {verdict.score:.2f} vs bar {verdict.bar:.2f}", "#475569"),
            pill("human approval required", "#dc2626"),
        ]
    )
    gate = (
        f"<div class='gs-body' style='margin-top:.35rem'><b>Gate:</b> {gate_reading(decision)}"
        f"</div>"
        if decision is not None
        else ""
    )
    st.markdown(
        f"<div class='gs-card'><div class='gs-kicker'>Verdict</div>{badges}"
        f"<div class='gs-body' style='margin-top:.5rem'><b>Why:</b> {verdict.reason}</div>"
        f"{gate}"
        f"<div class='gs-body' style='margin-top:.35rem'>Three hard vetoes, then three "
        f"weighted principles; the certainty bar rises with the money at stake. Weights "
        f"and bars are calibrated on <b>simulated</b> operator overrides, not on real "
        f"Base operators.</div></div>",
        unsafe_allow_html=True,
    )
    st.dataframe(pd.DataFrame(principle_rows(verdict)), hide_index=True, use_container_width=True)
    rules_correct, jev_correct, questions = blind_pack()
    caption(
        f"The rules and the hard vetoes decide; the model is a second opinion that escalates "
        f"when it disagrees, and it can never approve. On the safety pack committed before it "
        f"was scored the rules fallback answered {rules_correct} of {questions} and Jev "
        f"{jev_correct} of {questions} (`python -m gridsignal.judgment_report`)."
    )


def render_jev(eng: ControlRoomEngine, incident: Incident) -> None:
    """One verdict for the incident, then the answers it was read from."""
    response, decision = jev_incident.ask(eng, incident)
    _, _, verdict = jev_incident.judge_incident(eng, incident)
    render_principles(verdict, decision)
    st.dataframe(pd.DataFrame(jev_answer_rows(response)), hide_index=True, use_container_width=True)


def render_money(incident: Incident) -> None:
    """Price the lost capacity against the real settlement prices for the window."""
    risk, recovered, scale = st.columns(3)
    metric(
        risk,
        "Dollars at risk",
        money(incident.dollars_at_risk),
        note=(
            f"{incident.lost_kw:.1f} kW x {incident.window_hours:.2f} h "
            f"x ${incident.price_mwh:,.2f}/MWh"
        ),
    )
    if incident.status is IncidentStatus.RESOLVED:
        metric(
            recovered,
            "Dollars recovered",
            money(incident.dollars_recovered),
            note=f"{incident.restored_kw:.1f} kW reassigned after approval",
        )
    else:
        metric(
            recovered,
            "Dollars recovered",
            "$0.00",
            note="pending operator approval",
        )
    metric(
        scale,
        "Devices in outage",
        f"{len(incident.cohort) or 1:,}",
        note="one gateway firmware ring",
    )


def render_approval(eng: ControlRoomEngine, incident: Incident) -> None:
    if incident.status is IncidentStatus.AWAITING_APPROVAL:
        st.warning(
            "Human approval required. The orchestrator has planned the recovery but will not "
            "reassign any capacity until an operator approves."
        )
        if st.button("Approve Recovery Plan", type="primary", use_container_width=True):
            eng.approve_recovery()
            st.rerun()
    elif incident.status is IncidentStatus.RESOLVED:
        # The summary itself is the recover step of the timeline and an audit entry; a
        # third copy here is what made the screen read as three different reports.
        st.success(
            f"Recovery complete. Approved by {incident.approved_by} at "
            f"{incident.approved_at:%H:%M:%S}, resolved at {incident.resolved_at:%H:%M:%S}."
        )


def render_tasks(eng: ControlRoomEngine) -> None:
    st.markdown("<div class='gs-kicker'>Who is doing what</div>", True)
    tasks = eng.snapshot().tasks
    if not tasks:
        st.markdown(
            "<div class='gs-card'><div class='gs-body'>No tasks assigned. Roles on call: "
            "Fleet Operator, Reliability Engineer, Field Support.</div></div>",
            unsafe_allow_html=True,
        )
        return
    for task in tasks:
        label, color = TASK_BADGE[task.status]
        st.markdown(
            f"<div class='gs-card'><div class='gs-kicker'>{task.role.value}</div>"
            f"<div class='gs-title'>{task.title} {pill(label, color)}</div>"
            f"<div class='gs-body'>{task.detail}<br/><span style='color:#7f8da3'>"
            f"{task.task_id} · {task.owner}</span></div></div>",
            unsafe_allow_html=True,
        )


def render_audit(eng: ControlRoomEngine) -> None:
    st.subheader("Audit timeline (append-only)")
    for event in reversed(eng.snapshot().audit):
        st.markdown(
            f"<div class='gs-card' style='padding:.6rem .9rem'>"
            f"<div class='gs-kicker'>{event.at:%H:%M:%S} · {event.actor} · {event.kind}</div>"
            f"<div class='gs-title' style='margin-bottom:.15rem'>{event.summary}</div>"
            f"<div class='gs-body'>{event.detail}</div></div>",
            unsafe_allow_html=True,
        )


STAGE_COLOR = {
    "detect": "#f59e0b",
    "diagnose": "#0ea5e9",
    "approve": "#7c3aed",
    "reassign": "#2563eb",
    "recover": "#16a34a",
}


def render_alarm_grouping(eng: ControlRoomEngine) -> None:
    """What a per-device monitor would page about, and what the operator reads instead."""
    st.markdown("<div class='gs-kicker'>Alarms grouped into incidents</div>", True)
    report = workflow.group_alarms(eng.alarms)
    if not report.alarms:
        st.markdown(
            "<div class='gs-card'><div class='gs-body'>No alarms. Trigger the gateway "
            "failure in Demo Controls to see grouping.</div></div>",
            unsafe_allow_html=True,
        )
        return
    cols = st.columns(3)
    metric(cols[0], "Raw alarms", f"{report.alarms:,}")
    metric(cols[1], "Incidents to work", f"{report.incidents:,}")
    metric(
        cols[2],
        "Alarms per incident",
        f"{report.after:,.1f}",
        note=f"{report.before:.1f} before grouping",
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Gateway ring": g.ring,
                    "Alarms": g.alarms,
                    "Devices": len(g.device_ids),
                    "Symptoms": ", ".join(k.replace("_", " ") for k in g.kinds),
                    "kW": round(g.kw),
                }
                for g in report.groups
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    caption(f"{report.headline}. Reproduce with `python -m gridsignal.workflow`.")


def render_timeline(eng: ControlRoomEngine) -> None:
    """The incident as one story: detect, diagnose, approve, reassign, recover, dollars."""
    st.markdown("<div class='gs-kicker'>Incident timeline</div>", True)
    snap = eng.snapshot()
    incident = snap.incidents[-1] if snap.incidents else None
    steps = workflow.timeline(eng.audit, incident)
    if not steps:
        st.markdown(
            "<div class='gs-card'><div class='gs-body'>Nothing has happened yet.</div></div>",
            unsafe_allow_html=True,
        )
        return
    for step in steps:
        st.markdown(
            f"<div class='gs-card' style='padding:.6rem .9rem'>"
            f"{pill(step.stage.upper(), STAGE_COLOR[step.stage])} "
            f"<span class='gs-kicker'>{step.elapsed} · {step.actor}</span>"
            f"<div class='gs-title' style='margin:.15rem 0'>{step.summary}</div>"
            f"<div class='gs-body'>{step.detail}</div></div>",
            unsafe_allow_html=True,
        )
    if incident is not None:
        st.markdown(
            f"<div class='gs-card'>{pill('DOLLARS', '#16a34a')}"
            f"<div class='gs-title' style='margin-top:.35rem'>"
            f"{money(incident.dollars_at_risk, cents=False)} at risk · "
            f"{money(incident.dollars_recovered, cents=False)} recovered</div>"
            f"<div class='gs-body'>{incident.impact}</div></div>",
            unsafe_allow_html=True,
        )


def render_override(eng: ControlRoomEngine) -> None:
    """Any award can be changed by hand — with a reason, and the reason is logged."""
    st.markdown("<div class='gs-kicker'>Override an award</div>", True)
    choices = [d for d in eng.devices if d.is_operator_controlled][:25]
    if not choices:
        return
    device_id = st.selectbox(
        "Battery",
        [d.device_id for d in choices],
        format_func=lambda i: f"{i} — {eng.device(i).assigned_kw:,.2f} kW awarded",
        key="override_device",
    )
    kw = st.number_input("New award (kW)", min_value=0.0, step=0.5, value=0.0, key="override_kw")
    reason = st.text_input("Reason (required, goes in the audit trail)", key="override_reason")
    if st.button("Override award", key="override_submit"):
        try:
            record = eng.override_award(device_id, float(kw), reason)
        except workflow.OverrideError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"{record.device_id}: {record.kw_before:,.2f} kW → {record.kw_after:,.2f} kW "
                f"({record.delta_kw:+,.2f} kW), logged with your reason."
            )
    if eng.overrides:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "At": f"{o.at:%H:%M:%S}",
                        "Battery": o.device_id,
                        "kW before": o.kw_before,
                        "kW after": o.kw_after,
                        "Operator": o.operator,
                        "Reason": o.reason,
                    }
                    for o in eng.overrides
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )


def render_workflow(eng: ControlRoomEngine) -> None:
    """One operator surface: grouped alarms, the timeline, and a logged override."""
    st.subheader("Operator workflow")
    left, right = st.columns([3, 2], gap="large")
    with left:
        render_timeline(eng)
    with right:
        render_alarm_grouping(eng)
        render_override(eng)
        render_tasks(eng)


def render_scenario_controls() -> None:
    """Price scenario and fleet scale. Changing either rebuilds the simulation."""
    with st.sidebar:
        st.radio("View", VIEWS, key="view", horizontal=True)
        st.header("Scenario")
        scenarios = available_scenarios()
        keys = [s.key for s in scenarios]
        labels = {s.key: s.label for s in scenarios}
        st.radio(
            "ERCOT price day",
            keys,
            format_func=lambda k: labels[k],
            key="scenario",
            index=keys.index(DEFAULT_SCENARIO) if DEFAULT_SCENARIO in keys else 0,
        )
        chosen = next(s for s in scenarios if s.key == st.session_state.get("scenario", keys[0]))
        caption(chosen.blurb)
        st.radio(
            "Fleet scale",
            FLEET_SIZES,
            format_func=lambda n: f"{n:,} devices",
            key="fleet_size",
        )
        caption(
            "A gateway firmware ring covers one device per 48, so the same failure takes "
            "out more of the fleet — and more revenue — as the fleet grows."
        )
        st.divider()


@st.cache_data(show_spinner=False)
def congestion_rank() -> dict[str, int]:
    """Settlement zone -> its place in the congestion-aware discharge order."""
    if not congestion.bundled_days():
        return {}
    return {zone: i + 1 for i, zone in enumerate(congestion.dispatch_order(congestion_basis()))}


def render_dispatch_priority(eng: ControlRoomEngine) -> None:
    """Let the operator discharge one congested zone's batteries before the rest."""
    ranks = congestion_rank()
    zones = sorted({d.zone for d in eng.devices})
    options: list[str | None] = [
        None,
        *sorted(zones, key=lambda z: ranks.get(settlement_zone(z), 99)),
    ]

    def label(zone: str | None) -> str:
        if zone is None:
            return "Share by headroom (no preference)"
        place = ranks.get(settlement_zone(zone))
        suffix = f" — congestion rank #{place}" if place else ""
        return f"{zone}{suffix}"

    st.markdown("<div class='gs-kicker'>Congestion dispatch preference</div>", True)
    choice = st.selectbox(
        "Discharge first under congestion",
        options,
        format_func=label,
        key="priority_zone",
        label_visibility="collapsed",
    )
    if choice != eng.priority_zone:
        eng.set_priority_zone(choice)
        st.rerun()

    snap = eng.snapshot()
    in_zone = [d for d in eng.devices if d.zone == eng.priority_zone and d.assigned_kw > 0]
    cols = st.columns(2)
    metric(cols[0], "Committed", power(snap.committed_kw), note=f"{snap.coverage_pct:.0f}%")
    metric(
        cols[1],
        "Discharging first",
        f"{len(in_zone):,} devices" if eng.priority_zone else "whole fleet",
        note=power(sum(d.assigned_kw for d in in_zone)) if in_zone else "by headroom",
    )
    caption(
        "Zone order comes from the bundled ERCOT basis in Grid Signals: the zone that "
        "priced furthest above the hub average goes first. The target, the member "
        "reserve and the approval gate are unchanged — this only decides who carries the "
        "commitment first, in simulation."
    )


def render_demo_controls(eng: ControlRoomEngine) -> None:
    with st.sidebar:
        st.header("Demo Controls")
        caption("Judges can replay the story without reloading the page.")
        pending_or_done = any(i.device_id == FOCUS_DEVICE_ID for i in eng.incidents)
        if st.button(
            f"Trigger {FOCUS_DEVICE_ID} Failure",
            use_container_width=True,
            disabled=pending_or_done,
        ):
            eng.trigger_device_failure(FOCUS_DEVICE_ID)
            st.rerun()
        if st.button("Reset Demo", use_container_width=True):
            eng.reset()
            st.rerun()
        st.divider()
        st.markdown("**Scenario**")
        st.markdown(
            "1. Stable fleet during an ERCOT peak event\n"
            f"2. {FOCUS_DEVICE_ID} loses telemetry\n"
            "3. Incident opened, impact + plan explained\n"
            "4. **Human approves** the plan\n"
            "5. Work reassigned, device quarantined\n"
            "6. Audit timeline records everything"
        )
        st.divider()
        caption(
            "Safety boundary: this tool is a simulation. It performs no dispatch, "
            "no device commands and no utility integration. Recovery executes only "
            "after explicit human approval."
        )


def render_signal_chart(plan: pd.DataFrame) -> None:
    """Real-time price, the day-ahead curve it is judged against, and the action taken."""
    reference, label = (
        ("dam_mwh", "day-ahead $/MWh")
        if "dam_mwh" in plan.columns
        else ("reservation_mwh", "reservation price")
    )
    fig = px.line(plan, x="interval_start", y="spp", height=320, log_y=True)
    fig.update_traces(line={"color": "#64748b", "width": 1.5}, name="$/MWh")
    fig.add_scatter(
        x=plan["interval_start"],
        y=plan[reference],
        mode="lines",
        line={"color": "#a78bfa", "width": 1, "dash": "dot"},
        name=label,
    )
    for action, color in SIGNAL_COLOR.items():
        rows = plan[plan["signal"] == action]
        fig.add_scatter(
            x=rows["interval_start"],
            y=rows["spp"],
            mode="markers",
            marker={"color": color, "size": 8},
            name=action,
        )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title="$/MWh (log)",
        xaxis_title=None,
        legend={"orientation": "h", "y": -0.2},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_money_chart(ledger: pd.DataFrame) -> None:
    money = ledger.melt(
        id_vars="interval_start",
        value_vars=["signal_cum_usd", "naive_cum_usd"],
        var_name="strategy",
        value_name="usd",
    ).replace({"signal_cum_usd": "GridSignal", "naive_cum_usd": "Naive schedule"})
    fig = px.line(
        money,
        x="interval_start",
        y="usd",
        color="strategy",
        height=280,
        color_discrete_map={"GridSignal": "#4ade80", "Naive schedule": "#94a3b8"},
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title="$ per battery",
        xaxis_title=None,
        legend={"orientation": "h", "y": -0.2},
    )
    st.plotly_chart(fig, use_container_width=True)


def render_holdout() -> None:
    """Out-of-sample scorecard: the same frozen policy on days it never saw."""
    results = holdout_run()
    st.subheader("Held-out days (parameters frozen, never tuned on these)")
    if not results:
        caption(
            "No held-out days bundled. Run python scripts/fetch_holdout.py "
            "with the [ercot] extra to cache them."
        )
        return

    summary = holdout.summarize(results)
    cols = st.columns(4)
    metric(cols[0], "Days scored", f"{summary.days}")
    metric(
        cols[1],
        "Days beating the clock baseline",
        ratio(summary.days_won, summary.days),
    )
    metric(
        cols[2],
        "Mean uplift",
        money(summary.mean_uplift_usd),
        note="per battery per day",
    )
    metric(
        cols[3],
        "Worst day",
        money(summary.worst_uplift_usd),
        note="per battery",
    )

    frame = holdout.as_frame(results)
    st.dataframe(
        frame.rename(
            columns={
                "date": "Date",
                "peak_mwh": "Peak $/MWh",
                "mean_mwh": "Mean $/MWh",
                "spikes": "Spike intervals",
                "signal_usd": "GridSignal $",
                "naive_usd": "Naive $",
                "uplift_usd": "Uplift $",
                "member_savings_usd": "Member savings $",
            }
        ).style.format(
            {
                "Peak $/MWh": "{:,.2f}",
                "Mean $/MWh": "{:,.2f}",
                "GridSignal $": "{:,.2f}",
                "Naive $": "{:,.2f}",
                "Uplift $": "{:+,.2f}",
                "Member savings $": "{:,.2f}",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    caption(
        "Every figure is per battery per day on real cached LZ_HOUSTON 15-minute RTM "
        "settlement prices. Windows are planned from the day-ahead curve published "
        "the afternoon before; real time only overrides the plan. The policy "
        "parameters were fitted on the two scenario days plus data/tuning and were "
        "not adjusted after seeing these results, so losing days are shown as they "
        "came out."
    )
    render_lookahead_correction(results)
    render_home_first_cost(results)
    render_degradation()


@st.cache_data(show_spinner=False)
def degradation_run() -> list[degradation.SplitResult]:
    """Deterministic on cached prices, so caching the whole sweep is safe."""
    return degradation.evaluate_all()


def render_degradation() -> None:
    """Wear-aware dispatch: what a cycle costs, and which days are not worth taking."""
    results = degradation_run()
    st.markdown("#### Degradation-aware dispatch, by battery type")
    caption(
        "A cycle is only taken when the expected spread covers the energy and the "
        "wear of moving it. The wear cost is an assumption of this build \u2014 pack "
        "replacement over modelled cycle life \u2014 not a vendor figure: "
        + "; ".join(f"{m.label}, {m.assumption}" for m in degradation.WEAR_MODELS)
        + "."
    )
    frame = pd.DataFrame(
        [
            {
                "Split": r.split,
                "Battery": r.model.label,
                "Gross $": r.gross_usd,
                "Wear $": r.wear_usd,
                "Net $": r.net_usd,
                "Net after gate $": r.gated_net_usd,
                "Cycles": r.cycles,
                "Cycles saved": r.cycles_saved,
                "Days won": f"{r.days_won_ungated} \u2192 {r.days_won} of {r.days}",
            }
            for r in results
        ]
    )
    st.dataframe(
        frame.style.format(
            {
                "Gross $": "{:+,.2f}",
                "Wear $": "{:,.2f}",
                "Net $": "{:+,.2f}",
                "Net after gate $": "{:+,.2f}",
                "Cycles": "{:,.2f}",
                "Cycles saved": "{:,.2f}",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    for result in results:
        caption(result.verdict)
    caption(
        "Dollars are per battery per day against the naive schedule; wear is charged on "
        "the throughput the policy adds over that schedule, so cycling less is credited. "
        "Reproduce with python -m gridsignal.degradation."
    )


def render_lookahead_correction(results: list[holdout.DayResult]) -> None:
    """The old scoring read a price that had not settled yet; both results are shown."""
    corrected = holdout.summarize(results)
    as_first = holdout.summarize(holdout_as_first_scored())
    cols = st.columns(2)
    metric(
        cols[0],
        "Corrected: day-ahead and last settled print only",
        money(corrected.mean_uplift_usd),
        note=wins(corrected.days_won, corrected.days),
    )
    metric(
        cols[1],
        "As first scored (same-interval price)",
        money(as_first.mean_uplift_usd),
        note=wins(as_first.days_won, as_first.days),
    )
    caption(
        "A real-time price is published only after its interval is over, so the "
        "policy now decides each interval from the day-ahead curve and the last "
        "settled print. The table above is the corrected run; the figure on the "
        "right is the number as first scored, kept so the change is visible. "
        "Reproduce both with python -m gridsignal.holdout."
    )


def render_home_first_cost(results: list[holdout.DayResult]) -> None:
    """What home-first dispatch costs in export revenue, stated rather than buried."""
    grid_only = holdout.summarize(holdout_grid_only())
    home_first = holdout.summarize(results)
    savings = round(sum(r.member_savings_usd for r in results) / max(len(results), 1), 2)
    cols = st.columns(3)
    metric(
        cols[0],
        "Grid-only battery",
        money(grid_only.mean_uplift_usd),
        note=wins(grid_only.days_won, grid_only.days),
    )
    metric(
        cols[1],
        "Home-first battery",
        money(home_first.mean_uplift_usd),
        note=wins(home_first.days_won, home_first.days),
    )
    metric(
        cols[2],
        "Member savings, home-first",
        money(savings),
        note="energy the house did not buy",
    )
    caption(
        "Serving the house first costs export revenue, and the table above is the "
        "home-first result. On the scarcity held-out day the grid-only battery earns "
        "far more, because the household load eats the stored energy that a pure "
        "trading battery would have sold into the spike. That is the trade the "
        "product makes on purpose: the member keeps the energy and the backup."
    )


def render_mutual_aid(eng: ControlRoomEngine) -> None:
    """Neighbour mutual aid: opted-in members topping up a medical-device neighbour."""
    st.markdown("<div class='gs-kicker'>Neighbour mutual aid (simulated)</div>", True)
    recipient = member.aid_candidate(eng)
    plan = member.aid_plan(eng, recipient) if recipient else None
    if plan is None or not plan.transfers:
        st.markdown(
            "<div class='gs-card'><div class='gs-body'>No neighbour needs help right now. "
            "If a member who runs a medical device is short of backup during an island, "
            "opted-in neighbours are asked to share only the energy they hold above their "
            "own reserve.</div></div>",
            unsafe_allow_html=True,
        )
        return
    givers = ", ".join(f"{t.from_device} ({t.kwh:.2f} kWh)" for t in plan.transfers)
    st.markdown(
        f"<div class='gs-card' style='border-left:4px solid #38bdf8'>"
        f"<div class='gs-title'>{len(plan.transfers)} neighbours can send "
        f"{plan.shared_kwh:.2f} kWh to {plan.recipient}</div>"
        f"<div class='gs-body'>{plan.recipient} runs a medical device and has opted into "
        f"sharing. {givers} each stay above their own backup reserve. That takes "
        f"{plan.recipient} from {plan.hours_before:.1f} to {plan.hours_after:.1f} hours of "
        f"backup, {plan.hours_gained:+.1f} h. Simulated: nothing is moved until both "
        f"members confirm.</div></div>",
        unsafe_allow_html=True,
    )


def render_member(eng: ControlRoomEngine) -> None:
    """The same incident from the member's side: backup, dollars, plain-English notice."""
    devices = [FOCUS_DEVICE_ID] + [
        d.device_id for d in eng.devices[:GRID_TILES] if d.device_id != FOCUS_DEVICE_ID
    ]
    with st.sidebar:
        st.header("Member")
        st.selectbox("Home", devices, key="member_device")
        caption("Same simulation as the Control Room, seen from one house.")
        st.divider()

    device_id = st.session_state.get("member_device", FOCUS_DEVICE_ID)
    view = member.member_summary(eng, device_id)
    banner = "#dc2626" if view.is_affected else "#16a34a"

    st.subheader(f"Base Power — {view.site}")
    st.markdown(
        f"<div class='gs-card' style='border-left:4px solid {banner}'>"
        f"<div class='gs-kicker'>Notice about your system</div>"
        f"<div class='gs-title'>{view.headline}</div>"
        f"<div class='gs-body'>{view.body}</div>"
        f"<div class='gs-body' style='margin-top:.5rem;color:#e6edf6'>"
        f"<b>{view.next_step}</b></div></div>",
        unsafe_allow_html=True,
    )

    stale = view.is_affected  # no live readings: every number below is a last-known value
    cols = st.columns(4)
    metric(
        cols[0],
        "Whole-home backup, last reported" if stale else "Whole-home backup left",
        hours(view.backup_hours),
        note=(
            "from the last reading before contact was lost"
            if stale
            else (
                f"{view.backup_hours_with_generator:.1f} h with your generator"
                if view.generator_kwh > 0
                else f"{view.backup_kwh:,.1f} kWh reserved for you"
            )
        ),
    )
    metric(
        cols[1],
        "Powering your home, last reported" if stale else "Powering your home now",
        power(view.home_load_kw, 1),
        note=f"{view.discharge_kw:,.1f} kW discharging in total",
    )
    metric(
        cols[2],
        "Exported to the grid",
        power(view.export_kw, 1),
        note=f"earned ${view.earned_usd:,.2f} of ${view.grid_value_usd:,.2f}",
    )
    metric(
        cols[3],
        "Helped protect",
        money(view.protected_usd),
        note="covering a neighbour's outage",
    )

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("<div class='gs-kicker'>Where your stored energy is going</div>", True)
        split = pd.DataFrame(
            {
                "use": ["Reserved for your home", "Offered to the grid event"],
                "kwh": [view.backup_kwh, view.committed_kwh],
            }
        )  # your house is served first; only what is left is offered to the grid
        fig = px.bar(split, x="kwh", y="use", orientation="h", height=180, text="kwh")
        fig.update_traces(marker_color=["#38bdf8", "#f59e0b"], texttemplate="%{text:.1f} kWh")
        fig.update_layout(
            margin={"l": 0, "r": 0, "t": 6, "b": 0},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title=None,
            yaxis_title=None,
        )
        st.plotly_chart(fig, use_container_width=True)
        caption(
            f"{view.stored_kwh:,.1f} kWh stored right now, with at least "
            f"{view.reserve_kwh:,.1f} kWh held back for you. Backup hours assume a "
            f"{member.ESSENTIAL_LOAD_KW:.1f} kW essential household load and your "
            f"earnings assume a {member.MEMBER_REVENUE_SHARE:.0%} member revenue share "
            "— both are assumptions in this simulation, not a Base Power tariff."
        )
        if view.generator_kwh > 0:
            st.info(
                f"Your portable generator adds about {view.generator_kwh:,.1f} kWh, worth "
                f"another {view.generator_hours:.1f} hours if an outage runs long "
                "(simulated, and only counted while it has fuel)."
            )
        render_mutual_aid(eng)
    with right:
        st.markdown("<div class='gs-kicker'>Your neighbourhood</div>", True)
        peers = member.neighbours(eng, device_id)
        st.markdown(
            "<div class='gs-card'><div class='gs-body'>"
            + (f"Batteries at {', '.join(peers)} are on the same load zone. " if peers else "")
            + "When one home drops out, the others pick up its share — that is why your "
            "bill and your backup do not move when a single gateway fails."
            "</div></div>",
            unsafe_allow_html=True,
        )
        caption(
            "You never see incident IDs, kW targets or operator tooling here. "
            "Recovery decisions stay with a human operator in the Control Room."
        )


def signed_usd(amount: float) -> str:
    """Dollars with the sign outside the symbol, as a person would write it."""
    return f"{'-' if amount < 0 else '+'}${abs(amount):,.2f}"


def wins(won: int, days: int) -> str:
    return f"mean uplift, wins {ratio(won, days)} days"


def render_insight() -> None:
    """The Open Grid Data headline: what the day-ahead curve did not tell you."""
    results = insight_run()
    if not results:
        return
    s = insight.summarize(results)

    st.markdown(
        "<div class='gs-card gs-insight'>"
        "<div class='gs-kicker'>What most people miss in the ERCOT data</div>"
        f"<div class='gs-huge'>{s.scarcity_visible_share:.0%}</div>"
        f"<div class='gs-lead'>of a scarcity day's battery value was visible in the "
        f"day-ahead curve</div>"
        f"<div class='gs-body'>{s.headline} {s.subhead}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns(4)
    metric(
        cols[0],
        "Day-ahead share, scarcity days",
        f"{s.scarcity_visible_share:.0%}",
        note=f"{s.ordinary_visible_share:.0%} on ordinary days",
    )
    metric(
        cols[1],
        "Only visible in real time",
        money(s.scarcity_blind_usd),
        note="per battery per scarcity day",
        tooltip="Value a battery could only earn by reacting on the day, not the day before.",
    )
    metric(
        cols[2],
        "Equivalent ordinary days",
        f"{s.ordinary_days_equivalent}",
        note=f"at {money(s.ordinary_day_usd)} each",
        tooltip="How many ordinary days it takes to earn what one scarcity day pays.",
    )
    metric(
        cols[3],
        f"Intervals {insight.dam.DEVIATION_MULTIPLE:.0f}x above day-ahead",
        ratio(s.divergent_intervals, s.intervals),
        note=f"{s.ordinary_divergent_intervals} on ordinary days",
    )

    with st.expander("Day by day: day-ahead plan vs. perfect real-time foresight"):
        frame = insight.as_frame(results)
        st.dataframe(
            frame.rename(
                columns={
                    "date": "Date",
                    "peak_mwh": "Peak $/MWh",
                    "day_ahead_usd": "Day-ahead plan $",
                    "foresight_usd": "Perfect foresight $",
                    "visible_share": "Visible day-ahead",
                    "only_real_time_usd": "Only in real time $",
                    "divergent_intervals": f"{insight.dam.DEVIATION_MULTIPLE:.0f}x intervals",
                    "max_divergence": "Max divergence",
                }
            ).style.format(
                {
                    "Peak $/MWh": "{:,.2f}",
                    "Day-ahead plan $": "{:,.2f}",
                    "Perfect foresight $": "{:,.2f}",
                    "Visible day-ahead": "{:.0%}",
                    "Only in real time $": "{:,.2f}",
                    "Max divergence": "{:,.1f}x",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
        caption(
            "Both columns settle the same real 15-minute LZ_HOUSTON prints for one "
            "13.5 kWh / 5 kW battery. Day-ahead plan = charge and export windows chosen "
            "from that date's ERCOT day-ahead curve alone, executed blind. Perfect "
            "foresight = the best single cycle available if the real-time prices had been "
            "known in advance; it is a ceiling nobody can trade, not a strategy."
        )


@st.cache_data(show_spinner=False)
def congestion_basis() -> pd.DataFrame:
    return congestion.basis_frame()


@st.cache_data(show_spinner=False)
def congestion_uplift() -> list[congestion.ZoneUplift]:
    return congestion.zone_uplift(congestion_basis())


def render_congestion_heatmap(basis: pd.DataFrame) -> None:
    grid = congestion.heatmap(basis)
    fig = px.imshow(
        grid,
        color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0.0,
        aspect="auto",
        height=340,
        labels={"x": "hour of day", "y": "", "color": "$/MWh over hub"},
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_placement(ranks: list[congestion.PlacementRank]) -> None:
    """The 'where to install next' sketch: greedy placement with visible saturation."""
    st.markdown("<div class='gs-kicker'>Where to install next (sketch)</div>", True)
    batteries = st.slider(
        "Next batteries to place",
        min_value=100,
        max_value=5_000,
        value=1_000,
        step=100,
        key="placement_batteries",
    )
    sketch = congestion.placement_sketch(batteries, ranks=ranks)
    if sketch.empty:
        caption("No bundled zone priced above the hub average, so the sketch places none.")
        return

    cols = st.columns(3)
    metric(
        cols[0],
        "Placed",
        f"{int(sketch['batteries placed'].sum()):,}",
        tooltip="Where the next batteries would sit under this sketch, not a siting plan.",
    )
    metric(
        cols[1],
        "Value at these prices",
        f"${sketch.attrs['total_usd_per_day']:,.0f}/day",
        note=congestion.HINDSIGHT,
    )
    metric(
        cols[2],
        "Top zone",
        sketch.iloc[0]["metro"],
        note=f"{int(sketch.iloc[0]['batteries placed']):,} batteries",
    )

    fig = px.bar(
        sketch,
        x="metro",
        y="batteries placed",
        height=260,
        color_discrete_sequence=["#38bdf8"],
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title=None,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        sketch.style.format(
            {
                "batteries placed": "{:,.0f}",
                "first $/battery/day (hindsight-timed)": "{:,.2f}",
                "last $/battery/day (hindsight-timed)": "{:,.2f}",
                "total $/day (hindsight-timed)": "{:,.0f}",
                "saturates at": "{:,.0f}",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    caption(
        f"Every dollar here is {congestion.HINDSIGHT_NOTE}. "
        "Data-driven sketch on a few bundled days of prices, not a forecast and not a "
        "siting study. Batteries are placed greedily into whichever zone pays most at "
        f"that moment; a zone's marginal value falls linearly to zero at its assumed "
        f"saturation point ({congestion.RELIEF_MW_PER_DOLLAR:.0f} MW of congested-hour "
        "discharge per $1/MWh of mean basis, an explicit assumption). Real siting "
        "depends on interconnection, permitting and load growth, none of which are here."
    )


def render_congestion() -> None:
    """Zonal congestion: where the power was expensive, not just when."""
    if not congestion.bundled_days():
        return
    basis = congestion_basis()
    uplifts = congestion_uplift()
    summary = congestion.summarize(basis)

    st.subheader("Congestion: West Texas generation, load-center prices")
    st.markdown(
        "<div class='gs-card gs-insight'>"
        "<div class='gs-kicker'>What most people miss when they watch one price</div>"
        f"<div class='gs-huge'>${summary.widest.hub_basis:,.0f}</div>"
        f"<div class='gs-lead'>/MWh: how far {summary.widest.metro} priced above the ERCOT "
        f"hub average in hour {summary.widest.hour:02d}</div>"
        f"<div class='gs-body'>{summary.headline} {summary.subhead}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(
            "<div class='gs-kicker'>Mean basis by zone and hour ($/MWh over the hub)</div>", True
        )
        render_congestion_heatmap(basis)
    with right:
        st.markdown("<div class='gs-kicker'>West-to-load-center spread by hour ($/MWh)</div>", True)
        spread = congestion.west_spread(basis)
        st.dataframe(
            spread.rename(columns=congestion.METRO).style.format("{:,.1f}"),
            use_container_width=True,
            height=320,
        )

    st.markdown(
        "<div class='gs-kicker'>Timing discharge to your own zone vs. the hub signal</div>", True
    )
    st.dataframe(
        congestion.uplift_frame(uplifts).style.format(
            {
                "zone-timed $/battery/day (hindsight-timed)": "{:,.2f}",
                "zone-blind $/battery/day (hindsight-timed)": "{:,.2f}",
                "uplift $ (hindsight-timed)": "{:+,.2f}",
                "ordinary-day uplift $": "{:+,.2f}",
                "scarcity-day uplift $": "{:+,.2f}",
                "mean basis $/MWh": "{:,.2f}",
                "peak basis $/MWh": "{:,.2f}",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    caption(
        f"Both columns settle the same 13.5 kWh / 5 kW battery at that zone's own real "
        f"15-minute prints across {summary.days} bundled days. The only difference is "
        "which hours were chosen: the zone's own price, which carries its congestion, or "
        "the ERCOT hub average a zone-blind operator watches. Uplift is small and it is "
        "negative in some zones; that is what these days show. Ordinary and scarcity days "
        f"are split out because their averages are nothing alike, and every figure is "
        f"{congestion.HINDSIGHT_NOTE}."
    )

    render_placement(congestion.placement_ranks(uplifts))

    with st.expander("Widest zone-hours and data provenance"):
        widest = pd.DataFrame(
            [
                {
                    "zone": h.location,
                    "metro": h.metro,
                    "hour": f"{h.hour:02d}:00",
                    "mean $ over hub": h.hub_basis,
                    "mean $ over West": h.west_basis,
                    "days": h.days,
                }
                for h in congestion.widest_hours(basis, top=10)
            ]
        )
        st.dataframe(widest, hide_index=True, use_container_width=True)
        days = congestion.bundled_days()
        meta = congestion.provenance(days[-1])
        caption(
            f"{len(days)} bundled trade days, {len(congestion.ZONES)} ERCOT load zones plus "
            f"{congestion.HUB_AVERAGE}, {meta.get('market', '')} settlement point prices from "
            f"{meta.get('source', 'ERCOT')}. Each Parquet file has a provenance sidecar "
            "(market, location, date, source, fetched_at) in data/zones/. Read-only public "
            "data; nothing here is dispatched."
        )


def render_headline(summary: BacktestSummary, date: str, fleet_size: int) -> None:
    """One scenario day next to the held-out record — never the single day on its own.

    The scarcity day is the biggest number in the project and the least representative
    one, so it is rendered beside the out-of-sample mean, median and win rate.
    """
    out = holdout.summarize(holdout_run())
    scenario_card = (
        "<div class='gs-kicker'>This scenario day, if the fleet followed the signals</div>"
        f"<div style='font-size:3.1rem;font-weight:700;color:#4ade80;line-height:1.2'>"
        f"${summary.fleet_usd(fleet_size):,.0f}</div>"
        f"<div class='gs-body'>on {date} across {fleet_size:,} simulated batteries "
        f"— ${summary.uplift_usd:,.2f} per battery per day</div>"
    )
    if out.days:
        holdout_card = (
            f"<div class='gs-kicker'>Across {out.days} held-out days the policy never saw</div>"
            f"<div style='font-size:3.1rem;font-weight:700;color:#38bdf8;line-height:1.2'>"
            f"{signed_usd(out.mean_uplift_usd)}</div>"
            f"<div class='gs-body'>mean uplift per battery per day — median "
            f"{signed_usd(out.median_uplift_usd)}, beats naive on {out.days_won} of "
            f"{out.days} days (worst {signed_usd(out.worst_uplift_usd)})</div>"
        )
    else:
        holdout_card = (
            "<div class='gs-kicker'>Held-out days</div>"
            "<div class='gs-body'>None bundled — run python scripts/fetch_holdout.py.</div>"
        )

    left, right = st.columns(2, gap="large")
    left.markdown(
        f"<div class='gs-card' style='text-align:center'>{scenario_card}</div>",
        unsafe_allow_html=True,
    )
    right.markdown(
        f"<div class='gs-card' style='text-align:center'>{holdout_card}</div>",
        unsafe_allow_html=True,
    )
    caption(
        "The scenario-day figure is one extreme day and is never the claim on its own: "
        "the held-out average beside it is what the frozen policy does on days it was "
        "never tuned on, and it can lose money on an individual day."
    )


def _price_taker_note(fleet_size: int) -> str:
    """How the unrestricted comparison's Reg Down offer sizes up against ERCOT's plan."""
    flag = ancillary.procurement_flag(devices=fleet_size)
    if flag is None:
        return (
            "Fleet dollars assume a price taker; ERCOT no longer publishes an AS plan for the "
            "bundled days, so the offer is not sized against procurement and the fleet figure "
            "is an upper bound."
        )
    return (
        f"Price-taker check on the unrestricted comparison: {fleet_size:,} simulated "
        f"batteries would offer "
        f"{flag.fleet_mw:,.0f} MW into Reg Down against {flag.procured_mw:,.0f} MW ERCOT "
        f"procured in that hour on {flag.date} ({flag.share:.0%}), so a fleet this size would "
        "move the price it is paid; fleet dollars are an upper bound, not a forecast."
    )


def render_ancillary(fleet_size: int) -> None:
    """What the same battery earns for capacity it never has to move."""
    scarcity = ancillary_day()
    summary = ancillary_holdout()
    unrestricted = ancillary_unrestricted()
    meta = ancillary.as_provenance(ancillary._trace_path(signals_run("scarcity").trace))
    caps = ancillary.pilot_cap_kw(devices=fleet_size)

    st.subheader("Ancillary co-optimization: energy and capacity from one battery")
    st.markdown(
        "<div class='gs-card gs-insight'>"
        "<div class='gs-kicker'>Capacity nobody is selling</div>"
        f"<div class='gs-huge'>${summary.median_uplift_usd:,.2f}</div>"
        "<div class='gs-lead'>median held-out day per battery, inside the ADER pilot rules</div>"
        f"<div class='gs-body'>{ancillary.headline(summary, unrestricted)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    caption(
        f"Offers are restricted to what ERCOT's {ancillary.ADER_PILOT.name} allows an "
        f"aggregation of home batteries to sell: ECRS and Non-Spin only, "
        f"{ancillary.ADER_PILOT.system_mw['ecrs']:,.0f} MW each system-wide with no QSE above "
        f"{ancillary.ADER_PILOT.qse_share:.0%} of that, which is "
        f"{caps['ecrs']:,.1f} kW per battery across {fleet_size:,} "
        f"batteries (governing document, 3 Jun 2026: {ancillary.ADER_PILOT.source})."
    )

    cols = st.columns(5)
    metric(
        cols[0],
        "Scarcity day, energy",
        money(scarcity.energy_usd),
        note="per battery",
    )
    metric(
        cols[1],
        "Scarcity day, ancillary",
        money(scarcity.ancillary_usd),
        note=f"+{money(scarcity.uplift_usd)} vs energy alone",
    )
    metric(
        cols[2],
        "Median held-out day",
        money(summary.median_uplift_usd),
        note=f"mean ${summary.mean_uplift_usd:,.2f}, concentrated in rare days",
    )
    metric(
        cols[3],
        "Outside the pilot rules",
        money(unrestricted.median_uplift_usd),
        note=f"comparison only: all five products, mean ${unrestricted.mean_uplift_usd:,.2f}",
    )
    metric(
        cols[4],
        "Backup reserve violations",
        f"{summary.reserve_violations}",
        note=f"{summary.days} held-out days",
    )

    split = pd.DataFrame(
        [
            {
                "Product": product.label,
                "Allowed": "yes" if ancillary.ADER_PILOT.allows(product) else "no",
                "Held-out $": summary.by_product[product.key],
                "Held-out $, unrestricted": unrestricted.by_product[product.key],
            }
            for product in ancillary.PRODUCTS
        ]
    )
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.dataframe(
            split.style.format({"Held-out $": "{:,.2f}", "Held-out $, unrestricted": "{:,.2f}"}),
            hide_index=True,
            use_container_width=True,
        )
        per_day = pd.DataFrame(
            [
                {
                    "Held-out day": day.date,
                    "Uplift $": day.uplift_usd,
                    "ECRS $": day.by_product["ecrs"],
                    "Non-Spin $": day.by_product["nonspin"],
                }
                for day in sorted(summary.per_day, key=lambda d: d.date)
            ]
        )
        st.dataframe(
            per_day.style.format(
                {"Uplift $": "{:,.2f}", "ECRS $": "{:,.2f}", "Non-Spin $": "{:,.2f}"}
            ),
            hide_index=True,
            use_container_width=True,
        )
        top_date, top_share = summary.top_day_share
        caption(
            f"{top_date} alone carries {top_share:.0%} of the held-out uplift: this is a "
            "rare-day product, not a daily annuity."
        )
    with right:
        awards = pd.DataFrame(
            [
                {
                    "Hour": f"{a.hour:02d}:00",
                    "Product": next(p.label for p in ancillary.PRODUCTS if p.key == a.product),
                    "kW": a.kw,
                    "$/MW-h": a.price_mw_h,
                    "$": a.usd,
                }
                for a in scarcity.awards
            ]
        )
        st.dataframe(
            awards.style.format({"kW": "{:,.2f}", "$/MW-h": "{:,.2f}", "$": "{:,.2f}"}),
            hide_index=True,
            use_container_width=True,
        )
        caption(f"{scarcity.battery}, {scarcity.date}: every hour it sold capacity.")

    caption(
        "Real ERCOT day-ahead ancillary clearing prices "
        f"({meta.get('source', ingest.AS_SOURCE_URL)}, fetched "
        f"{meta.get('fetched_at', 'n/a')[:10]}), in "
        f"{meta.get('units', '$/MW per hour of capacity held')}. Capacity payments only: "
        "deployment energy is not modelled, and every battery, load profile and award is "
        "simulated. An award is only offered when the state of charge can sustain it for "
        "the product's full duration above the member's backup reserve. "
        f"{_price_taker_note(fleet_size)} "
        "Reproduce: python -m gridsignal.ancillary"
    )


def render_grid_signals(scenario: str, fleet_size: int) -> None:
    """Spike detection, spike forecast, dispatch signals and what they were worth."""
    result = signals_run(scenario)
    summary = result.summary
    trace = result.trace

    render_insight()
    render_ancillary(fleet_size)

    st.subheader("Backtest: GridSignal vs. a naive fixed schedule")
    render_headline(summary, trace.date, fleet_size)

    cols = st.columns(4)
    metric(cols[0], "GridSignal", money(summary.signal_usd), note="per battery")
    metric(cols[1], "Clock baseline", money(summary.naive_usd), note="per battery")
    metric(cols[2], "Uplift", money(summary.uplift_usd), note=f"{summary.uplift_pct:+.0f}%")
    metric(
        cols[3],
        "Spike intervals",
        f"{int(result.detections['is_spike'].sum())}",
        note=f"{len(result.windows)} window(s)",
    )

    render_signal_chart(result.plan)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("<div class='gs-kicker'>Cumulative dollars per battery</div>", True)
        render_money_chart(result.ledger)
    with right:
        st.markdown("<div class='gs-kicker'>Scarcity windows detected</div>", True)
        if result.windows.empty:
            caption("No interval cleared the spike threshold on this day.")
        else:
            windows = result.windows.assign(
                window=lambda w: (
                    w["start"].dt.strftime("%H:%M") + " – " + w["end"].dt.strftime("%H:%M")
                )
            )
            st.dataframe(
                windows[["window", "intervals", "peak_mwh", "peak_z"]],
                hide_index=True,
                use_container_width=True,
            )

    with st.expander("Interval-by-interval signal log"):
        plan = result.plan
        columns = ["interval", "spp", "dam_mwh", "z", "spike_prob", "planned", "signal", "reason"]
        st.dataframe(
            plan.assign(interval=plan["interval_start"].dt.strftime("%H:%M"))[
                [c for c in columns if c in plan.columns or c == "interval"]
            ],
            hide_index=True,
            use_container_width=True,
            height=320,
        )

    st.divider()
    render_holdout()

    st.divider()
    render_congestion()

    caption(
        f"Backtest on real cached ERCOT {trace.market} prices for {trace.location}, "
        f"{trace.date} ({len(trace.frame)} intervals, peak ${trace.peak_mwh:,.2f}/MWh). "
        "Battery model (assumed, not measured fleet data): 13.5 kWh usable, 5 kW "
        "inverter, 90% round trip. Signals are "
        "advisory only — nothing is dispatched, and past prices are not a forecast of "
        "future revenue."
    )


@st.cache_data(show_spinner=False)
def chaos_run(path: str, approver: str | None = None) -> RunResult:
    """Replay one YAML chaos scenario; deterministic, so caching is safe.

    Without an ``approver`` the replay stops at the approval gate, so the mesh awards
    only ever execute after an operator clicks Approve in this view.
    """
    return run_file(path, approver=approver, approve=approver is not None)


def render_mesh_approval(result: RunResult, approved_key: str) -> None:
    """The human gate for the mesh: a real click, on the same path the engine uses."""
    if result.metrics.pending_approval:
        st.warning(
            f"Human approval required. {result.awards[-1].covered_kw:,.1f} kW is proposed "
            "and nothing is committed until you approve it."
        )
        if st.button("Approve award set", type="primary", use_container_width=True):
            st.session_state[approved_key] = True
            st.rerun()
        return
    approved = [a for a in result.awards if a.approved_by]
    if approved:
        st.success(f"Awards executed. Approved by {approved[-1].approved_by}.")
        if st.button("Reset to the approval gate", use_container_width=True):
            st.session_state.pop(approved_key, None)
            st.rerun()


def render_award_revision(result: RunResult) -> None:
    """Before/after the distrusted bids were dropped and the auction re-run."""
    revised = [a for a in result.awards if a.covered_kw_before_revision is not None]
    if not revised:
        return
    award = revised[-1]
    before = award.covered_kw_before_revision or 0.0
    st.markdown(
        "<div class='gs-card'><div class='gs-kicker'>Award revised after the trust "
        f"check</div><div class='gs-body'>Signatures verified, but Jev distrusted "
        f"{', '.join(award.dropped)}. Those bids were removed and the auction re-run "
        f"before anyone was asked to approve it: <b>{before:,.1f} kW from "
        f"{len(award.awards) + len(award.dropped):,} agents</b> before, "
        f"<b>{award.covered_kw:,.1f} kW from {len(award.awards):,} agents</b> after."
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_deliverability(result: RunResult) -> None:
    """What the pre-award proof trimmed, refused, and why."""
    checks = [(a, v) for a in result.awards for v in a.checks]
    metrics = result.metrics
    saved_kw = metrics.undeliverable_kw
    if not checks and saved_kw <= 0:
        return
    st.markdown(
        "<div class='gs-card'><div class='gs-kicker'>Deliverability check before the "
        f"award</div><div class='gs-body'>Every award is proved against the whole event "
        f"window before it is committed: <b>{metrics.awards_trimmed} trimmed</b>, "
        f"<b>{metrics.awards_rejected} refused</b>. Without the check this run would have "
        f"committed <b>{saved_kw:,.2f} kW</b> across "
        f"{metrics.undeliverable_awards} awards that the battery could not have held for "
        "the full window. Simulated fleet.</div></div>",
        unsafe_allow_html=True,
    )
    if checks:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "agent": v.agent_id,
                        "asked (kW)": v.requested_kw,
                        "can deliver (kW)": v.deliverable_kw,
                        "outcome": "refused" if v.rejected else "trimmed",
                        "why": v.reason,
                    }
                    for _, v in checks
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )


def render_registry(result: RunResult) -> None:
    """Who is in the mesh, what they claim they can do, and whether we believe them."""
    statuses = result.registry.statuses()
    rows = [
        {
            "agent": agent_id,
            "kind": card.kind.value,
            "zone": card.zone,
            "kW available": round(card.capabilities.get("kw_available", 0.0), 2),
            "kWh available": round(card.capabilities.get("kwh_available", 0.0), 2),
            "state of charge": round(card.capabilities.get("soc", 0.0), 2),
            "health": card.health.value,
            "last heartbeat (s)": card.last_heartbeat_s,
            "card": statuses[agent_id].value,
        }
        for agent_id, card in ((a, result.registry.card(a)) for a in result.registry.agent_ids())
    ]
    frame = pd.DataFrame(rows)
    rank = {CardStatus.REJECTED.value: 0, CardStatus.STALE.value: 1, CardStatus.VERIFIED.value: 2}
    frame = frame.sort_values(
        by=["card", "agent"], key=lambda s: s.map(rank) if s.name == "card" else s
    )
    st.dataframe(frame, hide_index=True, use_container_width=True, height=360)
    caption(
        "Every card is signed with an HMAC key the registry generates at startup. "
        "A card whose signature does not verify is rejected; an agent that stops "
        "sending heartbeats goes stale. Neither can win an award."
    )


def render_mesh_log(result: RunResult) -> None:
    """The negotiation as it happened: calls, bids, the award and the human approval."""
    frame = pd.DataFrame(
        [
            {
                "t (s)": m.t_s,
                "kind": m.kind.value,
                "from": m.sender,
                "to": m.recipient,
                "message": m.summary,
            }
            for m in result.bus.messages
        ]
    )
    kinds = sorted(frame["kind"].unique().tolist())
    chosen = st.multiselect("Message types", kinds, default=kinds, key="mesh_kinds")
    st.dataframe(
        frame[frame["kind"].isin(chosen)] if chosen else frame,
        hide_index=True,
        use_container_width=True,
        height=420,
    )


def render_agent_mesh() -> None:
    """Agent Mesh: signed capability cards and contract-net bidding, human-gated."""
    files = available_chaos_scenarios()
    if not files:
        st.warning("No scenarios found in scenarios/.")
        return
    with st.sidebar:
        st.header("Chaos scenario")
        choice = st.selectbox(
            "Replay", files, format_func=lambda p: p.stem.replace("_", " "), key="mesh_scenario"
        )
        caption(
            "Same run as `python -m gridsignal.simulate "
            f"scenarios/{choice.name}`, replayed from its JSONL trace."
        )
    approved_key = f"mesh_approved::{choice.name}"
    approver = OPERATOR if st.session_state.get(approved_key) else None
    result = chaos_run(str(choice), approver)
    metrics, scenario = result.metrics, result.scenario

    st.subheader(scenario.name)
    caption(scenario.description.strip())
    a, b, c, d, e = st.columns(5)
    metric(a, "Agents in the mesh", f"{metrics.agents:,}")
    metric(b, "Capacity covered", f"{metrics.covered_pct:.0f}%", power(metrics.covered_kw, 1))
    metric(c, "Time to cover", seconds(metrics.time_to_cover_s))
    metric(d, "Messages", f"{metrics.messages:,}")
    metric(
        e,
        "Dollars recovered",
        money(metrics.dollars_recovered),
        f"of ${metrics.dollars_at_risk:,.2f} at risk",
    )

    badges = " ".join(
        [
            pill(f"{metrics.rejected_cards} rejected cards", CARD_COLOR[CardStatus.REJECTED]),
            pill(f"{metrics.stale_agents} stale agents", CARD_COLOR[CardStatus.STALE]),
            pill(f"{metrics.rounds} negotiation rounds", "#38bdf8"),
            pill(
                "escalated to a human" if metrics.escalated else "fully covered",
                "#dc2626" if metrics.escalated else CARD_COLOR[CardStatus.VERIFIED],
            ),
        ]
    )
    st.markdown(f"<div class='gs-card'>{badges}</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='gs-sim'>Simulation only. Awards are proposals: nothing is "
        "committed until a named human approves, and no real battery is ever "
        "contacted.</div>",
        unsafe_allow_html=True,
    )

    render_mesh_approval(result, approved_key)

    if result.responses:
        st.subheader("Jev decision layer")
        render_jev_card(result.responses[-1], result.decisions[-1])
        render_award_revision(result)
    render_deliverability(result)

    left, right = st.columns([3, 4], gap="large")
    with left:
        st.subheader("Agent registry")
        render_registry(result)
    with right:
        st.subheader("Message log")
        render_mesh_log(result)

    render_rollout()

    left_table, right_table = st.columns(2, gap="large")
    with left_table:
        render_jev_eval()
    with right_table:
        render_holdout_drills()


def render_rollout() -> None:
    """Firmware rollout as an orchestrated job: rings, gates, halt and rollback."""
    st.subheader("Firmware rollout")
    files = rollout.available_rollouts()
    if not files:
        st.info("No rollout scenarios found in scenarios/.")
        return
    choice = st.radio(
        "Build",
        files,
        format_func=lambda p: p.stem.replace("rollout_", "").replace("_", " "),
        horizontal=True,
        key="rollout_scenario",
    )
    result = rollout_run(str(choice))
    m = result.metrics

    a, b, c, d = st.columns(4)
    metric(a, "Rings cleared", ratio(m.rings_completed, len(rollout.RING_SHARES)))
    metric(b, "Homes touched", f"{m.homes_touched:,}", f"of {m.devices:,} devices")
    metric(
        c,
        "Time to detect",
        "no fault" if m.time_to_detect_s is None else seconds(m.time_to_detect_s),
        f"{m.homes_affected:,} homes affected" if m.homes_affected else "build held",
    )
    metric(d, "Human approvals", m.human_approvals, f"{m.rolled_back:,} rolled back")

    badges = " ".join(
        [
            pill(
                f"halted at {m.halted_ring} on {m.failed_gate}" if m.halted else "all rings held",
                "#dc2626" if m.halted else "#16a34a",
            ),
            pill(f"{m.reserve_violations} backup reserve violations", "#16a34a"),
            pill(f"{m.held_s}s held for grid events / islanded homes", "#38bdf8"),
        ]
    )
    st.markdown(f"<div class='gs-card'>{badges}</div>", unsafe_allow_html=True)
    st.dataframe(
        pd.DataFrame(rollout.ring_table(result)), hide_index=True, use_container_width=True
    )

    gate_rows = [
        {
            "ring": ring.ring,
            "gate": gate.gate,
            "result": "pass" if gate.passed else "FAIL",
            "observed": gate.observed,
            "threshold": gate.threshold,
            "detail": gate.detail,
        }
        for ring in result.rings
        for gate in ring.gates
    ]
    with st.expander("Health gates, ring by ring"):
        st.dataframe(pd.DataFrame(gate_rows), hide_index=True, use_container_width=True)
    caption(
        "Simulated rollout of a simulated build. Rings are lab, 1% canary, 10%, 50% "
        "and 100%; each one has to clear telemetry heartbeat, charge/discharge "
        "response and backup reserve held before the next opens, no ring advances "
        "during a grid event or while a home is islanded, and every promotion past "
        "10% of the fleet needs a named human — scripted by the scenario here, not "
        "clicked; the recovery approval in the Control Room is the live one. Reproduce with "
        f"`python -m gridsignal.rollout scenarios/{choice.name}`; the run writes a "
        "replayable JSONL trace."
    )

    render_install_wave()


def render_install_wave() -> None:
    """Several hundred new units joining the mesh mid-event, on probation."""
    st.subheader("Install wave")
    result = install_run("scenarios/install_wave.yaml")
    m = result.metrics
    a, b, c, d = st.columns(4)
    metric(a, "Units joined", f"{m.registered:,}", f"{m.rejected_cards:,} rejected at the door")
    metric(b, "Cleared probation", f"{m.promoted:,}", f"{m.failed_health:,} failed health check")
    metric(
        c,
        "Time to first eligible award",
        "none"
        if m.time_to_first_eligible_award_s is None
        else seconds(m.time_to_first_eligible_award_s),
        f"{m.joins_per_hour:,.0f} joins/h simulated",
    )
    metric(
        d,
        "Awards to unverified units",
        m.awards_to_unverified + m.awards_to_probation,
        f"{m.existing_kw_lost:.0f} kW of existing commitment lost",
    )
    caption(
        "Simulated install wave: new batteries are commissioned by an installer phone "
        "check that registers their signed card, start on probation advertising no "
        "biddable kW, and only become eligible after heartbeat, charge/discharge and "
        "reserve checks. Reproduce with "
        "`python -m gridsignal.install scenarios/install_wave.yaml`."
    )


def render_jev_eval() -> None:
    """Does the model earn its place? Score it against the injected ground truth."""
    st.subheader("Rules-only vs Jev, every chaos scenario")
    report = jev_eval()
    summary = pd.DataFrame(
        [
            {
                "decision layer": s.mode,
                "root-cause accuracy": f"{s.correct}/{s.scenarios} ({s.accuracy:.0%})",
                "human approvals": s.human_approvals,
                "median decision latency (ms)": s.median_latency_ms,
            }
            for s in report.summaries
        ]
    )
    detail = pd.DataFrame(
        [
            {
                "scenario": r.scenario,
                "decision layer": r.mode,
                "root cause": r.root_cause,
                "injected truth": r.truth,
                "correct": "yes" if r.correct else "no",
                "human approvals": r.human_approvals,
                "answers from": r.source,
            }
            for r in report.rows
        ]
    )
    st.dataframe(summary, hide_index=True, use_container_width=True)
    with st.expander("Per-scenario detail"):
        st.dataframe(detail, hide_index=True, use_container_width=True)
    caption(
        "Ground truth is the injection that caused the capacity loss, declared in each "
        "scenario YAML. The rules were written against these same injections, so treat "
        "their accuracy as a ceiling, not as evidence they generalise. Replayed from "
        "recorded Jev answers, so these numbers need no API key."
    )


def render_holdout_drills() -> None:
    """The same two decision layers on drills written after the rules were frozen."""
    st.subheader("Held-out drills, after tuning on held-out")
    report = drill_report()
    rules_correct, total = report.accuracy(drills.RULES)
    jev_correct, _ = report.accuracy(drills.JEV)
    summary = pd.DataFrame(
        [
            {"decision layer": drills.RULES, "root-cause accuracy": f"{rules_correct}/{total}"},
            {"decision layer": drills.JEV, "root-cause accuracy": f"{jev_correct}/{total}"},
        ]
    )
    detail = pd.DataFrame(
        [
            {
                "drill": r.drill,
                "injected truth": r.truth,
                "rules-only": _mark(report, r.drill, drills.RULES),
                "Jev": _mark(report, r.drill, drills.JEV),
                "kW recovered": f"{r.covered_kw:,.0f} of {r.lost_kw:,.0f} ({r.covered_pct:.0f}%)",
                "time to recover": f"{r.time_to_recover_s}s",
                "backup reserve violations": r.backup_violations,
                "self-deployed locally (kW)": f"{r.self_deployed_kw:,.0f}",
                "response (cycles)": (
                    "n/a" if r.response_cycles is None else f"{r.response_cycles:,}"
                ),
            }
            for r in report.of_mode(drills.JEV)
        ]
    )
    st.dataframe(summary, hide_index=True, use_container_width=True)
    st.dataframe(detail, hide_index=True, use_container_width=True)
    caption(
        "Four drills in `scenarios/holdout/` written after the detection rules and the "
        "Jev questions were frozen, and scored once before anything changed: a Spain-style "
        "cascade, an under-frequency event with the coordinator unreachable, a "
        "neighbourhood islanding on its own batteries, and a large-load ramp with "
        "conflicting bids. All frequency, outage and load values are simulated. These "
        "numbers are **after tuning on held-out**: the frozen baseline scored "
        "rules-only 0/4 and Jev 1/4, and the frequency drill answered in ~12,900 "
        "cycles because it waited for the coordinator. Batteries now act on the rule "
        "signed into their own card, so the cycle column is measured against the "
        "15-cycle ERCOT Fast Frequency Response concept. Both baseline and tuned "
        "tables are in the README."
    )


def _mark(report: drills.DrillReport, drill: str, mode: str) -> str:
    row = next(r for r in report.rows if r.drill == drill and r.mode == mode)
    return f"{row.root_cause} {'✓' if row.correct else '✗'}"


def main() -> None:
    prewarm()
    render_header()
    render_scenario_controls()
    if st.session_state.get("view", VIEWS[0]) == "Why":
        render_why()
        return
    if st.session_state.get("view", VIEWS[0]) == "Agent Mesh":
        render_agent_mesh()
        return
    if st.session_state.get("view", VIEWS[0]) == "Grid Signals":
        render_grid_signals(
            st.session_state.get("scenario", DEFAULT_SCENARIO),
            st.session_state.get("fleet_size", FLEET_SIZE),
        )
        return
    eng = engine()
    if st.session_state.get("view") == "Member App":
        render_member(eng)
        render_demo_controls(eng)
        return
    render_demo_controls(eng)
    render_overview(eng)
    st.divider()
    render_home_first(eng)
    st.divider()
    render_fleet_replay()
    st.divider()
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Fleet map")
        render_map(eng)
        st.subheader("ERCOT price trace")
        render_prices(eng)
    with right:
        render_surplus(eng)
        render_reserve_policy(eng)
        render_dispatch_priority(eng)
        render_incident(eng)
    st.divider()
    render_workflow(eng)
    st.divider()
    render_audit(eng)


main()
