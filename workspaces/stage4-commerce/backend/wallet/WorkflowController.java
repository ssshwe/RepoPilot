package learning.wallet;
public final class WorkflowController {
    private final WalletService service;
    public WorkflowController(WalletService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.credit(reference, amount);
    }
}
