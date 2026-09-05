from datetime import date
from calendar import monthrange

DEFAULT_CONFIG={
 "increase_pct":5.0,"notice_months":2,"churn_pct":3.0,"tenure_years":3.0,
 "lists":{"Hiring":75.0,"HR":100.0,"Payroll":14.0},
 "floors":{"Hiring":60.0,"HR":80.0,"Payroll":11.0},
 "high_volume":{"Hiring":7,"HR":7,"Payroll":120},
 # Optional governance guardrails, off by default. The baseline policy is the
 # case-study policy: standard = max(current x (1+increase), floor), strategic
 # legacy = current x (1+increase). Each guardrail below is an opt-in overlay.
 "max_increase_pct":None,        # cap on the floor uplift; None = uncapped
 # List becomes a ceiling: a price below list rises no further than list, and a
 # price already at or above list holds where it is (the ceiling never pushes
 # anyone down, so it can't create a decrease).
 "cap_at_list":False,
 # Churn is a sensitivity assumption, never a forecast or an elasticity
 # estimate. "post_increase" applies churn_pct to the post-increase ARR of
 # affected accounts, which is the more conservative basis.
 "churn_basis":"post_increase",  # or "pre_increase"
}
DAYS_PER_YEAR=365.25

def add_months(d, months):
    m=d.month-1+months; y=d.year+m//12; m=m%12+1
    return date(y,m,min(d.day,monthrange(y,m)[1]))

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

    held_reason=None; floor_binds=False
    cap_list=bool(cfg.get("cap_at_list")) and listp>0
    if current<=0:
        proposed=current; held_reason="zero_price"
    elif cap_list and current>=listp:
        # Already at or above list, so the ceiling leaves them exactly where
        # they are. It never reaches down, which is why capping at list also
        # holds these accounts instead of cutting them.
        proposed=current; held_reason="above_list"
    else:
        base=current*(1+float(cfg["increase_pct"])/100)
        # The floor only "binds" when the standard increase alone would have
        # left the line below it. A line that starts below floor but clears it
        # on the standard increase is not a floor normalization.
        floor_binds=not is_strategic and base<floor
        if is_strategic:
            # Strategic legacy accounts receive the standard increase only.
            # Floor logic and the cap never apply to them -- they may remain
            # below floor, and that gap is reported rather than closed.
            proposed=base
        elif base<floor:
            # Standard accounts are lifted to floor. With a cap configured the
            # lift stops at min(floor, current x (1 + cap)), never past either.
            cap=cfg.get("max_increase_pct")
            proposed=floor if cap is None else min(floor,current*(1+float(cap)/100))
        else:
            proposed=base
        # Below list: rise toward list and stop there.
        if cap_list: proposed=min(proposed,listp)

    delta=(proposed-current)*quantity*12
    eligible=date.fromisoformat(r["next_eligible_date"]) if r.get("next_eligible_date") else None
    effective=max(eligible,notice) if eligible else notice
    realized=effective<=horizon
    # Run-rate ARR (delta) is annualized and timing-blind. Cumulative revenue
    # prorates it by the share of a year actually elapsed between the price
    # taking effect and the end of the horizon.
    realized_revenue=delta*(horizon-effective).days/DAYS_PER_YEAR if realized else 0.0
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
       "floor_binds":floor_binds,
       "arr_left_below_floor":round(arr_short_of_floor,2)}
    if held_reason=="zero_price": segment="Held (Zero Price)"
    elif held_reason=="above_list": segment="Held (Above List)"
    elif not realized: segment="Deferred"
    elif is_strategic: segment="Strategic Legacy"
    elif floor_binds: segment="Floor Normalize"
    else: segment="Standard Increase"
    o["segment"]=segment
    # Unrounded companions for aggregation. Summing the rounded per-row values
    # would drift by cents across a large book, so summary figures are built
    # from these instead.
    return o,{"arr_change":delta,"realized_revenue":realized_revenue,
              "arr_left_below_floor":arr_short_of_floor}

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
    priced=[_price_row(r,cfg,strategic,notice,horizon) for r in rows]
    out=[p[0] for p in priced]; exact=[p[1] for p in priced]

    realized=[r for r in out if r["realized_in_horizon"]]
    gaining=[i for i,r in enumerate(out) if r["realized_in_horizon"] and exact[i]["arr_change"]>0]
    expansion=sum(exact[i]["arr_change"] for i in gaining)
    affected_accounts={out[i]["account_key"] for i in gaining}
    current_affected=sum(sum(x["current_arr"] for x in by_account[k]) for k in affected_accounts)
    # Post-increase ARR of affected accounts: their whole current book plus the
    # uplift that actually lands inside the horizon.
    post_increase_affected=current_affected+expansion
    churn_base=post_increase_affected if cfg.get("churn_basis","post_increase")=="post_increase" else current_affected
    churn_loss=churn_base*float(cfg["churn_pct"])/100
    realized_revenue_total=sum(e["realized_revenue"] for e in exact)
    arr_left_below_floor=sum(e["arr_left_below_floor"] for e in exact)
    # Break-even churn: the churn rate among affected accounts at which the
    # uplift nets to zero. Measured on the same basis as the churn sensitivity
    # so the two numbers are directly comparable.
    breakeven_churn_pct=(expansion/churn_base*100) if churn_base else 0.0

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
             # Run-rate ARR uplift is annualized uplift activated by the horizon.
             # Cumulative incremental revenue is what is actually earned inside
             # it. They are different quantities; never use one as the other.
             "run_rate_arr_uplift":round(expansion,3),
             "cumulative_incremental_revenue":round(realized_revenue_total,3),
             "gross_arr_expansion":round(expansion,2),
             "pre_increase_affected_arr":round(current_affected,3),
             "post_increase_affected_arr":round(post_increase_affected,3),
             "churn_basis":cfg.get("churn_basis","post_increase"),
             "churn_sensitivity_loss":round(churn_loss,3),
             "modeled_churn_loss":round(churn_loss,2),
             "risk_adjusted_net":round(expansion-churn_loss,3),
             "avg_unit_increase_pct":round(sum(r['price_change_pct'] for r in realized)/len(realized),2) if realized else 0,
             "arr_weighted_avg_increase_pct":round(arr_weighted_avg_increase_pct,2),
             "realized_revenue_in_horizon":round(realized_revenue_total,2),
             "breakeven_churn_pct":round(breakeven_churn_pct,4),
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
