package learning.invoice;
public final class WorkflowController {
    private final InvoiceService service;
    public WorkflowController(InvoiceService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.issue(reference, amount);
    }
}
