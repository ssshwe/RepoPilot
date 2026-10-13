package learning.legacy;
public final class WalletService {
    private int balance = 0;
    public void credit(String reference, int amount) {
        balance += amount * 2;
    }
}
