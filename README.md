# Pricing Governance & Renewal Simulator

A working RevOps prototype for turning messy Salesforce + Stripe exports into a repeatable pricing-increase workflow: validate → normalize → simulate → phase by contract/billing timing → export execution lists.

## What it demonstrates

- **Repeatability:** the analysis is driven by upload templates and configurable policy, not one-off spreadsheet formulas.
- **Governance:** list price is the external reference, floor is the internal economic guardrail, and strategic exceptions are explicit.
- **Data quality first:** unmatched billing customers are surfaced before decisions are made.
- **Execution readiness:** outputs can be filtered/exported for Finance, Billing, CS, and Marketing workflows.
- **Handoff:** business logic, data grain, assumptions, and production-hardening steps are documented separately from the UI.

## Case-study default policy

- 5% standard annual increase.
- Existing floors remain Hiring $60, HR $80, Payroll $11.
- New reference list prices are modeled as +5%: Hiring $78.75, HR $105, Payroll $14.70.
- Standard accounts are normalized to at least floor.
- Strategic legacy = 3+ years tenure **and** high volume (Hiring 7+, HR 7+, Payroll 120+). They still receive 5% but may remain below floor.
- Contracted accounts wait until renewal; out-of-term/no-contract accounts move on the next eligible bill date after notice.

These are scenario defaults, not hard-coded conclusions.

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

## GitHub / deployment

This folder is ready for `git init`. For a public repository, use synthetic/anonymized sample data only; do not commit customer exports. For production deployment, see `docs/implementation-guide.md`.
