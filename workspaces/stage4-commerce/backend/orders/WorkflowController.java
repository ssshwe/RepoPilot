package learning.orders;
public final class WorkflowController {
    private final OrderService service;
    public WorkflowController(OrderService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.submit(reference, amount);
    }
}
