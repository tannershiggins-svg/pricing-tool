import re
import pandas as pd

REQUIRED = {
 "accounts": ["account_id","account_name","customer_arr__c","number_of_locations__c","csm_name__c","account_ae","created_date"],
 "contracts": ["contract_id","account_id","start_date","end_date","contract_term_months"],
 "customers": ["stripe_customer_id","name","email","last_billing_date","metadata_salesforce_id","created"],
 "subscriptions": ["subscription_item_id","stripe_customer_id","price_nickname","unit_amount","billing_interval","quantity"],
}

VALID_BILLING_INTERVALS = {"month","year"}
VALID_PRODUCTS = {"Hiring","HR","Payroll"}

# One billing interval back from the analysis date is the activity window: a
# monthly line must have billed within the last calendar month, an annual line
# within the last calendar year. Calendar offsets, not 31/366-day subtraction,
# so the boundary lands on the same day-of-month as the analysis date.
ACTIVITY_WINDOW = {"month": pd.DateOffset(months=1), "year": pd.DateOffset(years=1)}

def norm_name(v):
    """Normalize a company name for exact matching: '&' and 'and' are treated
    as the same token, then all punctuation, spacing and case are dropped."""
    s = str(v or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]", "", s)

def email_domain_key(email):
    """Normalized billing-email domain, minus its public suffix.

    'billing@perezinc.com' -> 'perezinc', which matches norm_name('Perez Inc').
    The local part is ignored; only the domain identifies the company.
    """
    s = str(email or "").strip().lower()
    if "@" not in s: return ""
    domain = s.rsplit("@", 1)[1]
    return norm_name(domain.split(".")[0])

def read_csv(file, kind):
    df = pd.read_csv(file)
    missing = [c for c in REQUIRED[kind] if c not in df.columns]
    if missing: raise ValueError(f"{kind}: missing required columns: {', '.join(missing)}")
    return df

def _drop_duplicates(df, key, label, issues):
    dup_mask = df[key].duplicated(keep="first")
    dup_count = int(dup_mask.sum())
    if dup_count:
        issues.append({"severity":"warning","code":f"DUPLICATE_{label.upper()}","count":dup_count,
                        "message":f"Dropped {dup_count} duplicate {label} row(s) (kept first occurrence of each {key})."})
        df = df[~dup_mask].copy()
    return df

def build_rows(accounts, contracts, customers, subscriptions, analysis_date=None):
    """Normalize the four source exports into one priced-line book.

    `analysis_date` anchors activity, tenure and contract status. Pass it
    explicitly (ISO date or datetime) to pin a reproducible benchmark; when
    omitted it falls back to the latest `last_billing_date` in the customer
    file, which moves as new data arrives.
    """
    issues=[]

    accounts=_drop_duplicates(accounts,"account_id","accounts",issues)
    customers=_drop_duplicates(customers,"stripe_customer_id","customers",issues)
    subscriptions=_drop_duplicates(subscriptions,"subscription_item_id","subscriptions",issues)

    bad_interval=~subscriptions["billing_interval"].astype(str).str.lower().isin(VALID_BILLING_INTERVALS)
    bad_product=~subscriptions["price_nickname"].isin(VALID_PRODUCTS)
    bad=bad_interval|bad_product
    if bad.any():
        issues.append({"severity":"warning","code":"INVALID_SUBSCRIPTION_ROWS","count":int(bad.sum()),
                        "message":"Dropped subscriptions with an unrecognized billing_interval (must be month/year) or price_nickname (must be Hiring/HR/Payroll)."})
        subscriptions=subscriptions[~bad].copy()

    known_customers=set(customers["stripe_customer_id"].dropna().astype(str))
    orphaned=int((~subscriptions["stripe_customer_id"].astype(str).isin(known_customers)).sum())
    if orphaned:
        issues.append({"severity":"warning","code":"ORPHANED_SUBSCRIPTIONS","count":orphaned,
                        "message":"Subscriptions reference a Stripe customer not present in the customers file."})

    for df, cols in [(accounts,["created_date"]),(contracts,["start_date","end_date"]),(customers,["last_billing_date","created"])]:
        for c in cols:
            if c in df: df[c]=pd.to_datetime(df[c], errors="coerce")
    if analysis_date is not None:
        analysis_date=pd.Timestamp(analysis_date).normalize()
    else:
        latest_bill=customers["last_billing_date"].max()
        if pd.isna(latest_bill): raise ValueError("No valid last_billing_date values found")
        analysis_date=latest_bill.normalize()

    # Deterministic match hierarchy, most to least authoritative:
    #   1. a metadata_salesforce_id that resolves to a real account
    #   2. exact normalized company name, when it maps to exactly one account
    #   3. exact normalized billing-email domain, when it maps to exactly one account
    #   4. otherwise unmatched -- never fuzzy-matched, never guessed
    # Ambiguous candidates (a normalized key shared by two accounts) are left
    # unmatched at that tier rather than resolved arbitrarily.
    acct=accounts.copy(); acct["_name"]=acct["account_name"].map(norm_name)
    name_counts=acct.groupby("_name").size().to_dict()
    unique_name={row["_name"]:row["account_id"] for _,row in acct.iterrows() if name_counts.get(row["_name"])==1}
    valid_ids=set(acct["account_id"].dropna().astype(str))
    cust=customers.copy(); cust["sf_id"]=None
    cust["match_method"]="unmatched"
    stale=0
    for i,r in cust.iterrows():
        raw=r["metadata_salesforce_id"]
        sid=str(raw) if pd.notna(raw) and str(raw).strip() else None
        if sid and sid in valid_ids:
            cust.at[i,"sf_id"]=sid; cust.at[i,"match_method"]="salesforce_id"
            continue
        if sid: stale+=1  # supplied but not a real account: report it, then keep resolving
        candidate=unique_name.get(norm_name(r["name"]))
        if candidate:
            cust.at[i,"sf_id"]=candidate; cust.at[i,"match_method"]="exact_name"
            continue
        candidate=unique_name.get(email_domain_key(r["email"]))
        if candidate:
            cust.at[i,"sf_id"]=candidate; cust.at[i,"match_method"]="email_domain"
    if stale: issues.append({"severity":"warning","code":"STALE_SALESFORCE_ID","count":stale,
                              "message":"Stripe metadata_salesforce_id did not match any Salesforce account; resolved by name/email domain instead where possible."})
    unmatched=int((cust["match_method"]=="unmatched").sum())
    if unmatched: issues.append({"severity":"warning","code":"UNMATCHED_CUSTOMERS","count":unmatched,"message":"Stripe customers could not be matched by Salesforce ID, exact normalized name, or billing-email domain."})

    # One account-level contract record. A null end date is evergreen and wins over any dated
    # contract (including an expired one) for that account.
    csort=contracts.sort_values("end_date", na_position="last")
    latest_contract=csort.groupby("account_id", as_index=False).tail(1)
    cust=cust.merge(acct.drop(columns=["_name"]), left_on="sf_id", right_on="account_id", how="left")
    cust=cust.merge(latest_contract[["account_id","contract_id","start_date","end_date","contract_term_months"]], on="account_id", how="left")
    merged=subscriptions.merge(cust, on="stripe_customer_id", how="left", suffixes=("","_customer"))

    rows=[]
    for r in merged.to_dict("records"):
        interval=str(r.get("billing_interval") or "").lower(); last=r.get("last_billing_date")
        # A line with no billing date at all has churned out of the population;
        # it is dropped, not carried as a $0 row that would dilute every average.
        active = pd.notna(last) and last >= analysis_date - ACTIVITY_WINDOW[interval]
        if not active: continue
        end=r.get("end_date"); contract_id=r.get("contract_id")
        if pd.isna(contract_id): contract_status="No Contract"
        elif pd.isna(end): contract_status="Evergreen"
        elif end.normalize()>=analysis_date: contract_status="In-Term"
        else: contract_status="Out-of-Term"
        unit=float(r.get("unit_amount") or 0)/100.0/(12 if interval=="year" else 1)
        qty=float(r.get("quantity") or 0); mrr=unit*qty; arr=mrr*12
        created=r.get("created_date")
        tenure=max(0,(analysis_date-created).days/365.25) if pd.notna(created) else 0
        if contract_status=="In-Term": next_eligible=end.normalize()
        else:
            next_eligible=last.normalize() + (pd.DateOffset(months=1) if interval=="month" else pd.DateOffset(years=1)) if pd.notna(last) else pd.NaT
        account_key=str(r.get("account_id")) if pd.notna(r.get("account_id")) else f"stripe:{r.get('stripe_customer_id')}"
        rows.append({
          "subscription_item_id":r.get("subscription_item_id"),"stripe_customer_id":r.get("stripe_customer_id"),"salesforce_id":None if pd.isna(r.get("account_id")) else r.get("account_id"),
          "account_key":account_key,"account_name":r.get("account_name") if pd.notna(r.get("account_name")) else r.get("name"),"email":r.get("email"),"csm":None if pd.isna(r.get("csm_name__c")) else r.get("csm_name__c"),"ae":None if pd.isna(r.get("account_ae")) else r.get("account_ae"),
          "product":r.get("price_nickname"),"quantity":qty,"billing_interval":interval,"unit_price":round(unit,4),"current_mrr":round(mrr,2),"current_arr":round(arr,2),
          "tenure_years":round(tenure,6),"last_billing_date":last.date().isoformat() if pd.notna(last) else None,"contract_status":contract_status,"contract_end_date":end.date().isoformat() if pd.notna(end) else None,"next_eligible_date":next_eligible.date().isoformat() if pd.notna(next_eligible) else None,"match_method":r.get("match_method")
        })
    validation={"analysis_date":analysis_date.date().isoformat(),"issues":issues,"unmatched_customers":unmatched,"active_subscriptions":len(rows),"active_accounts":len({r['account_key'] for r in rows})}
    return rows, validation
