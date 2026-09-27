"""Every market rule, proved to bite: one invalid offer per guardrail, then a sweep."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from gridsignal import ancillary, guardrails, holdout
from gridsignal.guardrails import ENERGY, Offer

BATTERY_KWH = 40.0
POWER_KW = 20.0
RESERVE_KWH = 8.0


LEGAL = Offer(
    device_id="BAT-001",
    hour=17,
    product="ecrs",
    kw=9.0,
    price_mw_h=85.0,
    sustain_h=2.0,
    power_kw=POWER_KW,
    soc_kwh=30.0,
    reserve_kwh=RESERVE_KWH,
    room_kwh=10.0,
    direction="discharge",
)


def _offer(**overrides: float | str) -> Offer:
    """A legal ECRS offer from a Base Core-style battery, before an override breaks it."""
    return replace(LEGAL, **overrides)


def test_the_baseline_offer_is_legal_so_every_other_case_isolates_one_rule() -> None:
    assert guardrails.check((_offer(),), devices=10_000) == ()


@pytest.mark.parametrize(
    ("rule", "overrides"),
    [
        ("offer_cap", {"price_mw_h": 5_000.01}),
        ("energy_floor", {"product": ENERGY, "sustain_h": 1.0, "price_mw_h": -251.01}),
        ("capacity_floor", {"price_mw_h": -0.01}),
        ("pilot_product", {"product": "regup", "sustain_h": 1.0}),
        ("pilot_premise", {"kw": 1_000.5, "power_kw": 2_000.0, "soc_kwh": 4_000.0}),
        ("duration", {"kw": 15.0}),
        ("reserve", {"kw": 15.0, "soc_kwh": 8.0, "reserve_kwh": 8.0}),
        ("double_sold", {"kw": 20.5, "soc_kwh": 400.0}),
    ],
)
def test_each_guardrail_refuses_the_offer_that_breaks_it(
    rule: str, overrides: dict[str, float | str]
) -> None:
    """One offer per rule, otherwise legal, must come back named by that rule."""
    broken = guardrails.check((_offer(**overrides),), devices=10_000)

    assert rule in {v.rule for v in broken}, f"{rule} let {overrides} through: {broken}"


def test_the_same_kw_cannot_be_sold_into_two_products_in_one_hour() -> None:
    """Each offer fits the inverter alone; together they are 30 kW from a 20 kW unit."""
    energy = _offer(product=ENERGY, sustain_h=1.0, kw=15.0, price_mw_h=120.0)
    capacity = _offer(product="nonspin", sustain_h=4.0, kw=15.0, soc_kwh=400.0)

    assert guardrails.check((energy,), devices=1) == ()
    broken = guardrails.check((energy, capacity), devices=1)

    assert {v.rule for v in broken} == {"double_sold"}
    assert "20.00 kW inverter" in str(broken[0])


def test_no_energy_offer_comes_out_of_the_members_backup_reserve() -> None:
    """The pack holds 10 kWh, 8 of them promised to the member: 2 kWh may be sold."""
    legal = _offer(product=ENERGY, sustain_h=1.0, kw=2.0, soc_kwh=10.0, price_mw_h=900.0)
    greedy = _offer(product=ENERGY, sustain_h=1.0, kw=2.5, soc_kwh=10.0, price_mw_h=900.0)

    assert guardrails.check((legal,), devices=1) == ()
    assert "duration" in {v.rule for v in guardrails.check((greedy,), devices=1)}


def test_the_fleet_cannot_register_more_mw_of_a_product_than_the_qse_cap() -> None:
    """9 kW per battery is exactly 90 MW across 10,000; a watt more is not offerable."""
    assert guardrails.check((_offer(kw=9.0),), devices=10_000) == ()

    broken = guardrails.check((_offer(kw=9.001),), devices=10_000)

    assert {v.rule for v in broken} == {"pilot_volume"}
    assert "90.0 MW this QSE may register" in str(broken[0])


def test_a_refused_offer_is_dropped_whole_rather_than_trimmed_to_fit() -> None:
    good = _offer(hour=18)
    bad = _offer(hour=19, price_mw_h=9_000.0)

    kept, refused = guardrails.clean((good, bad), devices=10_000)

    assert kept == (good,)
    assert [v.rule for v in refused] == ["offer_cap"]


def test_enforce_raises_rather_than_letting_a_bad_offer_leave() -> None:
    with pytest.raises(guardrails.GuardrailBreach) as raised:
        guardrails.enforce((_offer(price_mw_h=9_000.0),), devices=10_000)

    assert "offer_cap" in str(raised.value)
    assert guardrails.enforce((_offer(),), devices=10_000) == (_offer(),)


def test_clamping_a_price_puts_it_between_the_floor_and_the_cap() -> None:
    assert guardrails.clamp_price(9_000.0, "ecrs") == guardrails.HCAP_USD
    assert guardrails.clamp_price(9_000.0, "ecrs", low_cap_in_force=True) == guardrails.LCAP_USD
    assert guardrails.clamp_price(-400.0, ENERGY) == guardrails.ENERGY_FLOOR_USD
    assert guardrails.clamp_price(-400.0, "ecrs") == 0.0
    assert guardrails.clamp_price(85.0, "ecrs") == 85.0


@pytest.mark.parametrize("seed", [1, 7, 20260906])
def test_no_generated_book_contains_an_offer_that_breaks_a_rule(seed: int) -> None:
    """The property: whatever the conditions, the bidder's own output is always legal."""
    rng = random.Random(seed)
    for _ in range(50):
        book = guardrails.random_book(rng)
        assert guardrails.check(book, devices=max(len(book), 1)) == ()


def test_the_sweep_is_deterministic_and_the_guards_are_not_true_by_construction() -> None:
    first = guardrails.sweep(books=25)
    again = guardrails.sweep(books=25)

    assert first.offers == again.offers
    assert first.violations == again.violations == ()
    # The same conditions with the clamp and the refusal removed break five rules, so a
    # clean sweep is the guardrails working rather than the generator being polite.
    assert first.broken_rules == (
        "capacity_floor",
        "duration",
        "offer_cap",
        "pilot_product",
        "reserve",
    )


def test_every_rule_names_a_source_and_modelled_ones_say_so() -> None:
    for rule in guardrails.RULES:
        assert rule.url.startswith("https://"), rule.key
        assert rule.source, rule.key
        if not rule.published:
            assert rule.provenance.startswith("modelled by this repo"), rule.key

    published = {rule.key for rule in guardrails.RULES if rule.published}
    assert published == {
        "offer_cap",
        "energy_floor",
        "pilot_product",
        "pilot_volume",
        "pilot_premise",
        "pilot_registration",
        "double_sold",
    }


def test_the_low_cap_refuses_a_price_the_high_cap_allows() -> None:
    """$3,000 is legal under the HCAP and illegal once the low cap is in force."""
    offer = _offer(price_mw_h=3_000.0)

    assert guardrails.check((offer,), devices=10_000) == ()
    assert {v.rule for v in guardrails.check((offer,), devices=10_000, low_cap_in_force=True)} == {
        "offer_cap"
    }


def test_validation_refuses_a_bad_price_rather_than_quietly_correcting_it() -> None:
    """Clamping is a bidder's job; the validator only ever says no."""
    over_cap = _offer(price_mw_h=9_000.0)
    kept, refused = guardrails.clean((over_cap,), devices=10_000)

    assert kept == ()
    assert [v.rule for v in refused] == ["offer_cap"]
    assert guardrails.clamp_price(over_cap.price_mw_h, over_cap.product) == guardrails.HCAP_USD


def test_an_aggregation_too_small_to_register_and_one_too_large_are_both_refused() -> None:
    fleet_kw = ancillary.BASE_CORE.power_kw * ancillary.FLEET_DEVICES

    assert guardrails.check_registration(fleet_kw) == ()
    assert [v.rule for v in guardrails.check_registration(99.0)] == ["pilot_registration"]
    too_big = guardrails.check_registration(500_001.0)
    assert [v.rule for v in too_big] == ["pilot_registration"]
    assert "500 MW the pilot holds" in too_big[0].detail


def test_the_validator_and_the_bidder_agree_on_what_the_pilot_registers() -> None:
    assert guardrails.PILOT.registered_system_mw == ancillary.ADER_PILOT.registered_system_mw


def test_the_validators_pilot_limits_match_the_bidders_copy_of_the_document() -> None:
    """Two readings of one document. If they drift, one of them is wrong."""
    assert guardrails.PILOT.products == ancillary.ADER_PILOT.products
    assert guardrails.PILOT.system_mw == ancillary.ADER_PILOT.system_mw
    assert guardrails.PILOT.qse_share == ancillary.ADER_PILOT.qse_share
    assert guardrails.PILOT.max_premise_kw == ancillary.ADER_PILOT.max_premise_kw
    assert guardrails.PILOT.min_aggregation_kw == ancillary.ADER_PILOT.min_aggregation_kw


@pytest.mark.slow
def test_every_offer_the_bidder_builds_on_the_bundled_days_leaves_clean() -> None:
    """The real path: nothing reaches a market that the validator has not passed."""
    checked = 0
    for trace in holdout.load_holdout():
        for battery in (ancillary.LEGACY, ancillary.BASE_CORE):
            day = ancillary.co_optimize(trace, battery)
            checked += day.offers_checked
            assert day.refusals == ()
            assert day.reserve_violations == 0
    assert checked > 100


@pytest.mark.slow
def test_an_award_never_exceeds_the_per_battery_share_of_the_qse_cap() -> None:
    cap_kw = ancillary.ADER_PILOT.per_battery_kw("ecrs", ancillary.FLEET_DEVICES)
    for trace in holdout.load_holdout():
        day = ancillary.co_optimize(trace, ancillary.BASE_CORE)
        for award in day.awards:
            assert award.kw <= cap_kw + 1e-9
            assert award.product in ancillary.ADER_PILOT.products
            assert 0.0 <= award.price_mw_h <= guardrails.HCAP_USD
