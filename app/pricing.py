from datetime import date
from calendar import monthrange

DEFAULT_CONFIG={
 "increase_pct":5.0,"notice_months":2,"churn_pct":3.0,"tenure_years":3.0,
 "lists":{"Hiring":78.75,"HR":105.0,"Payroll":14.70},
 "floors":{"Hiring":60.0,"HR":80.0,"Payroll":11.0},
 "high_volume":{"Hiring":7,"HR":7,"Payroll":120},
 "max_increase_pct":20.0,"max_increase_pct_strategic":10.0,
 "legacy_floor_exemption":False,"increase_above_list":False,
}

def add_months(d, months):
    m=d.month-1+months; y=d.year+m//12; m=m%12+1
    return date(y,m,min(d.day,monthrange(y,m)[1]))

def whole_months_between(start, end):
    """Whole months from start to end (end assumed >= start; 0 otherwise)."""
    if end<=start: return 0
    months=(end.year-start.year)*12+(end.month-start.month)
    if end.day<start.day: months-=1
    return max(0,months)

def _merge_config(config):
    cfg={**DEFAULT_CONFIG, **(config or {})}
    for k in ["lists","floors","high_volume"]:
        cfg[k]={**DEFAULT_CONFIG[k], **((config or {}).get(k,{}) if config else {})}
    return cfg

def _price_row(r, cfg, strategic, notice, horizon):
    current=float(r["unit_price"]); product=r["product"]
    floor=float(cfg["floors"].get(product,0)); listp=float(cfg["lists"].get(product,0))
    quantity=float(r["quantity"])
    is_strategic=strategic[r["account_key"]]

    held_reason=None
    if current<=0:
        proposed=current; held_reason="zero_price"
    elif current>listp and not cfg.get("increase_above_list",False):
        proposed=current; held_reason="above_list"
    else:
        base=current*(1+float(cfg["increase_pct"])/100)
        floor_exempt=is_strategic and cfg.get("legacy_floor_exemption",False)
        target=base if floor_exempt else max(base,floor)
        cap_pct=float(cfg["max_increase_pct_strategic"]) if is_strategic else float(cfg["max_increase_pct"])
        capped_price=current*(1+cap_pct/100)
        proposed=min(target,capped_price)

    delta=(proposed-current)*quantity*12
    eligible=date.fromisoformat(r["next_eligible_date"]) if r.get("next_eligible_date") else None
    effective=max(eligible,notice) if eligible else notice
    realized=effective<=horizon
    months=whole_months_between(effective,horizon) if realized else 0
    realized_revenue=delta*months/12
    discount=(1-proposed/listp) if listp else 0
    below_floor_after=proposed<floor
    arr_short_of_floor=(floor-proposed)*quantity*12 if below_floor_after else 0.0

    o={**r,"strategic_legacy":is_strategic,"list_price":listp,"floor_price":floor,
       "proposed_unit_price":round(proposed,4),
       "price_change_pct":round((proposed/current-1)*100,3) if current else 0,
       "discount_from_list_pct":round(discount*100,3),
       "arr_change":round(delta,2),
       "realized_revenue_in_horizon":round(realized_revenue,2),
       "effective_date":effective.isoformat(),"realized_in_horizon":realized,
       "below_floor_before":current<floor,"below_floor_after":below_floor_after,
       "above_list_after":proposed>listp,"held_reason":held_reason,
       "arr_left_below_floor":round(arr_short_of_floor,2)}
    if held_reason=="zero_price": segment="Held (Zero Price)"
    elif held_reason=="above_list": segment="Held (Above List)"
    elif not realized: segment="Deferred"
    elif is_strategic: segment="Strategic Legacy"
    elif current<floor: segment="Floor Normalize"
    else: segment="Standard Increase"
    o["segment"]=segment
    return o

def simulate(rows, config=None, analysis_date=None, horizon_months=12):
    cfg=_merge_config(config)
    ad=date.fromisoformat(analysis_date); horizon=add_months(ad,int(horizon_months)); notice=add_months(ad,int(cfg["notice_months"]))
    by_account={}
    for r in rows: by_account.setdefault(r["account_key"],[]).append(r)
    strategic={}
    for k,rs in by_account.items():
        tenure=max(x["tenure_years"] for x in rs)
        high=any(x["quantity"]>=float(cfg["high_volume"].get(x["product"],10**9)) for x in rs)
        strategic[k]=tenure>=float(cfg["tenure_years"]) and high
    out=[_price_row(r,cfg,strategic,notice,horizon) for r in rows]

    realized=[r for r in out if r["realized_in_horizon"]]
    expansion=sum(r["arr_change"] for r in realized if r["arr_change"]>0)
    affected_accounts={r["account_key"] for r in realized if r["arr_change"]>0}
    current_affected=sum(sum(x["current_arr"] for x in by_account[k]) for k in affected_accounts)
    churn_loss=current_affected*float(cfg["churn_pct"])/100
    realized_revenue_total=sum(r["realized_revenue_in_horizon"] for r in out)
    arr_left_below_floor=sum(r["arr_left_below_floor"] for r in out)
    breakeven_churn_pct=(expansion/current_affected*100) if current_affected else 0.0

    account_current_arr={k:sum(x["current_arr"] for x in by_account[k]) for k in affected_accounts}
    breakeven_accounts=0
    cum=0.0
    for _,arr in sorted(account_current_arr.items(), key=lambda kv:-kv[1]):
        if cum>=expansion: break
        cum+=arr; breakeven_accounts+=1

    realized_arr_weight=sum(r["current_arr"] for r in realized)
    arr_weighted_avg_increase_pct=(sum(r["price_change_pct"]*r["current_arr"] for r in realized)/realized_arr_weight) if realized_arr_weight else 0.0

    summary={"accounts":len(by_account),"subscriptions":len(out),"accounts_affected":len(affected_accounts),
             "strategic_accounts":sum(strategic.values()),
             "floor_normalization_accounts":len({r['account_key'] for r in realized if r['segment']=='Floor Normalize'}),
             "deferred_accounts":len({r['account_key'] for r in out if not r['realized_in_horizon']}),
             "held_above_list_accounts":len({r['account_key'] for r in out if r['held_reason']=='above_list'}),
             "held_zero_price_accounts":len({r['account_key'] for r in out if r['held_reason']=='zero_price'}),
             "gross_arr_expansion":round(expansion,2),"modeled_churn_loss":round(churn_loss,2),
             "risk_adjusted_net":round(expansion-churn_loss,2),
             "avg_unit_increase_pct":round(sum(r['price_change_pct'] for r in realized)/len(realized),2) if realized else 0,
             "arr_weighted_avg_increase_pct":round(arr_weighted_avg_increase_pct,2),
             "realized_revenue_in_horizon":round(realized_revenue_total,2),
             "breakeven_churn_pct":round(breakeven_churn_pct,2),
             "breakeven_accounts":breakeven_accounts,
             "arr_left_below_floor":round(arr_left_below_floor,2),
             "above_list_after_count":sum(1 for r in out if r['above_list_after']),
             "horizon_end":horizon.isoformat()}
    return {"config":cfg,"summary":summary,"rows":out}

def sensitivity_grid(rows, config=None, analysis_date=None, horizon_months=12, increase_options=None, churn_options=None):
    """Risk-adjusted net ARR across a grid of standard increase % and churn % assumptions."""
    increase_options=increase_options or [5,10,15,20]
    churn_options=churn_options or [1,2,3,5,7,10]
    grid=[]
    for inc in increase_options:
        for churn in churn_options:
            cfg={**(config or {}),"increase_pct":inc,"churn_pct":churn}
            result=simulate(rows,cfg,analysis_date,horizon_months)
            s=result["summary"]
            grid.append({"increase_pct":inc,"churn_pct":churn,
                         "gross_arr_expansion":s["gross_arr_expansion"],
                         "modeled_churn_loss":s["modeled_churn_loss"],
                         "risk_adjusted_net":s["risk_adjusted_net"]})
    return grid
