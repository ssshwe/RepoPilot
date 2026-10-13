package learning.auth;
public final class WorkflowController {
    private final AuthService service;
    public WorkflowController(AuthService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.login(reference, amount);
    }
}
