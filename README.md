# Pricing Governance & Renewal Simulator

A working RevOps prototype for turning messy Salesforce + Stripe exports into a repeatable pricing-increase workflow: validate → normalize → simulate → phase by contract/billing timing → export execution lists.

## What it demonstrates

- **Repeatability:** the analysis is driven by upload templates and configurable policy, not one-off spreadsheet formulas.
- **Governance:** list price is the external reference, floor is the internal economic guardrail, and strategic exceptions are explicit.
- **Data quality first:** unmatched billing customers are surfaced before decisions are made.
- **Execution readiness:** outputs can be filtered/exported for Finance, Billing, CS, and Marketing workflows.
- **Handoff:** business logic, data grain, assumptions, and production-hardening steps are documented separately from the UI.

## Case-study default policy

A single-cycle installed-base increase, anchored to an explicit analysis date.

- 5% standard annual increase, 2 months notice.
- Existing floors remain Hiring $60, HR $80, Payroll $11.
- New reference list prices are modeled as +5%: Hiring $78.75, HR $105, Payroll $14.70.
- **Standard accounts:** `max(current × 1.05, floor)` — the standard increase, lifted to floor if it lands short.
- **Strategic legacy accounts:** `current × 1.05` only. Floor logic does not apply, so they may remain below floor; the gap is reported, not closed. Qualifying requires 3+ years tenure **and** high volume on at least one product (Hiring 7+, HR 7+, Payroll 120+). Both tests are account-level; a missing tenure fails the tenure half.
- Subscriptions with a zero or negative unit price are held for review.
- The policy never produces a decrease.
- Contracted accounts wait until renewal; evergreen (null end date) contracts count as current; out-of-term/no-contract accounts move on the next eligible bill date, subject to the notice floor.
- Three **governance guardrails** — a cap on the floor uplift, holding at-or-above-list lines, and clamping increases at list — are available as scenario inputs and are **off by default**.
- Billing customers resolve to Salesforce accounts by Salesforce ID, then exact normalized name, then exact billing-email domain, then unmatched. Ambiguous candidates are never guessed.
- Outputs separate **run-rate ARR uplift** (annualized uplift activated by the horizon) from **cumulative incremental revenue** (prorated by days actually elapsed). These are not interchangeable.
- Churn is a **sensitivity assumption, not a forecast**, applied by default to the post-increase ARR of affected accounts — the conservative basis — and reported alongside a break-even churn rate.

These are scenario defaults, not hard-coded conclusions. See `docs/pricing-logic.md` for the full policy writeup, including the match hierarchy, contract-grain assumption, and the known limitation of a flat churn sensitivity.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8000`.

## Reproducing a benchmark

Pin the analysis date so a run is reproducible rather than drifting with the
data. Via the API, pass `analysis_date` with the upload:

```bash
curl -X POST http://127.0.0.1:8000/api/upload \
  -F "name=Case Study Benchmark" -F "analysis_date=2026-06-15" \
  -F "accounts=@sfdc_accounts.csv"      -F "contracts=@sfdc_contracts.csv" \
  -F "customers=@stripe_customers.csv"  -F "subscriptions=@stripe_subscriptions.csv"
```

Omit it and the analysis date falls back to the latest `last_billing_date` in
the customer file.

**Never commit customer exports.** `case-data/` and `*.xlsx` are git-ignored for
exactly this reason; `sample-data/` and `templates/` hold synthetic data only.

## Input files

Use the blank files in `templates/`:

- `accounts.csv`
- `contracts.csv`
- `customers.csv`
- `subscriptions.csv`

The uploader validates required columns. See `docs/data-dictionary.md` for grain and transformations.

## Database

The app creates `data/pricing_governance.db` automatically. It stores normalized snapshots and saved scenario configurations/results. Raw uploads are not persisted by this prototype.

## Tests

```bash
pytest
```

## Deployment

The app runs behind [gunicorn](https://gunicorn.org/) rather than the Flask dev server:

```bash
gunicorn --bind 0.0.0.0:${PORT:-8000} run:app
```

The `Dockerfile` runs this same command; it also honors `PORT` if the platform injects one.

Two environment variables control production behavior:

- `APP_PASSWORD` — when set, gates every route behind HTTP Basic Auth (any username, this password). `/healthz` is always exempt so platform health checks keep working. Leave unset for local/demo use.
- `DB_PATH` — overrides where the SQLite database file is created (default: `data/pricing_governance.db` under the repo root). Point this at a mounted persistent disk in production so snapshots and scenarios survive container restarts/redeploys.

For further production hardening (managed database, SSO/RBAC, audit logging, governed connectors, etc.), see `docs/implementation-guide.md`.

## GitHub / deployment

This folder is ready for `git init`. For a public repository, use synthetic/anonymized sample data only; do not commit customer exports.
