# Assumptions and limitations

- The case data does not contain historical price-change experiments, so churn is a sensitivity input, not a causal elasticity estimate.
- The case data does not establish competitive willingness-to-pay, so new list prices are scenario inputs rather than empirically optimized new-logo prices.
- Contract grain is assumed to be account-level for the prototype.
- Exact/fuzzy entity resolution beyond normalized exact names should be reviewed manually or upgraded with a governed matching workflow.
- This prototype never writes to Stripe or Salesforce. Exports are execution inputs requiring approval.
