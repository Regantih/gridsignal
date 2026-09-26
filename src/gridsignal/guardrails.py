"""Public ERCOT market rules, encoded as hard guardrails every offer passes through.

An aggregation that bids home batteries into ERCOT does not get to decide what a legal
offer looks like. The price caps, the price floor, the products an aggregated
distributed energy resource (ADER) may sell and the volumes it may register are all
published, and each rule below carries the document it is read off. :data:`RULES` is the
whole list; :func:`check` is the only way an offer in this repo reaches a market.

Three of the guardrails are this simulation's own, and are labelled ``modelled`` so they
are never mistaken for a protocol clause: the sustain-duration table (a simplification of
ERCOT's storage qualification rules), the non-negative ancillary offer price, and the
member's backup reserve, which is a promise this product makes to the homeowner rather
than anything ERCOT requires.

The guardrails are enforced, not asserted after the fact. :mod:`gridsignal.ancillary`
builds an hour's offers, hands them here, and emits only what comes back clean; a refused
offer is counted and reported rather than quietly dropped. Reproduce every number with::

    python -m gridsignal.guardrails
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

#: ERCOT's ADER pilot page: the governing document and the AS qualification procedure.
ADER_PILOT_URL = "https://www.ercot.com/mktrules/pilots/ader"
#: PUCT Substantive Rule 25.509, where the system-wide offer caps are set.
PUCT_25_509_URL = "https://ftp.puc.texas.gov/public/puct-info/agency/rulesnlaws/subrules/electric/25.509/25.509.pdf"
#: NPRR385, which records the -$251/MWh Energy Offer Curve floor it was aligned to.
NPRR385_URL = "https://www.ercot.com/mktrules/issues/NPRR385"
#: The current ERCOT Nodal Protocols, Section 4 (Day-Ahead Operations).
NODAL_PROTOCOLS_URL = "https://www.ercot.com/mktrules/nprotocols/current"

#: High System-Wide Offer Cap: $5,000/MWh for energy, $5,000/MW per hour for ancillary
#: services (PUCT Subst. R. 25.509(6)(B); ERCOT Nodal Protocols §4.4.11).
HCAP_USD = 5_000.0
#: Low System-Wide Offer Cap, which replaces the HCAP for the rest of a calendar year
#: once the peaker net margin passes its threshold (25.509(6)(A) and (D)).
LCAP_USD = 2_000.0
#: Energy Offer Curve floor, ERCOT Nodal Protocols §4.4.9.3.1, the value NPRR385 cites.
ENERGY_FLOOR_USD = -251.0
#: Offers are priced and settled in dollars per MWh, capacity in kW.
KW_PER_MW = 1_000.0

ENERGY = "energy"


@dataclass(frozen=True)
class Rule:
    """One published rule, its source, and whether the source is a market document."""

    key: str
    text: str
    source: str
    url: str
    #: False when this repo invented the constraint. Those are never cited as ERCOT's.
    published: bool = True

    @property
    def provenance(self) -> str:
        return self.source if self.published else f"modelled by this repo — {self.source}"


@dataclass(frozen=True)
class PilotLimits:
    """The ADER pilot's product list and volume caps, as the governing document sets them.

    ERCOT *ADER Pilot Project Governing Document* Phase 3.3 (3 Jun 2026) §3 and §2, with
    the *ADER Telemetry Validation, SCED and AS Qualification Procedure 3.0* for what an
    aggregation has to prove before it may offer a product at all.
    """

    name: str
    products: tuple[str, ...]
    system_mw: dict[str, float]
    qse_share: float
    min_aggregation_kw: float
    max_premise_kw: float
    #: Total ADER capacity the pilot allows to be registered system-wide.
    registered_system_mw: float = 500.0

    def qse_cap_mw(self, product: str) -> float:
        cap = self.system_mw.get(product)
        return float("inf") if cap is None else cap * self.qse_share


#: What an aggregation of home batteries may sell today. Kept in step with
#: :data:`gridsignal.ancillary.ADER_PILOT`, which is the same document read for the
#: bidder; a test fails if the two drift.
PILOT = PilotLimits(
    name="ERCOT ADER pilot, Phase 3.3",
    products=("ecrs", "nonspin"),
    system_mw={"ecrs": 100.0, "nonspin": 100.0},
    qse_share=0.90,
    min_aggregation_kw=100.0,
    max_premise_kw=1_000.0,
    registered_system_mw=500.0,
)

RULES: tuple[Rule, ...] = (
    Rule(
        "offer_cap",
        f"No offer above the System-Wide Offer Cap: ${HCAP_USD:,.0f}/MWh for energy and "
        f"${HCAP_USD:,.0f}/MW per hour for ancillary services (${LCAP_USD:,.0f} once the "
        f"low cap is in force).",
        "PUCT Subst. R. 25.509(6)(B), ERCOT Nodal Protocols §4.4.11",
        PUCT_25_509_URL,
    ),
    Rule(
        "energy_floor",
        f"No energy offer below -${abs(ENERGY_FLOOR_USD):,.0f}/MWh, the Energy Offer Curve floor.",
        "ERCOT Nodal Protocols §4.4.9.3.1, the floor NPRR385 aligns the price floor to",
        NPRR385_URL,
    ),
    Rule(
        "capacity_floor",
        "No ancillary offer priced below zero: an aggregation is paid to stand by, it does "
        "not pay to.",
        "not a protocol clause; this repo refuses to offer capacity at a negative price",
        PUCT_25_509_URL,
        published=False,
    ),
    Rule(
        "pilot_product",
        "An ADER may offer ECRS and Non-Spin and nothing else. Reg Up, Reg Down and RRS "
        "are not pilot products for an aggregation of home batteries.",
        "ADER Pilot Governing Document Phase 3.3 §3, AS Qualification Procedure 3.0",
        ADER_PILOT_URL,
    ),
    Rule(
        "pilot_volume",
        "100 MW of ECRS and 100 MW of Non-Spin system-wide across the pilot, and no QSE "
        "registering more than 90% of either.",
        "ADER Pilot Governing Document Phase 3.3 §3",
        ADER_PILOT_URL,
    ),
    Rule(
        "pilot_premise",
        "Each aggregation offers at least 100 kW; no single premise above 1 MW.",
        "ADER Pilot Governing Document Phase 3.3 §2",
        ADER_PILOT_URL,
    ),
    Rule(
        "duration",
        "A product is only offered when the battery can sustain the award for the "
        "product's full duration: ECRS 2 h, Non-Spin 4 h, above the backup reserve.",
        "the duration table simplifies ERCOT's ESR AS qualification; the requirement to "
        "prove sustained capability is in the ADER AS Qualification Procedure 3.0",
        ADER_PILOT_URL,
        published=False,
    ),
    Rule(
        "pilot_registration",
        "An aggregation registers at least 100 kW, and no more than 500 MW of ADER "
        "capacity is registered across the whole pilot.",
        "ADER Pilot Governing Document Phase 3.3 §2 and §3",
        ADER_PILOT_URL,
    ),
    Rule(
        "double_sold",
        "The same kW is never sold twice: energy plus every ancillary product on one "
        "battery in one hour stays inside the inverter's nameplate.",
        "ERCOT Nodal Protocols §4.4.7.2.2, Ancillary Service Offer Validation, which "
        "validates a Resource's offers against what it can actually deliver",
        NODAL_PROTOCOLS_URL,
    ),
    Rule(
        "reserve",
        "No energy or capacity is offered out of the member's backup reserve.",
        "not a market rule; the backup promise this product makes to the homeowner",
        ADER_PILOT_URL,
        published=False,
    ),
)

_BY_KEY = {rule.key: rule for rule in RULES}


def rule(key: str) -> Rule:
    return _BY_KEY[key]


@dataclass(frozen=True)
class Offer:
    """One battery's offer of one product in one hour, before it leaves the aggregation.

    ``sustain_h`` is how long the award must be held for; ``soc_kwh`` and ``room_kwh``
    are the worst moment of that hour, because an award has to survive all of it.
    """

    device_id: str
    hour: int
    #: ``"energy"`` or an ancillary product key.
    product: str
    kw: float
    price_mw_h: float
    sustain_h: float
    power_kw: float
    soc_kwh: float
    reserve_kwh: float
    room_kwh: float = 0.0
    #: ``charge`` products are paid to absorb, so they need room rather than energy.
    direction: str = "discharge"

    @property
    def sellable_kwh(self) -> float:
        return max(self.soc_kwh - self.reserve_kwh, 0.0)

    @property
    def needed_kwh(self) -> float:
        return self.kw * self.sustain_h


@dataclass(frozen=True)
class Violation:
    """A rule an offer broke, in the words of the rule."""

    rule: str
    device_id: str
    hour: int
    product: str
    detail: str

    def __str__(self) -> str:
        return f"{self.device_id} hour {self.hour} {self.product}: {self.detail} [{self.rule}]"


class GuardrailBreach(Exception):
    """Raised by :func:`enforce` when an offer would have left breaking a rule."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = violations


def cap_usd(low_cap_in_force: bool = False) -> float:
    """The System-Wide Offer Cap in force: the high cap unless the low cap has replaced it."""
    return LCAP_USD if low_cap_in_force else HCAP_USD


def clamp_price(price_mw_h: float, product: str, low_cap_in_force: bool = False) -> float:
    """A bidder's own clamp: bring a price inside the cap and the floor before offering it.

    Validation never clamps. :func:`check` reads the price it is handed and refuses it, so
    a caller that prices badly is caught rather than quietly corrected; the co-optimizer
    offers the cleared price as it stands. This is here for a bidder building prices of
    its own, and is what the property sweep's generator uses.
    """
    floor = ENERGY_FLOOR_USD if product == ENERGY else 0.0
    return min(max(price_mw_h, floor), cap_usd(low_cap_in_force))


def _check_price(offer: Offer, low_cap_in_force: bool) -> list[Violation]:
    found: list[Violation] = []
    cap = cap_usd(low_cap_in_force)
    if offer.price_mw_h > cap:
        found.append(
            _violation(
                "offer_cap",
                offer,
                f"offered at ${offer.price_mw_h:,.2f} above the ${cap:,.0f} system-wide cap",
            )
        )
    floor = ENERGY_FLOOR_USD if offer.product == ENERGY else 0.0
    if offer.price_mw_h < floor:
        key = "energy_floor" if offer.product == ENERGY else "capacity_floor"
        found.append(
            _violation(
                key, offer, f"offered at ${offer.price_mw_h:,.2f} below the ${floor:,.0f} floor"
            )
        )
    return found


def _check_deliverable(offer: Offer, limits: PilotLimits) -> list[Violation]:
    found: list[Violation] = []
    if offer.kw > offer.power_kw + 1e-6:
        found.append(
            _violation(
                "double_sold",
                offer,
                f"{offer.kw:,.2f} kW above the {offer.power_kw:,.2f} kW inverter",
            )
        )
    if offer.kw > limits.max_premise_kw + 1e-6:
        found.append(
            _violation(
                "pilot_premise",
                offer,
                f"{offer.kw:,.2f} kW from one premise, above the "
                f"{limits.max_premise_kw:,.0f} kW the pilot allows",
            )
        )
    if offer.product != ENERGY and offer.product not in limits.products:
        found.append(
            _violation(
                "pilot_product",
                offer,
                f"{offer.product} is not a product an ADER may offer under {limits.name}",
            )
        )
    available = offer.room_kwh if offer.direction == "charge" else offer.sellable_kwh
    if offer.needed_kwh > available + 1e-6:
        word = "room" if offer.direction == "charge" else "energy above the backup reserve"
        found.append(
            _violation(
                "duration",
                offer,
                f"{offer.kw:,.2f} kW for {offer.sustain_h:g} h needs "
                f"{offer.needed_kwh:,.2f} kWh of {word}, {available:,.2f} kWh is there",
            )
        )
    if offer.direction == "discharge" and offer.soc_kwh - offer.needed_kwh < -1e-6:
        found.append(
            _violation("reserve", offer, "the offer would empty the pack past its reserve")
        )
    return found


def _check_together(
    offers: tuple[Offer, ...], limits: PilotLimits, devices: int
) -> list[Violation]:
    """Rules that only a whole book can break: selling a kW twice, and the MW caps.

    Each offer's energy is checked on its own against the moment of the hour that has to
    carry it — an export against the storage it starts the hour with, a reserve award
    against the least the battery holds at any point in it — so the same kWh cannot back
    both an export and an award. What is left for the book to check is the inverter: kW,
    unlike kWh, is shared by everything the battery is doing at once.
    """
    found: list[Violation] = []
    by_battery_hour: dict[tuple[str, int], list[Offer]] = {}
    for offer in offers:
        by_battery_hour.setdefault((offer.device_id, offer.hour), []).append(offer)

    for (device_id, hour), group in sorted(by_battery_hour.items()):
        committed = sum(o.kw for o in group)
        power_kw = max(o.power_kw for o in group)
        if committed > power_kw + 1e-6:
            found.append(
                Violation(
                    "double_sold",
                    device_id,
                    hour,
                    ", ".join(sorted(o.product for o in group)),
                    f"{committed:,.2f} kW offered across "
                    f"{len(group)} products from a {power_kw:,.2f} kW inverter",
                )
            )
    for product in sorted({o.product for o in offers} - {ENERGY}):
        cap_mw = limits.qse_cap_mw(product)
        if cap_mw == float("inf"):
            continue
        by_hour: dict[int, float] = {}
        for offer in offers:
            if offer.product == product:
                by_hour[offer.hour] = by_hour.get(offer.hour, 0.0) + offer.kw
        # One scored battery stands in for the whole fleet, so the offer is what this
        # battery would contribute times the fleet the cap is shared across.
        scale = devices / max(len({o.device_id for o in offers}), 1)
        for hour, kw in sorted(by_hour.items()):
            offered_mw = kw * scale / KW_PER_MW
            if offered_mw > cap_mw + 1e-6:
                found.append(
                    Violation(
                        "pilot_volume",
                        "fleet",
                        hour,
                        product,
                        f"{offered_mw:,.1f} MW offered against the {cap_mw:,.1f} MW this "
                        f"QSE may register",
                    )
                )
    return found


def _violation(key: str, offer: Offer, detail: str) -> Violation:
    return Violation(key, offer.device_id, offer.hour, offer.product, detail)


def check(
    offers: tuple[Offer, ...] | list[Offer],
    limits: PilotLimits = PILOT,
    devices: int = 10_000,
    low_cap_in_force: bool = False,
) -> tuple[Violation, ...]:
    """Every rule each offer breaks, empty when the book is legal to send."""
    book = tuple(offers)
    found: list[Violation] = []
    for offer in book:
        found.extend(_check_price(offer, low_cap_in_force))
        found.extend(_check_deliverable(offer, limits))
    found.extend(_check_together(book, limits, devices))
    return tuple(found)


def clean(
    offers: tuple[Offer, ...] | list[Offer],
    limits: PilotLimits = PILOT,
    devices: int = 10_000,
) -> tuple[tuple[Offer, ...], tuple[Violation, ...]]:
    """Split a book into what may be sent and what the guardrails refused.

    An offer is refused whole. Trimming it to fit would be a second bidder hidden in the
    validator, and the point of the validator is that it only ever says no.
    """
    book = tuple(offers)
    refused = check(book, limits, devices)
    blocked = {(v.device_id, v.hour, v.product) for v in refused}
    kept = tuple(o for o in book if (o.device_id, o.hour, o.product) not in blocked)
    for violation in refused:
        if violation.device_id == "fleet":
            kept = tuple(o for o in kept if not (o.hour == violation.hour))
    return kept, refused


def check_registration(
    fleet_kw: float,
    limits: PilotLimits = PILOT,
) -> tuple[Violation, ...]:
    """The rules an aggregation breaks by its size rather than by any one offer.

    Volume per product is checked per offer book; this is the other half of the pilot's
    arithmetic — too small to register at all, or more capacity than the pilot holds.
    """
    found: list[Violation] = []
    if fleet_kw < limits.min_aggregation_kw - 1e-6:
        found.append(
            Violation(
                "pilot_registration",
                "fleet",
                -1,
                "aggregation",
                f"{fleet_kw:,.1f} kW registered, below the "
                f"{limits.min_aggregation_kw:,.0f} kW minimum aggregation",
            )
        )
    if fleet_kw / KW_PER_MW > limits.registered_system_mw + 1e-6:
        found.append(
            Violation(
                "pilot_registration",
                "fleet",
                -1,
                "aggregation",
                f"{fleet_kw / KW_PER_MW:,.1f} MW registered, above the "
                f"{limits.registered_system_mw:,.0f} MW the pilot holds system-wide",
            )
        )
    return tuple(found)


def enforce(
    offers: tuple[Offer, ...] | list[Offer],
    limits: PilotLimits = PILOT,
    devices: int = 10_000,
) -> tuple[Offer, ...]:
    """Return the book, or raise. For callers that should never produce a bad offer."""
    violations = check(offers, limits, devices)
    if violations:
        raise GuardrailBreach(violations)
    return tuple(offers)


@dataclass(frozen=True)
class SweepResult:
    """A random sweep of the bidder's own offers past the guardrails."""

    books: int
    offers: int
    violations: tuple[Violation, ...] = field(repr=False, default=())
    #: Violations the same offers produce with the guardrails' clamping taken away.
    unguarded: tuple[Violation, ...] = field(repr=False, default=())

    @property
    def broken_rules(self) -> tuple[str, ...]:
        return tuple(sorted({v.rule for v in self.unguarded}))


def random_book(rng: random.Random, batteries: int = 6, hours: int = 8) -> tuple[Offer, ...]:
    """Offers a price-taking bidder would build under random, sometimes silly, conditions.

    The conditions are deliberately hostile: prices above the cap and below the floor,
    packs emptier than their reserve, products the pilot forbids. The bidder still has to
    emit a legal book, which is what the property test asserts.
    """
    products = (("ecrs", 2.0, "discharge"), ("nonspin", 4.0, "discharge"), ("regdn", 1.0, "charge"))
    offers: list[Offer] = []
    for index in range(batteries):
        power_kw = rng.choice((5.0, 20.0))
        kwh = power_kw * rng.choice((2.0, 2.7))
        reserve_kwh = round(kwh * 0.20, 3)
        for hour in rng.sample(range(24), hours):
            soc_kwh = round(rng.uniform(0.0, kwh), 3)
            product, sustain_h, direction = rng.choice(products)
            price = rng.choice((-400.0, -1.0, 12.5, 900.0, 7_500.0))
            room_kwh = max(kwh - soc_kwh, 0.0)
            available = room_kwh if direction == "charge" else max(soc_kwh - reserve_kwh, 0.0)
            offers.append(
                Offer(
                    device_id=f"BAT-{index:03d}",
                    hour=hour,
                    product=product,
                    kw=round(min(power_kw, available / sustain_h), 3),
                    price_mw_h=clamp_price(price, product),
                    sustain_h=sustain_h,
                    power_kw=power_kw,
                    soc_kwh=soc_kwh,
                    reserve_kwh=reserve_kwh,
                    room_kwh=room_kwh,
                    direction=direction,
                )
            )
    keep, _ = clean(offers, devices=len(offers) or 1)
    return keep


def _unguarded_book(rng: random.Random, batteries: int = 6, hours: int = 8) -> tuple[Offer, ...]:
    """The same bidder with the clamp and the refusal taken out, to prove the guards bite."""
    products = (("ecrs", 2.0, "discharge"), ("nonspin", 4.0, "discharge"), ("regdn", 1.0, "charge"))
    offers: list[Offer] = []
    for index in range(batteries):
        power_kw = rng.choice((5.0, 20.0))
        kwh = power_kw * rng.choice((2.0, 2.7))
        reserve_kwh = round(kwh * 0.20, 3)
        for hour in rng.sample(range(24), hours):
            soc_kwh = round(rng.uniform(0.0, kwh), 3)
            product, sustain_h, direction = rng.choice(products)
            price = rng.choice((-400.0, -1.0, 12.5, 900.0, 7_500.0))
            offers.append(
                Offer(
                    device_id=f"BAT-{index:03d}",
                    hour=hour,
                    product=product,
                    kw=power_kw,
                    price_mw_h=price,
                    sustain_h=sustain_h,
                    power_kw=power_kw,
                    soc_kwh=soc_kwh,
                    reserve_kwh=reserve_kwh,
                    room_kwh=max(kwh - soc_kwh, 0.0),
                    direction=direction,
                )
            )
    return tuple(offers)


def sweep(books: int = 200, seed: int = 20260906) -> SweepResult:
    """Run the bidder through random conditions and check every offer it produces."""
    rng = random.Random(seed)
    mirror = random.Random(seed)
    violations: list[Violation] = []
    unguarded: list[Violation] = []
    offers = 0
    for _ in range(books):
        book = random_book(rng)
        offers += len(book)
        violations.extend(check(book, devices=max(len(book), 1)))
        raw = _unguarded_book(mirror)
        unguarded.extend(check(raw, devices=max(len(raw), 1)))
    return SweepResult(books, offers, tuple(violations), tuple(unguarded))


def lines() -> list[str]:
    """The CLI report: the rules, their sources, and the proof that they bite."""
    from gridsignal import ancillary

    out: list[str] = ["ERCOT market guardrails. Every offer this repo builds passes these first."]
    out.append("")
    for item in RULES:
        out.append(f"  {item.key:<19}{item.text}")
        out.append(f"  {'':<19}source: {item.provenance}")
        out.append(f"  {'':<19}{item.url}")
    out.append("")

    fleet_kw = ancillary.BASE_CORE.power_kw * ancillary.FLEET_DEVICES
    registration = check_registration(fleet_kw)
    verdict = (
        "; ".join(v.detail for v in registration)
        if registration
        else (
            f"inside the {PILOT.min_aggregation_kw:,.0f} kW minimum aggregation and the "
            f"{PILOT.registered_system_mw:,.0f} MW the pilot registers system-wide"
        )
    )
    out.append(
        f"Registration, {ancillary.FLEET_DEVICES:,} simulated {ancillary.BASE_CORE.label}s "
        f"at {ancillary.BASE_CORE.power_kw:,.0f} kW: {fleet_kw / KW_PER_MW:,.0f} MW, {verdict}."
    )
    out.append("")

    checked = 0
    refused = 0
    for battery in (ancillary.LEGACY, ancillary.BASE_CORE):
        summary = ancillary.holdout_summary(battery)
        for day in summary.per_day:
            checked += day.offers_checked
            refused += day.offers_refused
    out.append(
        f"Bundled held-out days, both simulated battery types: {checked:,} offers built, "
        f"{refused:,} refused by a guardrail before leaving, "
        f"{checked - refused:,} sent. Reproduce with python -m gridsignal.ancillary."
    )
    out.append("")

    result = sweep()
    out.append(
        f"Property sweep: {result.books:,} randomly generated books "
        f"({result.offers:,} offers) built by the same bidder under hostile conditions — "
        f"prices above the cap and below the floor, packs emptier than their reserve, "
        f"products the pilot forbids."
    )
    out.append(f"  offers that broke a rule: {len(result.violations)}")
    out.append(
        f"  the same conditions with the guardrails removed: {len(result.unguarded):,} "
        f"violations across {len(result.broken_rules)} rules "
        f"({', '.join(result.broken_rules)}) — the guards are not true by construction."
    )
    return out


def main() -> int:
    print("\n".join(lines()))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
