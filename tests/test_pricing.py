import pandas as pd
from app.pricing import simulate, price_distribution
from app.transform import build_rows

ANALYSIS_DATE = "2026-06-15"

# The baseline policy is the case-study policy: standard accounts get
# max(current x (1+increase), floor), strategic legacy get current x (1+increase).
# The governance guardrails below are an opt-in overlay, off by default.
GUARDRAILS = {"max_increase_pct": 20.0, "hold_at_or_above_list": True, "clamp_at_list": True}


def mk_row(**overrides):
    row = {
        "account_key": "A", "account_name": "A", "product": "Hiring",
        "quantity": 2, "unit_price": 58, "current_arr": 1392,
        "tenure_years": 1, "next_eligible_date": "2026-07-01",
    }
    row.update(overrides)
    return row


def test_standard_account_below_floor_is_lifted_to_floor():
    # Baseline policy: a standard account below floor goes straight to floor,
    # however far that is, because no cap is configured by default.
    rows = [mk_row(unit_price=40, tenure_years=1)]
    row = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["proposed_unit_price"] == 60.0
    assert row["floor_binds"] is True
    assert row["below_floor_after"] is False
    assert row["segment"] == "Floor Normalize"


def test_standard_account_above_floor_gets_standard_increase_only():
    # 90 * 1.05 = 94.5, already clear of the 60 floor, so the floor never binds
    # and the line is a plain standard increase.
    rows = [mk_row(unit_price=90, tenure_years=1)]
    row = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["proposed_unit_price"] == round(90 * 1.05, 4)
    assert row["floor_binds"] is False
    assert row["segment"] == "Standard Increase"


def test_guardrail_cap_binds_before_floor():
    # With the optional cap enabled, a line far below floor (60) is lifted only
    # to current * 1.20, stopping short of the floor.
    rows = [mk_row(unit_price=40, tenure_years=1)]
    r = simulate(rows, config=GUARDRAILS, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 48.0
    assert row["price_change_pct"] == 20.0
    assert row["below_floor_after"] is True
    assert row["arr_left_below_floor"] == round((60 - 48.0) * 2 * 12, 2)


def test_guardrail_floor_binds_before_cap():
    # Close enough to floor (60) that current * 1.20 would overshoot it, so
    # the account is raised only to the floor, not the full cap.
    rows = [mk_row(unit_price=55, tenure_years=1)]
    r = simulate(rows, config=GUARDRAILS, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 60.0
    assert round(row["price_change_pct"], 3) == round((60 / 55 - 1) * 100, 3)
    assert row["below_floor_after"] is False
    assert row["arr_left_below_floor"] == 0


def test_strategic_account_gets_standard_increase_only_regardless_of_cap():
    # Strategic legacy accounts are exempt from floor logic and the cap by
    # definition -- not by a toggle -- so they may stay deeply below floor.
    rows = [mk_row(unit_price=40, tenure_years=5, quantity=10)]
    default = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    tiny_cap = simulate(rows, config={"max_increase_pct": 1}, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    huge_cap = simulate(rows, config={"max_increase_pct": 999}, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]

    for row in (default, tiny_cap, huge_cap):
        assert row["strategic_legacy"] is True
        assert row["proposed_unit_price"] == 42.0  # 40 * 1.05, standard increase only
        assert row["price_change_pct"] == 5.0
        # Still deeply below floor -- reported, but never acted on.
        assert row["below_floor_after"] is True
        assert row["arr_left_below_floor"] == round((60 - 42.0) * 10 * 12, 2)


def test_guardrail_holds_at_or_above_list_lines():
    # Hiring list is 78.75. With the guardrail on, a line at or above list
    # receives exactly zero -- both the strictly-above and the exactly-at case.
    for price in (90, 78.75):
        rows = [mk_row(unit_price=price, tenure_years=1)]
        row = simulate(rows, config=GUARDRAILS, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
        assert row["proposed_unit_price"] == price
        assert row["arr_change"] == 0
        assert row["segment"] == "Held (Above List)"


def test_guardrail_clamps_a_below_list_line_at_list():
    # 77 * 1.05 = 80.85 would cross the 78.75 list; the clamp stops it at list.
    rows = [mk_row(unit_price=77, tenure_years=1)]
    guarded = simulate(rows, config=GUARDRAILS, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert guarded["proposed_unit_price"] == 78.75

    # Baseline (case-study) policy does not clamp: the standard increase applies.
    baseline = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert baseline["proposed_unit_price"] == round(77 * 1.05, 4)


def test_zero_or_negative_price_is_held_for_review():
    rows = [mk_row(unit_price=0, tenure_years=1)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 0
    assert row["arr_change"] == 0
    assert row["segment"] == "Held (Zero Price)"


def test_breakeven_math():
    # HR list is 105, so 90 -> 99 at +10% stays clear of the list ceiling and
    # the 80 floor; the arithmetic below is the pure standard-increase path.
    rows = [
        mk_row(account_key="A", product="HR", unit_price=90, quantity=10, current_arr=2000, tenure_years=1),
        mk_row(account_key="B", product="HR", unit_price=90, quantity=5, current_arr=500, tenure_years=1),
    ]
    r = simulate(rows, config={"increase_pct": 10.0}, analysis_date=ANALYSIS_DATE, horizon_months=12)
    s = r["summary"]
    expansion = s["run_rate_arr_uplift"]
    assert expansion == round((99 - 90) * 10 * 12 + (99 - 90) * 5 * 12, 3)

    # Break-even churn is measured on the post-increase ARR of affected
    # accounts, the same conservative basis as the churn sensitivity itself.
    pre = 2000 + 500
    post = pre + expansion
    assert s["pre_increase_affected_arr"] == pre
    assert s["post_increase_affected_arr"] == round(post, 3)
    assert s["breakeven_churn_pct"] == round(expansion / post * 100, 4)
    # Account A alone (2000 ARR) already exceeds the combined expansion
    # (1620), so churning just the single biggest account breaks even.
    assert s["breakeven_accounts"] == 1


def test_churn_sensitivity_basis_is_configurable():
    rows = [
        mk_row(account_key="A", product="HR", unit_price=90, quantity=10, current_arr=2000, tenure_years=1),
        mk_row(account_key="B", product="HR", unit_price=90, quantity=5, current_arr=500, tenure_years=1),
    ]
    post = simulate(rows, config={"increase_pct": 10.0}, analysis_date=ANALYSIS_DATE, horizon_months=12)["summary"]
    pre = simulate(rows, config={"increase_pct": 10.0, "churn_basis": "pre_increase"},
                    analysis_date=ANALYSIS_DATE, horizon_months=12)["summary"]

    assert post["churn_basis"] == "post_increase"
    assert pre["churn_basis"] == "pre_increase"
    # The post-increase basis is the more conservative of the two: a larger
    # base means a larger modeled loss and a smaller risk-adjusted net.
    assert post["churn_sensitivity_loss"] == round(post["post_increase_affected_arr"] * 0.03, 3)
    assert pre["churn_sensitivity_loss"] == round(pre["pre_increase_affected_arr"] * 0.03, 3)
    assert post["churn_sensitivity_loss"] > pre["churn_sensitivity_loss"]
    assert post["risk_adjusted_net"] < pre["risk_adjusted_net"]


def test_arr_weighted_average_increase_differs_from_simple_average():
    rows = [
        # Below the 11 floor -> lifted to floor, a big % move on a small ARR weight.
        mk_row(account_key="A", product="Payroll", unit_price=9, quantity=1, current_arr=1000, tenure_years=1),
        # Standard 5% increase, far larger ARR weight.
        mk_row(account_key="B", product="Payroll", unit_price=12, quantity=1, current_arr=9000, tenure_years=1),
    ]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    s = r["summary"]
    a, b = r["rows"]
    assert a["proposed_unit_price"] == 11.0
    assert a["price_change_pct"] == round((11 / 9 - 1) * 100, 3)
    assert b["price_change_pct"] == 5.0
    simple_avg = (a["price_change_pct"] + b["price_change_pct"]) / 2
    weighted_avg = (a["price_change_pct"] * 1000 + b["price_change_pct"] * 9000) / 10000
    assert s["avg_unit_increase_pct"] == round(simple_avg, 2)
    assert s["arr_weighted_avg_increase_pct"] == round(weighted_avg, 2)
    assert s["arr_weighted_avg_increase_pct"] != s["avg_unit_increase_pct"]


def test_price_distribution_before_after_buckets():
    rows = [
        mk_row(account_key="A", product="Hiring", unit_price=45, quantity=1, tenure_years=1),
        mk_row(account_key="B", product="Hiring", unit_price=90, quantity=1, tenure_years=1),
        mk_row(account_key="C", product="Payroll", unit_price=12, quantity=1, tenure_years=1),
    ]
    result = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    dist = {d["product"]: d for d in price_distribution(result)}

    hiring = dist["Hiring"]
    assert hiring["floor"] == 60.0
    assert hiring["list_price"] == 78.75
    assert hiring["before"] == sorted([45, 90])
    # 45 -> lifted to the 60 floor; 90 -> standard 5% increase (94.5), since
    # the above-list hold is an opt-in guardrail rather than baseline policy.
    assert hiring["after"] == sorted([60.0, round(90 * 1.05, 4)])

    payroll = dist["Payroll"]
    assert payroll["floor"] == 11.0
    assert payroll["list_price"] == 14.70
    assert payroll["before"] == [12]
    assert payroll["after"] == [round(12 * 1.05, 4)]


def test_pct_within_10pct_of_list_and_pct_below_floor_are_arr_weighted():
    rows = [
        # Below list-band before (68 < 70.875), lands within it after (71.4).
        mk_row(account_key="A", product="Hiring", unit_price=68, quantity=1, current_arr=1000, tenure_years=1),
        # Below floor before (50 < 60); capped exactly to floor (60) after,
        # which is not below floor and not within 10% of list (78.75) either.
        mk_row(account_key="B", product="Hiring", unit_price=50, quantity=1, current_arr=3000, tenure_years=1),
    ]
    s = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["summary"]
    assert s["pct_within_10pct_of_list_before"] == 0.0
    assert s["pct_within_10pct_of_list_after"] == 25.0  # only account A's 1000 of 4000 total ARR
    assert s["pct_below_floor_before"] == 75.0  # only account B's 3000 of 4000 total ARR
    assert s["pct_below_floor_after"] == 0.0


def test_discount_by_ae_weighted_vs_simple_average():
    d1 = (1 - 76 / 78.75) * 100  # small discount, small account
    d2 = (1 - 60 / 78.75) * 100  # large discount, dominant account
    rows = [
        mk_row(account_key="X1", ae="Jordan", product="Hiring", unit_price=76, quantity=1, current_arr=500, tenure_years=1),
        mk_row(account_key="X2", ae="Jordan", product="Hiring", unit_price=60, quantity=1, current_arr=9500, tenure_years=1),
    ]
    s = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["summary"]
    jordan = next(a for a in s["discount_by_ae"] if a["ae"] == "Jordan")
    assert jordan["accounts"] == 2

    expected_weighted = (d1 * 500 + d2 * 9500) / 10000
    expected_simple = (d1 + d2) / 2
    assert jordan["weighted_avg_discount_pct_before"] == round(expected_weighted, 3)
    assert jordan["simple_avg_discount_pct_before"] == round(expected_simple, 3)
    # The dominant (9500 ARR) account's discount should show up in the
    # weighted number, not the unweighted/simple one.
    assert abs(jordan["weighted_avg_discount_pct_before"] - d2) < abs(jordan["simple_avg_discount_pct_before"] - d2)
    assert jordan["weighted_avg_discount_pct_before"] != jordan["simple_avg_discount_pct_before"]


# ---------------------------------------------------------------------------
# Invariants. These are spec-neutral correctness properties that must hold in
# every scenario, checked across a matrix of configs rather than one example.
# They encode docs/pricing-logic.md, which is the authoritative spec.
# ---------------------------------------------------------------------------

EPS = 1e-9

# A book covering every branch: below floor (cap binds / floor binds), between
# floor and list, just below list (standard increase would overshoot), exactly
# at list, above list, strategic, zero price, a multi-line customer, and an
# orphan with no Salesforce link and therefore no tenure.
INVARIANT_BOOK = [
    mk_row(account_key="C1", product="Hiring", unit_price=40, quantity=1, current_arr=480, tenure_years=1),
    mk_row(account_key="C2", product="Hiring", unit_price=55, quantity=2, current_arr=1320, tenure_years=1),
    mk_row(account_key="C3", product="HR", unit_price=90, quantity=3, current_arr=3240, tenure_years=1),
    mk_row(account_key="C4", product="Hiring", unit_price=77, quantity=1, current_arr=924, tenure_years=1),
    mk_row(account_key="C5", product="Hiring", unit_price=78.75, quantity=1, current_arr=945, tenure_years=1),
    mk_row(account_key="C6", product="Hiring", unit_price=90, quantity=1, current_arr=1080, tenure_years=1),
    mk_row(account_key="C7", product="Hiring", unit_price=40, quantity=10, current_arr=4800, tenure_years=6),
    mk_row(account_key="C8", product="Payroll", unit_price=0, quantity=5, current_arr=0, tenure_years=2),
    mk_row(account_key="C9", product="Hiring", unit_price=45, quantity=2, current_arr=1080, tenure_years=2),
    mk_row(account_key="C9", product="HR", unit_price=95, quantity=1, current_arr=1140, tenure_years=2),
    mk_row(account_key="C9", product="Payroll", unit_price=9, quantity=4, current_arr=432, tenure_years=2),
    # High volume on Payroll (200 >= 120) but zero tenure: must never be strategic.
    mk_row(account_key="stripe:orphan", product="Payroll", unit_price=10, quantity=200, current_arr=24000, tenure_years=0),
]

# Baseline (case-study) policy: no cap, no list ceiling, no above-list hold.
BASELINE_CONFIGS = [
    {},
    {"increase_pct": 0.0},
    {"increase_pct": 10.0},
    {"increase_pct": 20.0},
    {"increase_pct": 10.0, "tenure_years": 99.0},                                  # nobody qualifies as legacy
    {"increase_pct": 10.0, "high_volume": {"Hiring": 1, "HR": 1, "Payroll": 1}},    # volume test passes for all
]

# The same scenarios with the governance guardrails switched on.
GUARDRAIL_CONFIGS = [
    {**cfg, **GUARDRAILS, **extra}
    for cfg in BASELINE_CONFIGS
    for extra in ({}, {"max_increase_pct": 5.0}, {"max_increase_pct": 50.0})
]

ALL_CONFIGS = BASELINE_CONFIGS + GUARDRAIL_CONFIGS


def _runs(configs):
    for cfg in configs:
        yield cfg, simulate(INVARIANT_BOOK, config=cfg, analysis_date=ANALYSIS_DATE, horizon_months=12)


def test_invariant_no_line_price_ever_decreases():
    # Holds in every regime: this is a price-increase policy, so no branch of
    # it -- guardrails included -- may hand a customer a reduction.
    for cfg, result in _runs(ALL_CONFIGS):
        for r in result["rows"]:
            assert r["proposed_unit_price"] >= r["unit_price"] - EPS, (cfg, r["account_key"], r["product"])


def test_invariant_never_lifted_past_the_floor():
    # The floor is a target, not a springboard: the floor rule alone never
    # carries a line above it.
    for cfg, result in _runs(ALL_CONFIGS):
        for r in result["rows"]:
            if r["floor_binds"]:
                assert r["proposed_unit_price"] <= r["floor_price"] + EPS, (cfg, r["account_key"])


def test_invariant_at_or_above_list_receives_exactly_zero_under_guardrails():
    for cfg, result in _runs(GUARDRAIL_CONFIGS):
        for r in result["rows"]:
            if r["unit_price"] >= r["list_price"] > 0:
                assert r["proposed_unit_price"] == r["unit_price"], (cfg, r["account_key"])
                assert r["arr_change"] == 0, (cfg, r["account_key"])
                assert r["price_change_pct"] == 0, (cfg, r["account_key"])
                assert r["held_reason"] == "above_list", (cfg, r["account_key"])


def test_invariant_no_new_price_exceeds_list_under_guardrails():
    # With the clamp on, list is a ceiling: a line at or below list can be
    # raised to list but never through it, and a line already above list keeps
    # its price, so the policy never creates a new above-list price.
    for cfg, result in _runs(GUARDRAIL_CONFIGS):
        for r in result["rows"]:
            if r["list_price"] <= 0:
                continue
            if r["unit_price"] <= r["list_price"]:
                assert r["proposed_unit_price"] <= r["list_price"] + EPS, (cfg, r["account_key"], r["product"])
            assert r["proposed_unit_price"] <= max(r["list_price"], r["unit_price"]) + EPS, (cfg, r["account_key"])


def test_invariant_floor_uplift_never_exceeds_cap():
    # The cap bounds the floor uplift specifically. The standard increase is
    # not capped, and a line whose standard increase already clears the floor
    # never enters the uplift branch at all -- which is what `floor_binds`
    # records, as distinct from merely having started below floor.
    for cfg, result in _runs(GUARDRAIL_CONFIGS):
        cap = result["config"]["max_increase_pct"]
        assert cap is not None
        for r in result["rows"]:
            if r["floor_binds"]:
                assert r["price_change_pct"] <= cap + 1e-6, (cfg, r["account_key"], r["price_change_pct"])


def test_invariant_customer_total_stays_within_cap():
    # A per-line cap makes the customer total a weighted average of per-line
    # increases, so the customer-level bound holds for free whenever the
    # standard increase is itself within the cap.
    for cfg, result in _runs(GUARDRAIL_CONFIGS):
        conf = result["config"]
        if conf["increase_pct"] > conf["max_increase_pct"]:
            continue
        totals = {}
        for r in result["rows"]:
            before, after = totals.get(r["account_key"], (0.0, 0.0))
            totals[r["account_key"]] = (before + r["unit_price"] * r["quantity"] * 12,
                                        after + r["proposed_unit_price"] * r["quantity"] * 12)
        for key, (before, after) in totals.items():
            if before <= 0:
                continue
            assert (after / before - 1) * 100 <= conf["max_increase_pct"] + 1e-6, (cfg, key)


def test_invariant_customer_counts_are_distinct_not_line_counts():
    result = simulate(INVARIANT_BOOK, analysis_date=ANALYSIS_DATE, horizon_months=12)
    s = result["summary"]
    distinct_customers = len({r["account_key"] for r in INVARIANT_BOOK})
    assert s["accounts"] == distinct_customers
    assert s["subscriptions"] == len(INVARIANT_BOOK)
    assert s["subscriptions"] > s["accounts"]  # C9 contributes three lines
    # Every customer-level count is bounded by the distinct customer count,
    # which a count() over the line table would breach.
    for field in ("accounts_affected", "strategic_accounts", "floor_normalization_accounts",
                  "deferred_accounts", "held_above_list_accounts", "held_zero_price_accounts",
                  "breakeven_accounts"):
        assert s[field] <= distinct_customers, field
    ae_accounts = sum(a["accounts"] for a in s["discount_by_ae"])
    assert ae_accounts == distinct_customers


def test_invariant_null_tenure_fails_the_tenure_test():
    # The orphan line has no Salesforce link, so transform resolves its tenure
    # to 0. It clears the volume threshold but must never qualify as legacy.
    for cfg, result in _runs(ALL_CONFIGS):
        orphan = next(r for r in result["rows"] if r["account_key"] == "stripe:orphan")
        assert orphan["tenure_years"] == 0
        assert orphan["strategic_legacy"] is False, cfg


def test_invariant_annual_unit_amounts_are_divided_by_12():
    accounts, contracts, customers = _base_frames()
    subscriptions = pd.DataFrame([
        # $945.00/yr per unit -> $78.75 monthly-equivalent per unit.
        {"subscription_item_id": "siYear", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 94500, "billing_interval": "year", "quantity": 2},
        # $78.75/mo per unit -> unchanged.
        {"subscription_item_id": "siMonth", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 7875, "billing_interval": "month", "quantity": 2},
    ])
    rows, _ = build_rows(accounts, contracts, customers, subscriptions)
    by_id = {r["subscription_item_id"]: r for r in rows}
    assert by_id["siYear"]["unit_price"] == 78.75
    assert by_id["siMonth"]["unit_price"] == 78.75
    # Both normalize to the same monthly-equivalent ARR -- no 12x inflation.
    assert by_id["siYear"]["current_arr"] == by_id["siMonth"]["current_arr"]


def test_invariant_inactive_lines_excluded_from_population():
    # This codebase has no subscription-status column; staleness of
    # last_billing_date is how a no-longer-billing line leaves the population.
    # Such lines must be dropped outright, not carried as $0 rows that would
    # dilute every average, percentage and customer count.
    accounts = pd.DataFrame([
        {"account_id": "001A", "account_name": "Fresh Co", "customer_arr__c": 6000,
         "number_of_locations__c": 4, "csm_name__c": "Alex", "account_ae": "Jordan", "created_date": "2024-01-10"},
        {"account_id": "001B", "account_name": "Stale Co", "customer_arr__c": 6000,
         "number_of_locations__c": 4, "csm_name__c": "Alex", "account_ae": "Jordan", "created_date": "2024-01-10"},
    ])
    contracts = pd.DataFrame([
        {"contract_id": "800A", "account_id": "001A", "start_date": "2026-01-01",
         "end_date": "2026-12-31", "contract_term_months": 12},
    ])
    customers = pd.DataFrame([
        {"stripe_customer_id": "cusFresh", "name": "Fresh Co", "email": "a@x.test",
         "last_billing_date": "2026-06-15", "metadata_salesforce_id": "001A", "created": "2024-01-10"},
        {"stripe_customer_id": "cusStale", "name": "Stale Co", "email": "b@x.test",
         "last_billing_date": "2025-01-01", "metadata_salesforce_id": "001B", "created": "2024-01-10"},
    ])
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siFresh", "stripe_customer_id": "cusFresh", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
        {"subscription_item_id": "siStale", "stripe_customer_id": "cusStale", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions)
    assert [r["subscription_item_id"] for r in rows] == ["siFresh"]
    assert validation["active_subscriptions"] == 1
    assert validation["active_accounts"] == 1

    s = simulate(rows, analysis_date=validation["analysis_date"], horizon_months=12)["summary"]
    assert s["accounts"] == 1
    assert s["subscriptions"] == 1


# ---------------------------------------------------------------------------
# Strategic-legacy qualification. Both halves of the test must pass, and the
# volume half is OR across the account's products, not AND.
# ---------------------------------------------------------------------------

def test_high_volume_without_tenure_is_not_strategic():
    rows = [mk_row(unit_price=50, quantity=10, tenure_years=1)]  # 10 >= 7 Hiring, tenure 1 < 3
    row = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["strategic_legacy"] is False
    assert row["proposed_unit_price"] == 60.0  # treated as standard: lifted to floor


def test_tenure_without_high_volume_is_not_strategic():
    rows = [mk_row(unit_price=50, quantity=2, tenure_years=8)]  # 2 < 7 Hiring, tenure 8 >= 3
    row = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["strategic_legacy"] is False
    assert row["proposed_unit_price"] == 60.0


def test_strategic_status_is_per_account_across_products():
    # Qualifies on Payroll volume alone (200 >= 120); the exception is an
    # account-level status, so the account's Hiring line inherits it and stays
    # below the 60 floor rather than being normalized.
    rows = [
        mk_row(account_key="M", product="Hiring", unit_price=50, quantity=2, tenure_years=6),
        mk_row(account_key="M", product="Payroll", unit_price=9, quantity=200, tenure_years=6),
    ]
    hiring, payroll = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"]
    assert hiring["strategic_legacy"] is True and payroll["strategic_legacy"] is True
    assert hiring["proposed_unit_price"] == round(50 * 1.05, 4)
    assert hiring["below_floor_after"] is True          # left below floor, by design
    assert payroll["proposed_unit_price"] == round(9 * 1.05, 4)


# ---------------------------------------------------------------------------
# Effective-date timing.
# ---------------------------------------------------------------------------

def _timed_row(**kw):
    base = dict(account_key="T", account_name="T", product="Hiring", quantity=2,
                unit_price=70, current_arr=1680, tenure_years=1)
    base.update(kw)
    return base


def test_in_term_contract_is_repriced_at_renewal_not_at_next_invoice():
    # Eligible only at contract end, which is later than the notice window.
    row = simulate([_timed_row(next_eligible_date="2026-12-31")],
                    analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["effective_date"] == "2026-12-31"
    assert row["realized_in_horizon"] is True


def test_out_of_term_line_moves_at_the_notice_floor():
    # Next eligible 2026-07-10 is inside the 2-month notice window, so the
    # notice period sets the effective date instead.
    row = simulate([_timed_row(next_eligible_date="2026-07-10")],
                    analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["effective_date"] == "2026-08-15"


def test_notice_period_override_is_configurable():
    rows = [_timed_row(next_eligible_date="2026-07-10")]
    assert simulate(rows, config={"notice_months": 0}, analysis_date=ANALYSIS_DATE,
                     horizon_months=12)["rows"][0]["effective_date"] == "2026-07-10"
    assert simulate(rows, config={"notice_months": 6}, analysis_date=ANALYSIS_DATE,
                     horizon_months=12)["rows"][0]["effective_date"] == "2026-12-15"


def test_increase_outside_the_horizon_is_deferred_not_realized():
    row = simulate([_timed_row(next_eligible_date="2028-01-01")],
                    analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert row["realized_in_horizon"] is False
    assert row["segment"] == "Deferred"
    assert row["realized_revenue_in_horizon"] == 0
    assert row["arr_change"] > 0  # run-rate uplift still exists, just not yet


# ---------------------------------------------------------------------------
# Run-rate ARR vs cumulative revenue -- different quantities, never
# interchangeable.
# ---------------------------------------------------------------------------

def test_cumulative_revenue_is_prorated_run_rate_arr():
    from datetime import date
    row = simulate([_timed_row(next_eligible_date="2026-12-31")],
                    analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    days = (date(2027, 6, 15) - date(2026, 12, 31)).days
    assert row["realized_revenue_in_horizon"] == round(row["arr_change"] * days / 365.25, 2)
    # Only a fraction of the year is captured, so realized revenue is well
    # short of the annualized run-rate figure.
    assert row["realized_revenue_in_horizon"] < row["arr_change"]


def test_summary_reports_run_rate_and_cumulative_separately():
    rows = [_timed_row(account_key="A", next_eligible_date="2026-07-01"),
            _timed_row(account_key="B", next_eligible_date="2027-03-01")]
    s = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["summary"]
    assert s["run_rate_arr_uplift"] > s["cumulative_incremental_revenue"] > 0
    # The later change contributes full run-rate but only a sliver of revenue.
    assert s["run_rate_arr_uplift"] == round(
        sum(r["arr_change"] for r in simulate(rows, analysis_date=ANALYSIS_DATE,
                                               horizon_months=12)["rows"]), 3)


def _base_frames():
    accounts = pd.DataFrame([
        {"account_id": "001A", "account_name": "Sample Burgers", "customer_arr__c": 6000,
         "number_of_locations__c": 4, "csm_name__c": "Alex", "account_ae": "Jordan", "created_date": "2024-01-10"},
    ])
    contracts = pd.DataFrame([
        {"contract_id": "800A", "account_id": "001A", "start_date": "2026-01-01",
         "end_date": "2026-12-31", "contract_term_months": 12},
    ])
    customers = pd.DataFrame([
        {"stripe_customer_id": "cusA", "name": "Sample Burgers", "email": "billing@sample.test",
         "last_billing_date": "2026-06-15", "metadata_salesforce_id": "001A", "created": "2024-01-10"},
    ])
    return accounts, contracts, customers


def test_unknown_billing_interval_and_product_rejected():
    accounts, contracts, customers = _base_frames()
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
        {"subscription_item_id": "siB", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "week", "quantity": 4},
        {"subscription_item_id": "siC", "stripe_customer_id": "cusA", "price_nickname": "Bogus Product",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions)
    assert len(rows) == 1
    issue = next(i for i in validation["issues"] if i["code"] == "INVALID_SUBSCRIPTION_ROWS")
    assert issue["count"] == 2


def test_duplicate_key_detection():
    accounts, contracts, customers = _base_frames()
    customers = pd.concat([customers, customers], ignore_index=True)
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions)
    codes = {i["code"]: i["count"] for i in validation["issues"]}
    assert codes["DUPLICATE_CUSTOMERS"] == 1
    assert codes["DUPLICATE_SUBSCRIPTIONS"] == 1
    assert len(rows) == 1


def test_evergreen_contract_wins_over_expired_contract():
    accounts, _, customers = _base_frames()
    contracts = pd.DataFrame([
        {"contract_id": "800A", "account_id": "001A", "start_date": "2020-01-01",
         "end_date": "2020-12-31", "contract_term_months": 12},
        {"contract_id": "800B", "account_id": "001A", "start_date": "2021-01-01",
         "end_date": None, "contract_term_months": None},
    ])
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, _ = build_rows(accounts, contracts, customers, subscriptions)
    assert rows[0]["contract_status"] == "Evergreen"


def _sub(sid, cust, product="Hiring", amount=5800, interval="month", qty=4):
    return {"subscription_item_id": sid, "stripe_customer_id": cust, "price_nickname": product,
            "unit_amount": amount, "billing_interval": interval, "quantity": qty}


def _account(aid, name, created="2024-01-10"):
    return {"account_id": aid, "account_name": name, "customer_arr__c": 6000,
            "number_of_locations__c": 4, "csm_name__c": "Alex", "account_ae": "Jordan",
            "created_date": created}


def _customer(cid, name, email, last_billing, sf_id=None):
    return {"stripe_customer_id": cid, "name": name, "email": email,
            "last_billing_date": last_billing, "metadata_salesforce_id": sf_id,
            "created": "2024-01-10"}


# ---------------------------------------------------------------------------
# Active-status boundaries, measured in calendar months/years from the
# analysis date rather than 31/366-day arithmetic.
# ---------------------------------------------------------------------------

def test_monthly_active_boundary():
    accounts = pd.DataFrame([_account("001A", "On Co"), _account("001B", "Off Co")])
    contracts = pd.DataFrame(columns=["contract_id", "account_id", "start_date", "end_date", "contract_term_months"])
    customers = pd.DataFrame([
        _customer("cusOn", "On Co", "billing@onco.com", "2026-05-15", "001A"),    # exactly on the boundary
        _customer("cusOff", "Off Co", "billing@offco.com", "2026-05-14", "001B"),  # one day stale
    ])
    subscriptions = pd.DataFrame([_sub("siOn", "cusOn"), _sub("siOff", "cusOff")])
    rows, _ = build_rows(accounts, contracts, customers, subscriptions, analysis_date="2026-06-15")
    assert [r["subscription_item_id"] for r in rows] == ["siOn"]


def test_annual_active_boundary():
    accounts = pd.DataFrame([_account("001A", "On Co"), _account("001B", "Off Co")])
    contracts = pd.DataFrame(columns=["contract_id", "account_id", "start_date", "end_date", "contract_term_months"])
    customers = pd.DataFrame([
        _customer("cusOn", "On Co", "billing@onco.com", "2025-06-15", "001A"),
        _customer("cusOff", "Off Co", "billing@offco.com", "2025-06-14", "001B"),
    ])
    subscriptions = pd.DataFrame([_sub("siOn", "cusOn", interval="year", amount=94500),
                                  _sub("siOff", "cusOff", interval="year", amount=94500)])
    rows, _ = build_rows(accounts, contracts, customers, subscriptions, analysis_date="2026-06-15")
    assert [r["subscription_item_id"] for r in rows] == ["siOn"]


# ---------------------------------------------------------------------------
# Entity resolution: SF ID, then exact normalized name, then billing-email
# domain, then unmatched. Ambiguity is never resolved by guessing.
# ---------------------------------------------------------------------------

def test_match_hierarchy_resolves_each_tier():
    accounts = pd.DataFrame([
        _account("001A", "Alpha Inc"),
        _account("001B", "Bravo, Charlie and Delta"),
        _account("001C", "Perez Inc"),
        _account("001D", "Echo Ltd"),
    ])
    contracts = pd.DataFrame(columns=["contract_id", "account_id", "start_date", "end_date", "contract_term_months"])
    customers = pd.DataFrame([
        # tier 1: a valid Salesforce ID wins even though the name is a mess
        _customer("cus1", "totally different name", "billing@nowhere.example", "2026-06-10", "001A"),
        # tier 2: exact normalized name, with '&' treated as 'and'
        _customer("cus2", "Bravo, Charlie & Delta", "billing@unrelated.example", "2026-06-10"),
        # tier 3: name is misspelled, but the billing domain resolves it
        _customer("cus3", "Perz", "billing@perezinc.com", "2026-06-10"),
        # tier 4: nothing resolves
        _customer("cus4", "Ghost Co", "billing@ghostco.com", "2026-06-10"),
    ])
    subscriptions = pd.DataFrame([_sub(f"si{i}", f"cus{i}") for i in (1, 2, 3, 4)])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions, analysis_date="2026-06-15")
    by_sub = {r["subscription_item_id"]: r for r in rows}

    assert by_sub["si1"]["match_method"] == "salesforce_id" and by_sub["si1"]["salesforce_id"] == "001A"
    assert by_sub["si2"]["match_method"] == "exact_name" and by_sub["si2"]["salesforce_id"] == "001B"
    assert by_sub["si3"]["match_method"] == "email_domain" and by_sub["si3"]["salesforce_id"] == "001C"
    assert by_sub["si4"]["match_method"] == "unmatched"
    assert by_sub["si4"]["account_key"] == "stripe:cus4"
    assert validation["unmatched_customers"] == 1


def test_ambiguous_domain_stays_unmatched():
    # Two accounts normalize to the same key, so neither the name nor the
    # domain tier can pick one without guessing -- the customer stays unmatched.
    accounts = pd.DataFrame([_account("001A", "Acme Corp"), _account("001B", "Acme-Corp")])
    contracts = pd.DataFrame(columns=["contract_id", "account_id", "start_date", "end_date", "contract_term_months"])
    customers = pd.DataFrame([_customer("cusX", "Acme Corp", "billing@acmecorp.com", "2026-06-10")])
    subscriptions = pd.DataFrame([_sub("siX", "cusX")])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions, analysis_date="2026-06-15")
    assert rows[0]["match_method"] == "unmatched"
    assert rows[0]["salesforce_id"] is None
    assert validation["unmatched_customers"] == 1


def test_explicit_analysis_date_overrides_max_billing_date():
    accounts = pd.DataFrame([_account("001A", "On Co")])
    contracts = pd.DataFrame(columns=["contract_id", "account_id", "start_date", "end_date", "contract_term_months"])
    customers = pd.DataFrame([_customer("cusOn", "On Co", "billing@onco.com", "2026-06-10", "001A")])
    subscriptions = pd.DataFrame([_sub("siOn", "cusOn")])

    pinned = build_rows(accounts, contracts, customers, subscriptions, analysis_date="2026-06-15")[1]
    assert pinned["analysis_date"] == "2026-06-15"
    derived = build_rows(accounts, contracts, customers, subscriptions)[1]
    assert derived["analysis_date"] == "2026-06-10"  # falls back to latest billing date


def test_stale_salesforce_id_is_flagged_but_still_resolved():
    # A supplied SF ID that is not a real account is a data-quality problem, so
    # it is counted and surfaced -- but resolution continues down the hierarchy
    # rather than discarding the line's Salesforce context outright.
    accounts, contracts, customers = _base_frames()
    customers.loc[0, "metadata_salesforce_id"] = "999Z"  # not an account; name still matches
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions)
    issue = next(i for i in validation["issues"] if i["code"] == "STALE_SALESFORCE_ID")
    assert issue["count"] == 1
    assert rows[0]["salesforce_id"] == "001A"
    assert rows[0]["match_method"] == "exact_name"
