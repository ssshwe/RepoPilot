# Commerce retrieval learning fixture

This handwritten fixture is not a deployed payment application.
Official wallet code lives in backend/wallet; backend/legacy doubles credits.
WorkflowController.handle delegates to a feature service, then SharedRecorder records an event.
The database defaults wallet balance to zero and ledger status to PENDING.
Application settings use CNY, maxRetries=3, timeoutMs=1000.
