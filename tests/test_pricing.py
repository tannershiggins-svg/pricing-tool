import pandas as pd
from app.pricing import simulate, price_distribution
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


def test_standard_account_capped_when_cap_binds_before_floor():
    # Far below floor (60): a flat normalize-to-floor would be +50%, but the
    # cap (default 20%) limits the raise to current * 1.20, still short of floor.
    rows = [mk_row(unit_price=40, tenure_years=1)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
    row = r["rows"][0]
    assert row["proposed_unit_price"] == 48.0
    assert row["price_change_pct"] == 20.0
    assert row["below_floor_after"] is True
    assert row["arr_left_below_floor"] == round((60 - 48.0) * 2 * 12, 2)


def test_standard_account_reaches_floor_when_floor_binds_before_cap():
    # Close enough to floor (60) that current * 1.20 would overshoot it, so
    # the account is raised only to the floor, not the full cap.
    rows = [mk_row(unit_price=55, tenure_years=1)]
    r = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)
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


def test_above_list_pricing_is_held_under_all_configs():
    rows = [mk_row(unit_price=90, tenure_years=1)]  # list price for Hiring is 78.75
    default = simulate(rows, analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert default["proposed_unit_price"] == 90.0
    assert default["arr_change"] == 0
    assert default["segment"] == "Held (Above List)"

    # The old increase_above_list config key no longer does anything -- it's
    # simply ignored, not a way to re-enable raising an above-list price.
    ignored = simulate(rows, config={"increase_above_list": True, "max_increase_pct": 999},
                        analysis_date=ANALYSIS_DATE, horizon_months=12)["rows"][0]
    assert ignored["proposed_unit_price"] == 90.0
    assert ignored["segment"] == "Held (Above List)"


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
    # 45 -> raised toward floor, capped at min(60, 45*1.2=54) == 54; 90 held above list.
    assert hiring["after"] == sorted([54.0, 90])

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
