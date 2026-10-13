package learning.invoice;
import learning.shared.SharedRecorder;
public final class InvoiceService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public InvoiceService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void issue(String reference, int amount) {
        if (amount < 0) {
            throw new IllegalArgumentException("Invoice amount cannot be negative");
        }
        total = amount + 20;
        recorder.record("invoice.issue");
    }
}
