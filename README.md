# Pricing Governance & Renewal Simulator

A working RevOps prototype for turning messy Salesforce + Stripe exports into a repeatable pricing-increase workflow: validate → normalize → simulate → phase by contract/billing timing → export execution lists.

## What it demonstrates

- **Repeatability:** the analysis is driven by upload templates and configurable policy, not one-off spreadsheet formulas.
- **Governance:** list price is the external reference, floor is the internal economic guardrail, and strategic exceptions are explicit.
- **Data quality first:** unmatched billing customers are surfaced before decisions are made.
- **Execution readiness:** outputs can be filtered/exported for Finance, Billing, CS, and Marketing workflows.
- **Handoff:** business logic, data grain, assumptions, and production-hardening steps are documented separately from the UI.

## Case-study default policy

This is a single-cycle, capped-increase pass intended to tighten the pricing distribution around list — not a multi-year mechanism.

- 5% standard annual increase.
- Existing floors remain Hiring $60, HR $80, Payroll $11.
- New reference list prices are modeled as +5%: Hiring $78.75, HR $105, Payroll $14.70.
- **Standard accounts** below floor after the standard increase are raised further to close the gap, but only up to `min(floor, current * (1 + max_increase_pct/100))` (default cap 20%) — never past the floor, never past the cap. A cap-limited account still short of floor is tracked via `arr_left_below_floor`.
- **Strategic legacy** accounts (3+ years tenure **and** high volume: Hiring 7+, HR 7+, Payroll 120+) receive the standard increase only — floor logic and the cap never apply to them, unconditionally. They can remain below floor afterward; that gap is still reported (`below_floor_after`, `arr_left_below_floor`), just not acted on.
- Accounts already priced above the configured list price are always held (not increased, not reduced) — there is no config path that raises a price above list.
- Subscriptions with a zero or negative unit price are held for review rather than normalized to floor.
- Contracted accounts wait until renewal; evergreen (null end date) contracts are treated as always current; out-of-term/no-contract accounts move on the next eligible bill date after notice.
- Distribution/discount reporting shows where pricing sits before vs. after the change: `price_distribution()` per product, the ARR-weighted share within 10% of list and below floor, and ARR-weighted vs. simple average discount from list overall and by AE.
- Governance/risk outputs include ARR-weighted average increase, realized revenue in horizon (prorated by whole months), breakeven churn %, breakeven account count, and a risk-adjusted sensitivity grid across increase/churn assumptions.

These are scenario defaults, not hard-coded conclusions. See `docs/pricing-logic.md` for the full policy writeup.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:8000`.

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
