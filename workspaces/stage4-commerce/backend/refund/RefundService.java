package learning.refund;
import learning.shared.SharedRecorder;
public final class RefundService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public RefundService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void refund(String reference, int amount) {
        if (reference.isBlank()) {
            throw new IllegalArgumentException("Refund order ID required");
        }
        refunded += amount;
        recorder.record("refund.refund");
    }
}
