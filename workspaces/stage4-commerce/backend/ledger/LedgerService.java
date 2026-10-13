package learning.ledger;
import learning.shared.SharedRecorder;
public final class LedgerService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public LedgerService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void transfer(String reference, int amount) {
        if (amount > balance) {
            throw new IllegalArgumentException("Ledger balance insufficient");
        }
        balance -= amount;
        recorder.record("ledger.transfer");
    }
}
