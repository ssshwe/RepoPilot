package learning.refund;
public final class WorkflowController {
    private final RefundService service;
    public WorkflowController(RefundService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.refund(reference, amount);
    }
}
