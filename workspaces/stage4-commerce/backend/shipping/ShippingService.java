package learning.shipping;
import learning.shared.SharedRecorder;
public final class ShippingService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public ShippingService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void dispatch(String reference, int amount) {
        if (reference.length() < 5) {
            throw new IllegalArgumentException("Shipping postcode invalid");
        }
        state = "TRACK-" + reference;
        recorder.record("shipping.dispatch");
    }
}
