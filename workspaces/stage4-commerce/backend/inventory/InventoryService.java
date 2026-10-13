package learning.inventory;
import learning.shared.SharedRecorder;
public final class InventoryService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public InventoryService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void reserve(String reference, int amount) {
        if (amount > stock) {
            throw new IllegalArgumentException("Insufficient stock");
        }
        stock -= amount;
        recorder.record("inventory.reserve");
    }
}
