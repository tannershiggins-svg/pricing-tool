# Assumptions and limitations

- The case data does not contain historical price-change experiments, so churn is a sensitivity input, not a causal elasticity estimate. Because the sensitivity is flat, net revenue rises monotonically with the increase and the model has no interior optimum — it must be read alongside the exposure counts, break-even churn rate and increase distribution rather than on its own.
- The case data does not establish competitive willingness-to-pay, so new list prices are scenario inputs rather than empirically optimized new-logo prices.
- Contract grain is assumed to be account-level for the prototype. The source data carries one contract per account; product- or subscription-specific contracts would require remapping before deployment.
- Entity resolution stops at deterministic tiers: Salesforce ID, exact normalized name, exact billing-email domain. Anything ambiguous or beyond those tiers is left unmatched for manual review rather than fuzzy-matched, and should be upgraded with a governed matching workflow.
- Unmatched billing customers have no Salesforce account, therefore no tenure and no contract. They are priced on their billing data, treated as out-of-term, and can never qualify as strategic legacy.
- The analysis date is an explicit input. A reported benchmark should always pin it; leaving it derived means the result moves as new billing data arrives.
- This prototype never writes to Stripe or Salesforce. Exports are execution inputs requiring approval.
