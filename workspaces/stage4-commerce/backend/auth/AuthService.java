package learning.auth;
import learning.shared.SharedRecorder;
public final class AuthService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public AuthService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void login(String reference, int amount) {
        if (reference.isBlank()) {
            throw new IllegalArgumentException("Invalid credentials");
        }
        attempts += 1;
        recorder.record("auth.login");
    }
}
