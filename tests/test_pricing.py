import pandas as pd
from app.pricing import simulate
from app.transform import build_rows

ANALYSIS_DATE = "2026-06-15"


def mk_row(**overrides):
    row = {
        "account_key": "A", "account_name": "A", "product": "Hiring",
        "quantity": 2, "unit_price": 58, "current_arr": 1392,
        "tenure_years": 1, "next_eligible_date": "2026-07-01",
    }
    row.update(overrides)
    return row


def test_floor_normalization_and_strategic_default():
    rows = [
        mk_row(account_key="A", quantity=2, unit_price=58, tenure_years=1),
        mk_row(account_key="B", quantity=10, unit_price=55, current_arr=6600, tenure_years=4),
    ]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    a, b = r["rows"]
    assert a["proposed_unit_price"] == 60.9
    assert b["strategic_legacy"] is True
    # v2 default: the floor now applies to strategic legacy accounts too (no
    # exemption unless legacy_floor_exemption is set), capped by the gentler
    # strategic glidepath rather than jumping straight to floor.
    assert round(b["proposed_unit_price"], 2) == 60.0


def test_glidepath_cap_limits_floor_normalization():
    # Far below floor (60): a flat normalize-to-floor would be +50%, but the
    # standard glidepath cap (default 20%) limits it to current * 1.20.
    rows = [mk_row(unit_price=40, tenure_years=1)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 48.0
    assert row["price_change_pct"] == 20.0
    assert row["below_floor_after"] is True
    assert row["arr_left_below_floor"] == round((60 - 48.0) * 2 * 12, 2)


def test_strategic_gentler_cap():
    # Strategic legacy accounts are capped more tightly (default 10%) than
    # standard accounts, even though the floor still normally applies to them.
    rows = [mk_row(unit_price=40, tenure_years=5, quantity=10)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["strategic_legacy"] is True
    assert row["proposed_unit_price"] == 44.0
    assert row["price_change_pct"] == 10.0


def test_legacy_floor_exemption_restores_old_behavior():
    # Far enough below floor (60) that the strategic 10% cap keeps the
    # default (floor-applies) outcome short of the floor.
    rows = [mk_row(unit_price=50, tenure_years=5, quantity=10)]
    default = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert default["proposed_unit_price"] == round(50 * 1.10, 4)  # capped en route to floor
    assert default["below_floor_after"] is True

    exempt = simulate(rows, config={"legacy_floor_exemption": True},
                       analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert exempt["proposed_unit_price"] == round(50 * 1.05, 4)  # standard increase only, no floor target


def test_above_list_pricing_is_held():
    rows = [mk_row(unit_price=90, tenure_years=1)]  # list price for Hiring is 78.75
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 90.0
    assert row["arr_change"] == 0
    assert row["segment"] == "Held (Above List)"


def test_increase_above_list_flag_allows_increase():
    rows = [mk_row(unit_price=90, tenure_years=1)]
    r = simulate(rows, config={"increase_above_list": True}, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] > 90.0
    assert row["segment"] != "Held (Above List)"


def test_zero_or_negative_price_is_held_for_review():
    rows = [mk_row(unit_price=0, tenure_years=1)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 0
    assert row["arr_change"] == 0
    assert row["segment"] == "Held (Zero Price)"


def test_breakeven_math():
    rows = [
        mk_row(account_key="A", product="HR", unit_price=100, quantity=10, current_arr=2000, tenure_years=1),
        mk_row(account_key="B", product="HR", unit_price=100, quantity=5, current_arr=500, tenure_years=1),
    ]
    r = simulate(rows, config={"increase_pct": 10.0}, analysis_date=ANALYSIS_DATE, horizon_months=12)
    s = r["summary"]
    expansion = s["gross_arr_expansion"]
    assert expansion == round((110 - 100) * 10 * 12 + (110 - 100) * 5 * 12, 2)
    current_affected = 2000 + 500
    assert s["breakeven_churn_pct"] == round(expansion / current_affected * 100, 2)
    # Account A alone (2000 ARR) already exceeds the combined expansion
    # (1800), so churning just the single biggest account breaks even.
    assert s["breakeven_accounts"] == 1


def test_arr_weighted_average_increase_differs_from_simple_average():
    rows = [
        # Below floor and capped at 20% -> price_change_pct == 20.0, small ARR weight.
        mk_row(account_key="A", product="Payroll", unit_price=9, quantity=1, current_arr=1000, tenure_years=1),
        # Standard 5% increase, far larger ARR weight.
        mk_row(account_key="B", product="Payroll", unit_price=12, quantity=1, current_arr=9000, tenure_years=1),
    ]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    s = r["summary"]
    a, b = r["rows"]
    assert a["price_change_pct"] == 20.0
    assert b["price_change_pct"] == 5.0
    simple_avg = (a["price_change_pct"] + b["price_change_pct"]) / 2
    weighted_avg = (a["price_change_pct"] * 1000 + b["price_change_pct"] * 9000) / 10000
    assert s["avg_unit_increase_pct"] == round(simple_avg, 2)
    assert s["arr_weighted_avg_increase_pct"] == round(weighted_avg, 2)
    assert s["arr_weighted_avg_increase_pct"] != s["avg_unit_increase_pct"]


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


def test_stale_salesforce_id_is_excluded_not_name_matched():
    accounts, contracts, customers = _base_frames()
    customers.loc[0, "metadata_salesforce_id"] = "999Z"  # does not exist, but name still matches
    subscriptions = pd.DataFrame([
        {"subscription_item_id": "siA", "stripe_customer_id": "cusA", "price_nickname": "Hiring",
         "unit_amount": 5800, "billing_interval": "month", "quantity": 4},
    ])
    rows, validation = build_rows(accounts, contracts, customers, subscriptions)
    issue = next(i for i in validation["issues"] if i["code"] == "STALE_SALESFORCE_ID")
    assert issue["count"] == 1
    assert rows[0]["salesforce_id"] is None
    assert rows[0]["account_key"] == "stripe:cusA"
