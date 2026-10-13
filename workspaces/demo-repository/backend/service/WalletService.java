package demo.service;

import java.math.BigDecimal;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public final class WalletService {
    private final Map<String, BigDecimal> balances = new ConcurrentHashMap<>();

    public void credit(String userId, BigDecimal amount) {
        if (userId == null || userId.isBlank()) {
            throw new IllegalArgumentException("User ID is required");
        }
        if (amount == null || amount.signum() <= 0) {
            throw new IllegalArgumentException("Amount must be positive");
        }
        balances.merge(userId, amount, BigDecimal::add);
    }

    public BigDecimal balanceOf(String userId) {
        return balances.getOrDefault(userId, BigDecimal.ZERO);
    }
}
