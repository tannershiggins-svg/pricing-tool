# Pricing policy logic

## What this is

This is a **variance-reduction pass**, not a multi-cycle mechanism. Each run of the simulator answers: "if we applied this one increase cycle today, how much would it tighten the pricing distribution around list, and what's left over?" There is no path-dependence across cycles baked into the tool — running it again next year with a fresh snapshot is a fresh decision, not a continuation of a plan.

## Recommended installed-base policy

1. Every active subscription is evaluated for a configurable standard annual increase (default 5%): `current_price * (1 + increase_pct/100)`.
2. **Held for review, not priced:**
   - Subscriptions with `unit_price <= 0` are held unchanged. A zero/negative unit price is a data problem, not a pricing decision.
   - Accounts already priced **at or above** the configured list price are held unchanged (not increased further, not reduced), unconditionally. There is no config flag that re-enables raising such a price — that is a deliberate invariant, not a default.
3. **Standard accounts** (everyone else): apply the standard increase. If the result is still below the product floor, raise the price further to close the gap — but only up to `min(floor, current_price * (1 + max_increase_pct/100))` (default cap 20%). The account is never pushed past the floor by this rule, and never past the cap. If the cap binds before reaching floor, the shortfall is reported per-row (`arr_left_below_floor`) and in the summary (`arr_left_below_floor`, `pct_below_floor_after`) so it's visible, not hidden.
4. **List is a ceiling on every priced line.** A below-list line can be raised up to list but never through it — if the standard increase (or the floor uplift, under a floor configured above list) would overshoot, the result is clamped to list. Combined with the hold rule above, this means the policy never creates a *new* above-list price: any line priced above list after a run was already there before it. The clamp applies to strategic legacy lines too — their exemption is from the floor and the cap, not from the list ceiling.
5. **Strategic legacy accounts** receive the standard increase only — full stop. Floor logic and the cap do not apply to them at all, unconditionally (this is a structural exemption in how strategic accounts are priced, not a toggle to switch on or off). A strategic account can remain deeply below floor after the change; the tool still computes `below_floor_after` and `arr_left_below_floor` for these rows so they show up correctly in reporting, even though no action was taken to close the gap.
6. A **strategic legacy account** is both:
   - at or above the configured tenure threshold (a missing/unknown tenure resolves to 0 and therefore fails this test — it never qualifies by default); and
   - high-volume in at least one product (`quantity >= threshold`, evaluated with OR across the account's products, not AND) using the configured product-specific threshold.
7. Fixed-term contracts become eligible at contract end/renewal. Evergreen contracts (a real contract row with a null end date) are treated as always current and never lose to an expired dated contract when selecting the operative contract for an account. Out-of-term, evergreen, and no-contract customers become eligible on the next billing date, subject to the notice period.

## Revenue timing: run-rate vs. realized

- `arr_change` is the full annualized run-rate delta (current vs. proposed unit price × quantity × 12), independent of when in the horizon it takes effect.
- `realized_revenue_in_horizon` prorates that delta by the number of *whole months* between the change's effective date and the end of the modeling horizon. A change that lands with only 3 months left in the horizon contributes 3/12 of its annualized value to this field, not the full amount. This is the field that should be summed for an in-horizon cash/revenue view; `arr_change` should not be, since it overstates near-horizon changes.

## List price and discount representation

The new list price is the external/reference price. The tool calculates, per subscription, both before and after the proposed change:

`discount_from_list = 1 - price / list_price`

A negative value means the price is above the configured list price and should be reviewed as a pricing-governance exception. (Per the hold rule above, `discount_from_list` after the change can only be negative for a row that was already above list before the change — the policy itself never creates a new one.)

## Why floor is separate from list

The case brief describes list as the Sales-led guideline and floor as the target price needed to support gross-margin targets. The tool therefore treats floor as an internal economic guardrail, not the advertised price.

## Pricing distribution and discount reporting

Because this is a variance-reduction pass, the tool reports where the book of business sits relative to list and floor, both before and after the proposed change, rather than only the increase mechanics:

- `price_distribution()` returns, per product, the sorted list of unit prices before and after the change alongside that product's floor and list price — meant to be plotted as two distributions with floor/list as vertical reference lines, so you can see the distribution visibly tighten around list.
- `pct_within_10pct_of_list_before` / `_after` — the ARR-weighted share of subscriptions priced within 10% of that product's list price.
- `pct_below_floor_before` / `_after` — the ARR-weighted share of subscriptions priced below that product's floor.
- `discount_from_list_avg_pct_before` / `_after` — the overall ARR-weighted average discount from list.
- `discount_by_ae` — per AE, both the ARR-weighted and the simple (unweighted) average discount from list, before and after, plus an account count. The weighted and simple figures are reported side by side deliberately: an AE whose discounting is concentrated in one large account will show a weighted average dragged toward that account's discount while the simple average stays close to their typical deal — the gap between the two numbers is itself the signal of inconsistent discounting.

These distribution/discount metrics are computed across the full modeled book (not gated by `realized_in_horizon`), since the question they answer is "where would pricing sit if this cycle were fully applied," not "what lands within the horizon."

## Governance and risk metrics

- `gross_arr_expansion` — sum of positive `arr_change` across subscriptions realized within the horizon.
- `modeled_churn_loss` — the current ARR of accounts receiving an increase, multiplied by the configured `churn_pct` sensitivity input.
- `risk_adjusted_net` — `gross_arr_expansion - modeled_churn_loss`.
- `breakeven_churn_pct` — `gross_arr_expansion / ARR exposed to churn`, i.e. the actual churn rate among affected accounts at which the increase cycle would net to zero. Compare this against the configured `churn_pct` sensitivity input as a margin-of-safety check.
- `breakeven_accounts` — the minimum number of affected accounts (starting from the largest by current ARR) whose combined current ARR would need to churn to offset the gross expansion.
- `arr_left_below_floor` — total ARR still short of the product floor after this cycle (including strategic accounts, which are exempt by design, and standard accounts the cap left short), summed across all subscriptions.
- `arr_weighted_avg_increase_pct` — the average price increase percentage, weighted by each subscription's current ARR, alongside the simple (unweighted) `avg_unit_increase_pct`. The weighted figure is what actually drives revenue; the unweighted figure can be skewed by many small accounts.

## Sensitivity grid

`sensitivity_grid()` runs the simulation across a grid of standard increase % and churn % assumptions and returns the resulting `gross_arr_expansion`, `modeled_churn_loss`, and `risk_adjusted_net` for each combination, so a scenario's risk-adjusted outcome can be stress-tested against a range of plausible churn responses rather than a single point estimate.
