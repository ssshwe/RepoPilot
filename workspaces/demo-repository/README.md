# Refund workflow fixture

This small hand-written repository provides actual files for RepoPilot's local
scanner and later code retrieval exercises. It demonstrates an order refund,
wallet balance updates and a Vue refund form. It is not a production payment
system or a standalone deployable application.

Key source files:

- `backend/service/RefundService.java`: validate an order reference and credit a wallet.
- `backend/service/WalletService.java`: validate amounts and update user balances.
- `backend/controller/OrderController.java`: delegate the refund request to the service.
- `frontend/src/RefundPanel.vue`: collect order, user and amount fields.
- `frontend/src/types.ts`: define the refund request structure.
- `database/schema.sql`: sample order and wallet tables.
- `config/repository.json`: fixture metadata without credentials.

`notes.txt` is intentionally outside the scanner's supported extensions.
`package-lock.json` demonstrates the fixed lock-file ignore rule.
Neither file is indexed. Task 4 only scans files; subsequent tasks add chunking
and indexing explicitly.
