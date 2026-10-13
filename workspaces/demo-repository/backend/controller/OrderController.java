package demo.controller;

import demo.service.RefundService;
import java.math.BigDecimal;

public final class OrderController {
    private final RefundService refunds;

    public OrderController(RefundService refunds) {
        this.refunds = refunds;
    }

    public void refundOrder(String orderId, String userId, BigDecimal amount) {
        refunds.refund(orderId, userId, amount);
    }
}
