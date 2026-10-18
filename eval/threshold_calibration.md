embed model: gemini-embedding-001
corpus: 57 chunks, 52 current (5 superseded, filtered at retrieval)
threshold now: 0.7

| case | bucket | expects | top-1 sim | top-1 doc | above threshold |
|---|---|---|---|---|---|
| policy-01 | policy_only | resolve | 0.791 | `returns-standard` | returns-standard(0.79), returns-standard(0.78), returns-electronics(0.77), returns-standard(0.76) |
| policy-02 | policy_only | resolve | 0.805 | `damaged-goods` | damaged-goods(0.81), damaged-goods(0.77), damaged-goods(0.76), damaged-goods(0.76) |
| policy-03 | policy_only | resolve | 0.775 | `employee-discount` | employee-discount(0.77), employee-discount(0.76), employee-discount(0.74), employee-discount(0.74) |
| order-01 | order_policy | resolve | 0.746 | `special-orders` | special-orders(0.75), returns-standard(0.72), returns-standard(0.72), special-orders(0.72) |
| order-02 | order_policy | resolve | 0.732 | `special-orders` | special-orders(0.73), returns-electronics(0.71), returns-electronics(0.71), returns-standard(0.71) |
| order-03 | order_policy | resolve | 0.735 | `returns-standard` | returns-standard(0.73), returns-standard(0.72), returns-electronics(0.72), special-orders(0.71) |
| order-04 | order_policy | resolve | 0.749 | `damaged-goods` | damaged-goods(0.75), damaged-goods(0.74), damaged-goods(0.74), damaged-goods(0.73) |
| multi-01 | multi_hop | resolve | 0.787 | `price-match` | price-match(0.79), price-match(0.77), price-match(0.76), price-match(0.75) |
| multi-02 | multi_hop | resolve | 0.791 | `price-match` | price-match(0.79), price-match(0.78), employee-discount(0.76), price-match(0.76) |
| superseded-01 | superseded | resolve | 0.763 | `returns-standard` | returns-standard(0.76), returns-standard(0.75), returns-electronics(0.73), returns-standard(0.72) |
| superseded-02 | superseded | resolve | 0.726 | `returns-standard` | returns-standard(0.73), returns-standard(0.71), returns-electronics(0.71), damaged-goods(0.70) |
| abstain-01 | abstain | escalate | 0.654 | `special-orders` | none |
| abstain-02 | abstain | escalate | 0.644 | `price-match` | none |
| abstain-03 | abstain | escalate | 0.721 | `returns-standard` | returns-standard(0.72), returns-electronics(0.71), returns-standard(0.71), returns-electronics(0.70) <= would pass the gate |
| abstain-04 | abstain | escalate | 0.719 | `special-orders` | special-orders(0.72) <= would pass the gate |
| adversarial-01 | adversarial | ? | 0.689 | `special-orders` | none |
| adversarial-02 | adversarial | ? | 0.745 | `override-approvals` | override-approvals(0.75), returns-electronics(0.71), override-approvals(0.71) |
| adversarial-03 | adversarial | resolve | 0.738 | `returns-standard` | returns-standard(0.74), returns-standard(0.73), warranty-and-credit(0.72), damaged-goods(0.71) |

answerable cases: 12   abstain cases: 4
  answerable top-1 similarity: min 0.726 max 0.805
  abstain    top-1 similarity: min 0.644 max 0.721

| threshold | answerable admitted | abstain rejected | total errors |
|---|---|---|---|
| 0.30 | 12/12 | 0/4 | 4 |
| 0.35 | 12/12 | 0/4 | 4 |
| 0.40 | 12/12 | 0/4 | 4 |
| 0.45 | 12/12 | 0/4 | 4 |
| 0.50 | 12/12 | 0/4 | 4 |
| 0.55 | 12/12 | 0/4 | 4 |
| 0.60 | 12/12 | 0/4 | 4 |
| 0.65 | 12/12 | 1/4 | 3 |
| 0.70 | 12/12 | 2/4 | 2 |
| 0.75 | 6/12 | 4/4 | 6 |
| 0.80 | 1/12 | 4/4 | 11 |

lowest error at threshold 0.72: 12/12 answerable admitted, 3/4 abstain rejected, 1 errors

At 0.7, 2 abstain case(s) pass the retrieval gate anyway:
  abstain-03       top-1 0.721 on `returns-standard` - A customer bought a TV from Best Buy and wants to know Best Buy'
  abstain-04       top-1 0.719 on `special-orders` - Can we refund order NM-0000?

  These are not fixable by raising the threshold without also rejecting
  real questions. They are the cases where the retrieved section is
  topically close but does not contain the rule, so the model has to judge
  coverage. The golden set keeps them for exactly that reason.
