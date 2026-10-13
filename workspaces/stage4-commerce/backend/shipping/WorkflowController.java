package learning.shipping;
public final class WorkflowController {
    private final ShippingService service;
    public WorkflowController(ShippingService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.dispatch(reference, amount);
    }
}
