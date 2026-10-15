# Task 4 development receipts

`tokenizer-development-report.json` is the intermediate receipt produced before
the path scanning guard and basename deduplication were finalized. Its original
implementation is preserved byte-for-byte as `tokenizer-before-path-guard.py.txt`.
It is not the accepted Task 4 report; the current verifier deliberately rejects
it after the implementation fingerprint changed. No generation results were
repeated or selected during this task.

`tokenizer-development-report-path-guard.json` records a subsequent intermediate
run, with its matching implementation in
`tokenizer-before-general-word-guard.py.txt`. The final implementation additionally
consumes repeated separators as one lexical run to avoid repeated suffix scans.

The accepted report is
`evaluation/reports/stage4_tokenizer_20261015_task4_final.json`.
`tokenizer-tests-first.log` retains the initial incorrect expected token assertion
for a long alternating-case identifier; the finalized acronym rule also emits
`aa` in that input. The 72 final tokenizer tests and complete test suites passed.
