# Pricing Governance & Renewal Simulator

A working RevOps prototype for turning messy Salesforce + Stripe exports into a repeatable pricing-increase workflow: validate → normalize → simulate → phase by contract/billing timing → export execution lists.

## What it demonstrates

- **Repeatability:** the analysis is driven by upload templates and configurable policy, not one-off spreadsheet formulas.
- **Governance:** list price is the external reference, floor is the internal economic guardrail, and strategic exceptions are explicit.
- **Data quality first:** unmatched billing customers are surfaced before decisions are made.
- **Execution readiness:** outputs can be filtered/exported for Finance, Billing, CS, and Marketing workflows.
- **Handoff:** business logic, data grain, assumptions, and production-hardening steps are documented separately from the UI.

## Case-study default policy (v2: capped glidepath)

- 5% standard annual increase.
- Existing floors remain Hiring $60, HR $80, Payroll $11.
- New reference list prices are modeled as +5%: Hiring $78.75, HR $105, Payroll $14.70.
- Standard accounts are normalized to at least floor, but **no account's price may increase by more than a capped percentage in one cycle** — 20% for standard accounts, 10% for strategic legacy accounts — even when floor normalization would otherwise push it higher. An account the cap leaves short of floor is tracked via `arr_left_below_floor` for the next cycle.
- Strategic legacy = 3+ years tenure **and** high volume (Hiring 7+, HR 7+, Payroll 120+). By default the floor still applies to them (subject to their gentler cap); the legacy full-exemption behavior is available via a `legacy_floor_exemption` flag for scenarios that need it.
- Accounts already priced above the configured list price are held (not increased, not reduced) unless `increase_above_list` is enabled.
- Subscriptions with a zero or negative unit price are held for review rather than normalized to floor.
- Contracted accounts wait until renewal; evergreen (null end date) contracts are treated as always current; out-of-term/no-contract accounts move on the next eligible bill date after notice.
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
