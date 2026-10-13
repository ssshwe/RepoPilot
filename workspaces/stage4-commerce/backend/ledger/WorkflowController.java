package learning.ledger;
public final class WorkflowController {
    private final LedgerService service;
    public WorkflowController(LedgerService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.transfer(reference, amount);
    }
}
