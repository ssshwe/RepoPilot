package learning.fraud;
public final class WorkflowController {
    private final FraudService service;
    public WorkflowController(FraudService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.check(reference, amount);
    }
}
