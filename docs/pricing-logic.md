# Pricing policy logic (v2)

## Recommended installed-base policy

1. Every active subscription is evaluated for a configurable standard annual increase (default 5%).
2. **Held for review, not priced:**
   - Subscriptions with `unit_price <= 0` are held unchanged. A zero/negative unit price is a data problem, not a pricing decision, so the tool does not attempt to normalize it to floor.
   - Accounts already priced above the configured list price are held unchanged (not increased further, not reduced) unless the `increase_above_list` flag is enabled, in which case they receive the normal treatment below.
3. **Standard treatment** (everyone else): the account's price moves toward `current * (1 + increase_pct/100)`. If that result is below the product floor, the target becomes the floor instead — this is floor normalization.
4. **Capped glidepath:** regardless of what floor normalization or the strategic increase would otherwise produce, no account's proposed price may increase by more than a configured percentage in a single cycle:
   - `max_increase_pct` (default 20%) for standard accounts.
   - `max_increase_pct_strategic` (default 10%), a gentler cap, for strategic legacy accounts.
   The cap can leave an account still below the product floor after this cycle; the remaining gap is reported as `arr_left_below_floor` so it can be tracked across future cycles.
5. A **strategic legacy account** is both:
   - at or above the configured tenure threshold; and
   - high-volume in at least one product using the configured product-specific threshold.
6. By default, the floor still applies to strategic legacy accounts (subject to their gentler cap). Setting `legacy_floor_exemption: true` restores the pre-v2 behavior of exempting strategic legacy accounts from the floor entirely — they then only receive the standard increase, uncapped by any floor target.
7. Fixed-term contracts become eligible at contract end/renewal. Evergreen contracts (a real contract row with a null end date) are treated as always current and never lose to an expired dated contract when selecting the operative contract for an account. Out-of-term, evergreen, and no-contract customers become eligible on the next billing date, subject to the notice period.

## Revenue timing: run-rate vs. realized

- `arr_change` is the full annualized run-rate delta (current vs. proposed unit price × quantity × 12), independent of when in the horizon it takes effect.
- `realized_revenue_in_horizon` prorates that delta by the number of *whole months* between the change's effective date and the end of the modeling horizon. A change that lands with only 3 months left in the horizon contributes 3/12 of its annualized value to this field, not the full amount. This is the field that should be summed for an in-horizon cash/revenue view; `arr_change` should not be, since it overstates near-horizon changes.

## List price and discount representation

The new list price is the external/reference price. The tool calculates:

`discount_from_list = 1 - proposed_unit_price / list_price`

A negative value means the proposed effective price is above the configured list price and should be reviewed as a pricing-governance exception.

## Why floor is separate from list

The case brief describes list as the Sales-led guideline and floor as the target price needed to support gross-margin targets. The tool therefore treats floor as an internal economic guardrail, not the advertised price.

## Governance and risk metrics

- `gross_arr_expansion` — sum of positive `arr_change` across subscriptions realized within the horizon.
- `modeled_churn_loss` — the current ARR of accounts receiving an increase, multiplied by the configured `churn_pct` sensitivity input.
- `risk_adjusted_net` — `gross_arr_expansion - modeled_churn_loss`.
- `breakeven_churn_pct` — `gross_arr_expansion / ARR exposed to churn`, i.e. the actual churn rate among affected accounts at which the increase cycle would net to zero. Compare this against the configured `churn_pct` sensitivity input as a margin-of-safety check.
- `breakeven_accounts` — the minimum number of affected accounts (starting from the largest by current ARR) whose combined current ARR would need to churn to offset the gross expansion.
- `arr_left_below_floor` — total ARR still short of the product floor after the glidepath cap is applied, summed across all subscriptions. This is the backlog of floor-normalization work that a single cycle's cap could not complete.
- `arr_weighted_avg_increase_pct` — the average price increase percentage, weighted by each subscription's current ARR, alongside the simple (unweighted) `avg_unit_increase_pct`. The weighted figure is what actually drives revenue; the unweighted figure can be skewed by many small accounts.

## Sensitivity grid

`sensitivity_grid()` runs the simulation across a grid of standard increase % and churn % assumptions and returns the resulting `gross_arr_expansion`, `modeled_churn_loss`, and `risk_adjusted_net` for each combination, so a scenario's risk-adjusted outcome can be stress-tested against a range of plausible churn responses rather than a single point estimate.
