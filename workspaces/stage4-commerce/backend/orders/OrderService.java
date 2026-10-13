package learning.orders;
import learning.shared.SharedRecorder;
public final class OrderService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public OrderService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void submit(String reference, int amount) {
        if (amount < 1) {
            throw new IllegalArgumentException("Cart must contain an item");
        }
        state = "PAID";
        recorder.record("orders.submit");
    }
}
