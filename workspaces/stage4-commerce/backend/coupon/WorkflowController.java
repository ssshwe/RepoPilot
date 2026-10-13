package learning.coupon;
public final class WorkflowController {
    private final CouponService service;
    public WorkflowController(CouponService service) {
        this.service = service;
    }
    public void handle(String reference, int amount) {
        service.apply(reference, amount);
    }
}
