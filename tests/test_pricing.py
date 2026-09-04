from app.pricing import simulate
ROWS=[{"account_key":"A","account_name":"A","product":"Hiring","quantity":2,"unit_price":58,"current_arr":1392,"tenure_years":1,"next_eligible_date":"2026-07-01"},{"account_key":"B","account_name":"B","product":"Hiring","quantity":10,"unit_price":55,"current_arr":6600,"tenure_years":4,"next_eligible_date":"2026-07-01"}]
def test_floor_and_legacy_exception():
    r=simulate(ROWS,analysis_date="2026-06-15",horizon_months=12)
    a,b=r['rows']
    assert a['proposed_unit_price']==60.9
    assert round(b['proposed_unit_price'],2)==57.75
    assert b['strategic_legacy'] is True
