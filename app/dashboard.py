"""GridSignal Control Room: simulation-only operator dashboard for a battery fleet.

Run with:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import (
    DeviceStatus,
    Incident,
    IncidentStatus,
    Severity,
    TaskStatus,
)
from gridsignal.fleet import FOCUS_DEVICE_ID

st.set_page_config(page_title="GridSignal Control Room", layout="wide", page_icon="⚡")

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

CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 1500px;}
.gs-card {background: #11161f; border: 1px solid #263041; border-radius: 12px;
          padding: 1rem 1.15rem; margin-bottom: 0.85rem;}
.gs-pill {display:inline-block; padding: 2px 10px; border-radius: 999px;
          font-size: 0.74rem; font-weight: 600; color: #fff;}
.gs-kicker {text-transform: uppercase; letter-spacing: .08em; font-size: .7rem;
            color: #8b98ad; margin-bottom: .2rem;}
.gs-title {font-size: 1.02rem; font-weight: 650; color: #e6edf6; margin-bottom: .35rem;}
.gs-body {font-size: .87rem; color: #b9c4d4; line-height: 1.45;}
.gs-sim {background: #1b2537; border-left: 4px solid #38bdf8; border-radius: 8px;
         padding: .65rem .9rem; font-size: .85rem; color: #cfe0f2;}
</style>
"""


def engine() -> ControlRoomEngine:
    if "engine" not in st.session_state:
        st.session_state.engine = ControlRoomEngine()
    return st.session_state.engine


def pill(text: str, color: str) -> str:
    return f"<span class='gs-pill' style='background:{color}'>{text}</span>"


def render_header() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    left, right = st.columns([3, 2])
    with left:
        st.title("⚡ GridSignal Control Room")
        st.caption("Distributed home-battery fleet orchestration — Texas (simulated)")
    with right:
        st.markdown(
            "<div class='gs-sim'><b>SIMULATION ONLY.</b> Deterministic mock data. "
            "No real devices, utilities or ERCOT systems are contacted. "
            "<b>A human operator approves every recovery action</b> — nothing is "
            "dispatched automatically.</div>",
            unsafe_allow_html=True,
        )


def render_overview(eng: ControlRoomEngine) -> None:
    snap = eng.snapshot()
    ev = snap.grid_event
    cols = st.columns(6)
    cols[0].metric("Fleet size", snap.total_devices)
    cols[1].metric(
        "Available capacity",
        f"{snap.available_capacity_kwh:,.0f} kWh",
        delta=f"{snap.committed_kw:,.0f} kW committed",
        delta_color="off",
    )
    cols[2].metric("Online", snap.online, delta=f"{snap.degraded} degraded", delta_color="off")
    cols[3].metric(
        "Offline / quarantined",
        f"{snap.offline} / {snap.unavailable}",
        delta="BAT-042" if snap.offline or snap.unavailable else "none",
        delta_color="off",
    )
    cols[4].metric(
        f"Grid event ({ev.zone})",
        ev.status.title(),
        delta=f"{snap.coverage_pct:.0f}% of {ev.target_kw:.0f} kW target",
        delta_color="off",
    )
    cols[5].metric(
        "Open incidents",
        snap.open_incidents,
        delta=f"{len(snap.incidents)} total",
        delta_color="off",
    )

    if snap.coverage_pct >= 99.5:
        st.success(f"Commitment covered: {snap.committed_kw:.0f} kW of {ev.target_kw:.0f} kW.")
    else:
        st.error(
            f"Commitment at risk: {snap.committed_kw:.0f} kW of {ev.target_kw:.0f} kW "
            f"({snap.coverage_pct:.0f}%). Recovery plan needs operator approval."
        )


def render_map(eng: ControlRoomEngine) -> None:
    snap = eng.snapshot()
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
            for d in snap.devices
        ]
    )
    color_map = {STATUS_LABEL[s]: c for s, c in STATUS_COLOR.items()}
    fig = px.scatter_map(
        frame,
        lat="lat",
        lon="lon",
        color="Status",
        size="size",
        size_max=20,
        color_discrete_map=color_map,
        hover_name="Device",
        hover_data={
            "Site": True,
            "Zone": True,
            "Assigned kW": True,
            "SoC %": True,
            "lat": False,
            "lon": False,
            "size": False,
        },
        zoom=4.5,
        center={"lat": 30.8, "lon": -97.5},
        height=430,
    )
    fig.update_layout(
        map_style="carto-darkmatter",
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        legend={"orientation": "h", "y": -0.05},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("<div class='gs-kicker'>Device grid</div>", unsafe_allow_html=True)
    per_row = 12
    for start in range(0, len(snap.devices), per_row):
        row = st.columns(per_row)
        for col, device in zip(row, snap.devices[start : start + per_row], strict=False):
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
    render_approval(eng, incident)


def render_approval(eng: ControlRoomEngine, incident: Incident) -> None:
    if incident.status is IncidentStatus.AWAITING_APPROVAL:
        st.warning(
            "Human approval required. The orchestrator has planned the recovery but will not "
            "reassign any capacity until an operator approves."
        )
        if st.button("✅ Approve Recovery Plan", type="primary", use_container_width=True):
            eng.approve_recovery()
            st.rerun()
    elif incident.status is IncidentStatus.RESOLVED:
        st.success(
            f"Recovery complete. Approved by {incident.approved_by} at "
            f"{incident.approved_at:%H:%M:%S}, resolved at {incident.resolved_at:%H:%M:%S}."
        )
        st.markdown(
            f"<div class='gs-card'><div class='gs-kicker'>Operator summary</div>"
            f"<div class='gs-body'>{eng.human_summary()}</div></div>",
            unsafe_allow_html=True,
        )


def render_tasks(eng: ControlRoomEngine) -> None:
    st.subheader("Collaboration")
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


def render_demo_controls(eng: ControlRoomEngine) -> None:
    with st.sidebar:
        st.header("Demo Controls")
        st.caption("Judges can replay the story without reloading the page.")
        pending_or_done = any(i.device_id == FOCUS_DEVICE_ID for i in eng.incidents)
        if st.button(
            f"⚠️ Trigger {FOCUS_DEVICE_ID} Failure",
            use_container_width=True,
            disabled=pending_or_done,
        ):
            eng.trigger_device_failure(FOCUS_DEVICE_ID)
            st.rerun()
        if st.button("🔄 Reset Demo", use_container_width=True):
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
        st.caption(
            "Safety boundary: this tool is a simulation. It performs no dispatch, "
            "no device commands and no utility integration. Recovery executes only "
            "after explicit human approval."
        )


def main() -> None:
    eng = engine()
    render_header()
    render_demo_controls(eng)
    render_overview(eng)
    st.divider()
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Fleet map")
        render_map(eng)
    with right:
        render_incident(eng)
        render_tasks(eng)
    st.divider()
    render_audit(eng)


main()
