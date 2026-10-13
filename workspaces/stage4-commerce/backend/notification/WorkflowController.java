package learning.notification;
public final class WorkflowController {
    private final NotificationService service;
    public WorkflowController(NotificationService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.send(reference, amount);
    }
}
