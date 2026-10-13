package learning.fraud;
import learning.shared.SharedRecorder;
public final class FraudService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public FraudService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void check(String reference, int amount) {
        if (amount > 80) {
            throw new IllegalArgumentException("Fraud score exceeds threshold");
        }
        state = "APPROVED";
        recorder.record("fraud.check");
    }
}
