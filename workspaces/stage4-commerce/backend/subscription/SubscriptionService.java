package learning.subscription;
import learning.shared.SharedRecorder;
public final class SubscriptionService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public SubscriptionService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void renew(String reference, int amount) {
        if (amount < 1) {
            throw new IllegalArgumentException("Renewal months required");
        }
        months += amount;
        recorder.record("subscription.renew");
    }
}
