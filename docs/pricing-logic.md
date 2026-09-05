# Pricing policy logic

How the tool decides a new price for every subscription line, and what each
number in the output means.

## Matching customers to accounts

Stripe is the source of truth for billing; Salesforce supplies tenure, contract
terms and AE/CSM ownership. Billing customers are matched to Salesforce accounts
in this order:

1. **Salesforce ID** on the Stripe record, if it points at a real account.
2. **Company name**, ignoring case, punctuation and spacing (and treating `&`
   and `and` as the same word) — only if exactly one account matches.
3. **Billing email domain**, normalized the same way. `billing@perezinc.com`
   matches `Perez Inc` — only if exactly one account matches.
4. **Unmatched.**

If a name or domain could point at two accounts, it matches nothing rather than
guessing. Nothing is fuzzy-matched. Every row records which rule matched it.

Unmatched customers still get priced on their billing data. They have no
Salesforce account, so they have no tenure and no contract — which means they
can never qualify as strategic legacy, and they're treated as out-of-term.

## Which lines count

The **analysis date** anchors everything. Pass it explicitly for a reproducible
run; otherwise it defaults to the latest billing date in the customer file.

A line is active if it billed recently enough:

- **Monthly:** billed on or after (analysis date − 1 month).
- **Annual:** billed on or after (analysis date − 1 year).
- **No billing date:** churned, dropped entirely.

Dropped lines are removed, never kept as $0 rows — carrying them would quietly
drag down every average and percentage.

Prices arrive in cents, so unit price is `unit_amount / 100`. Annual lines are
also divided by 12 so they're comparable to monthly ones. Quantity comes from
Stripe.

## When the new price takes effect

Contracts are handled **at the account level** — the data has one contract per
account. If contracts ever apply per product, this needs revisiting.

- No contract → eligible at the next bill.
- Contract ended before the analysis date → out-of-term, eligible at the next bill.
- Contract still running → locked until it ends.

"Next bill" means last billing date plus one month (monthly) or one year
(annual). The change then takes effect at whichever is later: that next-bill
date, or the analysis date plus the notice period.

Anything that can't take effect inside the reporting horizon is reported as
deferred, never as realized.

## How the new price is set

Per line, because floor and list are product-specific:

- **Standard accounts:** `max(current × (1 + increase), floor)` — apply the
  standard increase, and if that still lands below the floor, lift to the floor.
- **Strategic legacy accounts:** `current × (1 + increase)` only. No floor lift,
  so they can stay below floor. The gap is reported, not closed.
- **Zero or negative price:** held for review. That's a data problem, not a
  pricing decision.
- The policy never lowers a price.

An account is **strategic legacy** if it clears *both* tests:

- tenure at or above the cutoff, **and**
- high volume on **at least one** product (any one, not all).

Both are account-level: decided once per account, applied to all its lines. An
account with no tenure on file fails the first test, so it never qualifies by
accident.

The floor is only said to **bind** when the standard increase alone would have
left the line short of it. A line that starts below floor but clears it on the
standard increase is just a standard increase.

### Optional guardrails

Two extra controls, both **off by default**:

| Setting | What it does |
|---|---|
| `max_increase_pct` | Caps how far the floor lift can go in one cycle. Bounds the floor lift only — not the standard increase. |
| `cap_at_list` | List becomes a ceiling. A price below list rises up to list and stops; a price already at or above list holds where it is. |

The list cap covers both halves of the "don't price through list" rule in one
switch. It only ever reaches *down* toward list from above, never below a
customer's current price — so an above-list account is held rather than cut,
and the cap can never produce a decrease.

Leave both off and you get the baseline policy above.

## The two revenue numbers

These are different and must not be swapped:

- **Run-rate ARR uplift** — the annualized value of every increase that takes
  effect inside the horizon. A change landing in month 11 still counts its full
  annual value.
- **Cumulative incremental revenue** — what you actually collect inside the
  horizon, prorated by days. That same month-11 change contributes about one
  twelfth.

Run-rate ARR is not revenue earned. Reporting one as the other badly overstates
near-term cash.

## Churn sensitivity

There's no price-change experiment in the data, so churn is a **what-if, not a
forecast**. It answers "what would this cost if X% of affected accounts left".

By default it's applied to the **post-increase ARR** of affected accounts — a
customer who leaves takes their whole bill, not just the increment — which is
the conservative choice. Set `churn_basis` to `pre_increase` for the other basis.

`breakeven_churn_pct` is the churn rate at which the uplift nets to zero, on the
same basis, so it compares directly to your assumption.

**Known limitation:** because the sensitivity is flat, a bigger increase always
looks better — the model has no built-in optimum and will always favor raising
prices more. So it's reported alongside the things that *do* carry that signal:
how many customers are affected, the break-even rate, the spread of
customer-level increases, and where prices sit relative to floor and list before
and after.

## Distribution and discount reporting

- `price_distribution()` — prices per product before and after, with floor and
  list as reference lines.
- `pct_within_10pct_of_list_before` / `_after` — share of ARR priced near list.
- `pct_below_floor_before` / `_after` — share of ARR under the floor.
- `discount_from_list_avg_pct_before` / `_after` — average discount off list,
  ARR-weighted.
- `discount_by_ae` — per AE, the ARR-weighted *and* plain average discount, plus
  an account count. Shown side by side on purpose: when one big discounted
  account drags the weighted number well below the plain one, that gap is the
  signal.

These cover the whole book rather than just what lands in the horizon, since
they answer "where would pricing sit if this cycle were fully applied".

## Counting rules

Any count of customers or accounts is a distinct count — a customer with three
product lines counts once, never three times. Pricing is per line; strategic
status, contract status and churn are per account.

## Sensitivity grid

`sensitivity_grid()` re-runs the model across a range of increase and churn
assumptions, so an outcome can be stress-tested instead of resting on one
guess.
