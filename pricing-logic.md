# Pricing policy logic

## Recommended installed-base policy

1. Every active subscription receives a configurable standard annual increase (default 5%).
2. For standard accounts, the resulting unit price cannot remain below the existing product floor.
3. A **strategic legacy account** is both:
   - at or above the configured tenure threshold; and
   - high-volume in at least one product using the configured product-specific threshold.
4. Strategic legacy accounts still receive the standard increase, but may remain below floor. This is the legacy exception.
5. Fixed-term contracts become eligible at contract end/renewal. Out-of-term and no-contract customers become eligible on the next billing date, subject to the notice period.
6. The simulator does **not** automatically reduce above-list legacy pricing during a price-increase cycle. It flags it for review instead. This prevents a price-increase initiative from manufacturing contraction.

## List price and discount representation

The new list price is the external/reference price. The tool calculates:

`discount_from_list = 1 - proposed_unit_price / list_price`

A negative value means the proposed effective price is above the configured list price and should be reviewed as a pricing-governance exception.

## Why floor is separate from list

The case brief describes list as the Sales-led guideline and floor as the target price needed to support gross-margin targets. The tool therefore treats floor as an internal economic guardrail, not the advertised price.
