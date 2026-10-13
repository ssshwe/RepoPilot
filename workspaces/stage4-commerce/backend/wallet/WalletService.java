package learning.wallet;
import learning.shared.SharedRecorder;
public final class WalletService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public WalletService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void credit(String reference, int amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("Amount must be positive");
        }
        balance += amount;
        recorder.record("wallet.credit");
    }
}
