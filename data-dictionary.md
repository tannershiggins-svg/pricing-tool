# Data dictionary and grain

## Accounts
One row per Salesforce account. Used for account identity, tenure, CSM/AE ownership, and account size context.

## Contracts
One row per Salesforce contract. The prototype assumes contract restrictions apply at the **account grain** and uses the contract with the latest end date for each account. If production contracts are product/subscription-specific, this mapping must be changed before deployment.

## Stripe customers
One row per billing customer. Stripe is treated as billing source of truth. Matching hierarchy: valid Salesforce metadata ID first, then exact normalized company name. Unmatched records are surfaced as validation warnings rather than silently guessed.

## Stripe subscriptions
One row per product line item. `unit_amount` is per billing period in cents. Annual lines are divided by 12 to normalize to monthly equivalent unit price. Quantity is then applied to calculate MRR and ARR.

## Active status
The prototype derives its analysis date from the maximum `last_billing_date` in the uploaded dataset. Monthly customers billed within 31 days and annual customers billed within 366 days are considered active. This is configurable code, not a universal accounting definition.
