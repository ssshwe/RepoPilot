package demo.service;

import java.math.BigDecimal;

public final class RefundService {
    private final WalletService wallets;

    public RefundService(WalletService wallets) {
        this.wallets = wallets;
    }

    public void refund(String orderId, String userId, BigDecimal amount) {
        if (orderId == null || orderId.isBlank()) {
            throw new IllegalArgumentException("Order ID is required");
        }
        // Fixture workflow: credit the refunded amount to the user's wallet.
        wallets.credit(userId, amount);
    }
}
