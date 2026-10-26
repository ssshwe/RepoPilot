# Task 6 smoke correction

The first run saved all 52 standard snapshot-only BM25 case receipts successfully.
Its separate live smoke then used `RefundService.refundOrder`, which does not
exist: `refundOrder` belongs to `OrderController`; the service method is
`RefundService.refund`. The failing test assertion is preserved in
`evaluation-run.log`. No completed report was published for that run.

The accepted final run corrects only the smoke request/expectation, saves each
live smoke batch before assertions, and reuses the exact original 52 standard
case receipts. It independently verifies the scores, explanations, scope,
coordinates, Context and metrics, and fingerprints every original case file.
No model answers or standard-case rankings were regenerated or selected.
