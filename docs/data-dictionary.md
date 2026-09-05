# Data dictionary and grain

## Accounts
One row per Salesforce account. Used for account identity, tenure, CSM/AE ownership, and account size context.

## Contracts
One row per Salesforce contract. The prototype assumes contract restrictions apply at the **account grain** and uses the contract with the latest end date for each account. If production contracts are product/subscription-specific, this mapping must be changed before deployment.

## Stripe customers
One row per billing customer. Stripe is treated as billing source of truth. Matching hierarchy: valid Salesforce metadata ID, then exact normalized company name, then exact normalized billing-email domain, then unmatched. A key that maps to more than one account is ambiguous and resolves at no tier. Unmatched records are surfaced as validation warnings rather than silently guessed, and `match_method` is preserved per row for audit.

## Stripe subscriptions
One row per product line item. `unit_amount` is per billing period in cents. Annual lines are divided by 12 to normalize to monthly equivalent unit price. Quantity comes from Stripe — not Salesforce `number_of_locations__c`, which disagrees on a meaningful share of lines — and is then applied to calculate MRR and ARR.

## Active status
The analysis date is an explicit input; when omitted it falls back to the maximum `last_billing_date` in the uploaded dataset. Activity is measured in calendar offsets from it: monthly lines are active when billed on or after `analysis_date - 1 month`, annual lines on or after `analysis_date - 1 year`. Lines with no billing date have churned out of the population and are dropped, never carried as $0 rows. This is configurable code, not a universal accounting definition.

## Value thresholds
Any ARR-based threshold or reporting figure uses ARR derived from Stripe billing data, not Salesforce `customer_arr__c`, which disagrees with Stripe on a substantial minority of accounts.
