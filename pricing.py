from datetime import date
from calendar import monthrange

DEFAULT_CONFIG={
 "increase_pct":5.0,"notice_months":2,"churn_pct":3.0,"tenure_years":3.0,
 "lists":{"Hiring":78.75,"HR":105.0,"Payroll":14.70},
 "floors":{"Hiring":60.0,"HR":80.0,"Payroll":11.0},
 "high_volume":{"Hiring":7,"HR":7,"Payroll":120},
 "enforce_floor_for_strategic":False
}

def add_months(d, months):
    m=d.month-1+months; y=d.year+m//12; m=m%12+1
    return date(y,m,min(d.day,monthrange(y,m)[1]))

def simulate(rows, config=None, analysis_date=None, horizon_months=12):
    cfg={**DEFAULT_CONFIG, **(config or {})}
    for k in ["lists","floors","high_volume"]: cfg[k]={**DEFAULT_CONFIG[k], **((config or {}).get(k,{}) if config else {})}
    ad=date.fromisoformat(analysis_date); horizon=add_months(ad,int(horizon_months)); notice=add_months(ad,int(cfg["notice_months"]))
    by_account={}
    for r in rows: by_account.setdefault(r["account_key"],[]).append(r)
    strategic={}
    for k,rs in by_account.items():
        tenure=max(x["tenure_years"] for x in rs)
        high=any(x["quantity"]>=float(cfg["high_volume"].get(x["product"],10**9)) for x in rs)
        strategic[k]=tenure>=float(cfg["tenure_years"]) and high
    out=[]
    for r in rows:
        current=float(r["unit_price"]); product=r["product"]; floor=float(cfg["floors"].get(product,0)); listp=float(cfg["lists"].get(product,0)); base=current*(1+float(cfg["increase_pct"])/100)
        is_strategic=strategic[r["account_key"]]
        proposed=base if (is_strategic and not cfg.get("enforce_floor_for_strategic",False)) else max(base,floor)
        # This price-increase policy never creates decreases. Above-list legacy prices are flagged, not reduced automatically.
        delta=(proposed-current)*float(r["quantity"])*12
        eligible=date.fromisoformat(r["next_eligible_date"]) if r.get("next_eligible_date") else None
        effective=max(eligible,notice) if eligible else notice
        realized=effective<=horizon
        discount=(1-proposed/listp) if listp else 0
        o={**r,"strategic_legacy":is_strategic,"list_price":listp,"floor_price":floor,"proposed_unit_price":round(proposed,4),"price_change_pct":round((proposed/current-1)*100,3) if current else 0,"discount_from_list_pct":round(discount*100,3),"arr_change":round(delta,2),"effective_date":effective.isoformat(),"realized_in_horizon":realized,"below_floor_before":current<floor,"above_list_after":proposed>listp}
        o["segment"]="Deferred" if not realized else ("Strategic Legacy" if is_strategic else ("Floor Normalize" if current<floor else "Standard Increase"))
        out.append(o)
    realized=[r for r in out if r["realized_in_horizon"]]
    expansion=sum(r["arr_change"] for r in realized if r["arr_change"]>0)
    affected_accounts={r["account_key"] for r in realized if r["arr_change"]>0}
    current_affected=sum(sum(x["current_arr"] for x in by_account[k]) for k in affected_accounts)
    churn_loss=current_affected*float(cfg["churn_pct"])/100
    summary={"accounts":len(by_account),"subscriptions":len(out),"accounts_affected":len(affected_accounts),"strategic_accounts":sum(strategic.values()),"floor_normalization_accounts":len({r['account_key'] for r in realized if r['segment']=='Floor Normalize'}),"deferred_accounts":len({r['account_key'] for r in out if not r['realized_in_horizon']}),"gross_arr_expansion":round(expansion,2),"modeled_churn_loss":round(churn_loss,2),"risk_adjusted_net":round(expansion-churn_loss,2),"avg_unit_increase_pct":round(sum(r['price_change_pct'] for r in realized)/len(realized),2) if realized else 0,"above_list_after_count":sum(1 for r in out if r['above_list_after']),"horizon_end":horizon.isoformat()}
    return {"config":cfg,"summary":summary,"rows":out}
