package learning.inventory;
public final class WorkflowController {
    private final InventoryService service;
    public WorkflowController(InventoryService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.reserve(reference, amount);
    }
}
