"""The four decisions the mesh puts to Jev, and the simulated state it sends with them.

Only simulated fleet data leaves the process: agent-card summaries, telemetry ages,
cached ERCOT prices and the bids in the current auction. No member names, no addresses,
no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridsignal.jev.client import Question, QuestionKind

ROOT_CAUSE = "root_cause"
BACKUP_RISK = "backup_risk"
TRUST_PREFIX = "trust_"

ROOT_CAUSES: dict[str, str] = {
    "device_fault": "One battery failed or lost power on its own.",
    "gateway_outage": "A gateway or zone controller is down, so a whole group of otherwise "
    "healthy batteries is unreachable at once.",
    "telemetry_lag": "The devices are fine and still delivering; only their reporting is late.",
    "spoofed_agent": "An agent is misrepresenting itself: its capability card does not verify "
    "or its claims contradict its telemetry.",
    "grid_event": "Grid conditions, not a component failure, explain the change in output.",
}


@dataclass(frozen=True)
class Suspect:
    """An agent whose card or bid the coordinator wants a second opinion on."""

    agent_id: str
    card_status: str
    signature_valid: bool
    heartbeat_age_s: int
    claimed_kw: float
    claimed_kwh: float
    soc: float
    bid_kw: float
    fleet_median_kw: float

    def as_state(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "card_status": self.card_status,
            "hmac_signature_valid": self.signature_valid,
            "heartbeat_age_s": self.heartbeat_age_s,
            "claimed_kw_available": round(self.claimed_kw, 2),
            "claimed_kwh_available": round(self.claimed_kwh, 2),
            "state_of_charge": round(self.soc, 3),
            "bid_kw": round(self.bid_kw, 2),
            "fleet_median_kw_available": round(self.fleet_median_kw, 2),
        }


@dataclass(frozen=True)
class IncidentSnapshot:
    """Everything Jev is told about one simulated incident."""

    scenario: str
    agents: int
    batteries: int
    offline_agents: int
    offline_zones: tuple[str, ...]
    offline_gateway_rings: int
    largest_offline_group_in_one_ring: int
    gateway_ring_size: int
    rejected_cards: int
    stale_agents: int
    max_telemetry_age_s: int
    lost_kw: float
    price_usd_mwh: float
    event_hours: float
    dollars_at_risk: float
    bidders: int
    proposed_kw: float
    uncovered_kw: float
    plan_agents: int
    plan_mean_soc: float
    plan_min_spare_kwh: float
    backup_reserve_kwh: float
    suspects: tuple[Suspect, ...] = ()
    # Simulated grid-side conditions. Only sent when the drill actually has them, so a
    # plain component failure sends exactly the state it always did.
    frequency_hz: float = 60.0
    grid_side_kw: float = 0.0
    islanded_agents: int = 0
    self_deployed_kw: float = 0.0

    @property
    def has_grid_conditions(self) -> bool:
        return bool(
            self.frequency_hz != 60.0
            or self.grid_side_kw
            or self.islanded_agents
            or self.self_deployed_kw
        )

    def as_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "simulation": True,
            "note": "Simulated home-battery fleet. No real devices, utilities or market systems.",
            "scenario": self.scenario,
            "fleet": {
                "agents": self.agents,
                "batteries": self.batteries,
                "offline_agents": self.offline_agents,
                "offline_zones": list(self.offline_zones),
                "gateway_rings_touched": self.offline_gateway_rings,
                "largest_offline_group_in_one_gateway_ring": self.largest_offline_group_in_one_ring,
                "gateway_ring_size": self.gateway_ring_size,
                "rejected_cards": self.rejected_cards,
                "stale_agents": self.stale_agents,
                "max_telemetry_age_s": self.max_telemetry_age_s,
            },
            "grid_event": {
                "kw_lost": round(self.lost_kw, 2),
                "price_usd_per_mwh": round(self.price_usd_mwh, 2),
                "remaining_hours": round(self.event_hours, 3),
                "dollars_at_risk": round(self.dollars_at_risk, 2),
            },
            "recovery_plan": {
                "bidders": self.bidders,
                "proposed_kw": round(self.proposed_kw, 2),
                "uncovered_kw": round(self.uncovered_kw, 2),
                "agents_committed": self.plan_agents,
                "mean_state_of_charge_of_committed": round(self.plan_mean_soc, 3),
                "min_spare_kwh_after_plan": round(self.plan_min_spare_kwh, 2),
                "homeowner_backup_reserve_kwh": round(self.backup_reserve_kwh, 2),
            },
            "suspect_agents": [s.as_state() for s in self.suspects],
        }
        if self.has_grid_conditions:
            state["grid_conditions"] = {
                "simulated": True,
                "frequency_hz": round(self.frequency_hz, 3),
                "kw_lost_to_grid_side_events": round(self.grid_side_kw, 2),
                "kw_lost_to_component_failures": round(
                    max(self.lost_kw - self.grid_side_kw, 0.0), 2
                ),
                "homes_islanded_on_their_own_battery": self.islanded_agents,
                "kw_self_deployed_under_local_card_rule": round(self.self_deployed_kw, 2),
            }
        return state


def root_cause_question() -> Question:
    return Question(
        kind=QuestionKind.CHOICE,
        instructions=(
            "A simulated home-battery fleet lost committed capacity during a grid event. "
            "Pick the single most likely root cause of the capacity loss."
        ),
        options=ROOT_CAUSES,
    )


def trust_question(agent_id: str) -> Question:
    return Question(
        kind=QuestionKind.NOUL,
        instructions=(
            f"Agent {agent_id} passed or failed an HMAC check on its capability card and has "
            "bid into the recovery auction. Judging its behaviour rather than its signature, "
            "is this agent's card and bid trustworthy enough to award capacity to?"
        ),
        options={
            "yes": "Its claimed capability, state of charge, bid and heartbeat are mutually "
            "consistent and in line with the rest of the fleet.",
            "no": "Its claims are implausible, contradict its telemetry, or look like a "
            "validly signed agent behaving suspiciously.",
        },
    )


def backup_risk_question() -> Question:
    return Question(
        kind=QuestionKind.SCORE,
        instructions=(
            "Score the risk that executing this recovery plan leaves homeowners without "
            "enough stored energy for whole-home backup. 0 is no meaningful risk, 1 is severe."
        ),
        considerations=(
            "How much stored energy each committed battery keeps after the plan, against the "
            "homeowner backup reserve.",
            "How low the state of charge of the committed batteries already is.",
            "How many homes the plan draws on at once, and for how long.",
        ),
    )


def incident_questions(snapshot: IncidentSnapshot) -> dict[str, Question]:
    """Root cause, plan risk, and one trust question per suspect agent."""
    questions: dict[str, Question] = {
        ROOT_CAUSE: root_cause_question(),
        BACKUP_RISK: backup_risk_question(),
    }
    for suspect in snapshot.suspects:
        questions[f"{TRUST_PREFIX}{suspect.agent_id}"] = trust_question(suspect.agent_id)
    return questions
