package learning.subscription;
public final class WorkflowController {
    private final SubscriptionService service;
    public WorkflowController(SubscriptionService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.renew(reference, amount);
    }
}
