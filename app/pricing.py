from datetime import date
from calendar import monthrange

DEFAULT_CONFIG={
 "increase_pct":5.0,"notice_months":2,"churn_pct":3.0,"tenure_years":3.0,
 "lists":{"Hiring":78.75,"HR":105.0,"Payroll":14.70},
 "floors":{"Hiring":60.0,"HR":80.0,"Payroll":11.0},
 "high_volume":{"Hiring":7,"HR":7,"Payroll":120},
 "max_increase_pct":20.0,
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
    elif current>=listp:
        # At or above list: no increase, unconditionally. A price already at
        # list has nowhere to go without breaching the list ceiling below.
        proposed=current; held_reason="above_list"
    else:
        base=current*(1+float(cfg["increase_pct"])/100)
        if is_strategic:
            # Strategic legacy accounts receive the standard increase only.
            # Floor logic and the cap never apply to them, unconditionally
            # (not via a toggle) -- they may remain deeply below floor.
            proposed=base
        elif base<floor:
            # Standard accounts: raise beyond the standard increase to close
            # the floor gap, but never past the floor and never past the cap.
            cap_price=current*(1+float(cfg["max_increase_pct"])/100)
            proposed=min(floor,cap_price)
        else:
            proposed=base
        # List is a ceiling: the policy never creates a new above-list price,
        # so a below-list line can be raised to list but never through it.
        proposed=min(proposed,listp)

    delta=(proposed-current)*quantity*12
    eligible=date.fromisoformat(r["next_eligible_date"]) if r.get("next_eligible_date") else None
    effective=max(eligible,notice) if eligible else notice
    realized=effective<=horizon
    months=whole_months_between(effective,horizon) if realized else 0
    realized_revenue=delta*months/12
    discount_after=(1-proposed/listp) if listp else 0
    discount_before=(1-current/listp) if listp else 0
    below_floor_after=proposed<floor
    arr_short_of_floor=(floor-proposed)*quantity*12 if below_floor_after else 0.0

    o={**r,"strategic_legacy":is_strategic,"list_price":listp,"floor_price":floor,
       "proposed_unit_price":round(proposed,4),
       "price_change_pct":round((proposed/current-1)*100,3) if current else 0,
       "discount_from_list_pct":round(discount_after*100,3),
       "discount_from_list_pct_before":round(discount_before*100,3),
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

def _weighted_avg(rows, weight_key, value_fn):
    total=sum(r[weight_key] for r in rows)
    if not total: return 0.0
    return sum(value_fn(r)*r[weight_key] for r in rows)/total

def _weighted_share(rows, weight_key, predicate):
    total=sum(r[weight_key] for r in rows)
    if not total: return 0.0
    return sum(r[weight_key] for r in rows if predicate(r))/total*100

def _discount_by_ae(rows):
    by_ae={}
    for r in rows:
        by_ae.setdefault(r.get("ae") or "Unassigned", []).append(r)
    out=[]
    for ae, rs in by_ae.items():
        arr_total=sum(r["current_arr"] for r in rs)
        out.append({
            "ae": ae,
            "accounts": len({r["account_key"] for r in rs}),
            "weighted_avg_discount_pct_before": round((sum(r["discount_from_list_pct_before"]*r["current_arr"] for r in rs)/arr_total) if arr_total else 0.0, 3),
            "weighted_avg_discount_pct_after": round((sum(r["discount_from_list_pct"]*r["current_arr"] for r in rs)/arr_total) if arr_total else 0.0, 3),
            "simple_avg_discount_pct_before": round(sum(r["discount_from_list_pct_before"] for r in rs)/len(rs), 3),
            "simple_avg_discount_pct_after": round(sum(r["discount_from_list_pct"] for r in rs)/len(rs), 3),
        })
    out.sort(key=lambda d: -d["weighted_avg_discount_pct_after"])
    return out

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

    # Distribution/discount metrics reflect the full modeled book (not just
    # what's realized within the horizon) -- this is a snapshot of where
    # pricing sits today vs. after the proposed change is fully applied.
    within_10pct_before=lambda r: r["list_price"]>0 and abs(r["unit_price"]/r["list_price"]-1)<=0.10
    within_10pct_after=lambda r: r["list_price"]>0 and abs(r["proposed_unit_price"]/r["list_price"]-1)<=0.10

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
             "pct_within_10pct_of_list_before":round(_weighted_share(out,"current_arr",within_10pct_before),2),
             "pct_within_10pct_of_list_after":round(_weighted_share(out,"current_arr",within_10pct_after),2),
             "pct_below_floor_before":round(_weighted_share(out,"current_arr",lambda r:r["below_floor_before"]),2),
             "pct_below_floor_after":round(_weighted_share(out,"current_arr",lambda r:r["below_floor_after"]),2),
             "discount_from_list_avg_pct_before":round(_weighted_avg(out,"current_arr",lambda r:r["discount_from_list_pct_before"]),3),
             "discount_from_list_avg_pct_after":round(_weighted_avg(out,"current_arr",lambda r:r["discount_from_list_pct"]),3),
             "discount_by_ae":_discount_by_ae(out),
             "horizon_end":horizon.isoformat()}
    return {"config":cfg,"summary":summary,"rows":out}

def price_distribution(result):
    """Per-product before/after unit-price distributions for charting,
    alongside that product's floor and list price as reference lines."""
    cfg=result["config"]; rows=result["rows"]
    by_product={}
    for r in rows:
        p=r["product"]
        entry=by_product.setdefault(p, {
            "product":p,
            "floor":float(cfg["floors"].get(p,0)),
            "list_price":float(cfg["lists"].get(p,0)),
            "before":[],"after":[],
        })
        entry["before"].append(r["unit_price"])
        entry["after"].append(r["proposed_unit_price"])
    dist=list(by_product.values())
    for entry in dist:
        entry["before"]=sorted(entry["before"])
        entry["after"]=sorted(entry["after"])
    return dist

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
