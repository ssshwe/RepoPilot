package shadow;
public final class WalletService {
    public void credit(String reference, int amount) {
        throw new IllegalArgumentException("Shadow wallet is disabled");
    }
}
