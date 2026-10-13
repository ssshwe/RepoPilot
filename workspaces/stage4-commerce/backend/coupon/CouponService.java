package learning.coupon;
import learning.shared.SharedRecorder;
public final class CouponService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public CouponService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void apply(String reference, int amount) {
        if (amount > 100) {
            throw new IllegalArgumentException("Coupon discount exceeds limit");
        }
        discount = amount;
        recorder.record("coupon.apply");
    }
}
