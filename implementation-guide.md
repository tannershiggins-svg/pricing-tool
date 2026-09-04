# Production handoff guide

This repository is deliberately a working prototype, not a production billing system.

## Production hardening
- Replace local SQLite with managed Postgres or the company's approved warehouse/database.
- Add SSO/RBAC and restrict customer-level exports.
- Encrypt uploaded data at rest and define retention/deletion policy.
- Add immutable audit logs for scenario changes and approvals.
- Replace CSV upload with governed Salesforce/Stripe connectors after source-field definitions are approved.
- Add approval workflow before any billing write-back.
- Add contract-grain validation if contracts can apply to individual products/subscriptions.
- Add automated reconciliation tests against Finance's recognized ARR.
- Add versioned pricing policies and effective dates.

## Ownership
Recommended operating model: Finance owns pricing policy; RevOps owns data model, simulation, controls and rollout orchestration; Sales/CS own customer strategy and exception requests; Billing/Engineering own production price changes.
