package learning.notification;
import learning.shared.SharedRecorder;
public final class NotificationService {
    private int balance = 1000, stock = 100, refunded = 0, attempts = 0;
    private int total = 0, months = 0, queued = 0, discount = 0;
    private String state = "NEW";
    private final SharedRecorder recorder;
    public NotificationService(SharedRecorder recorder) {
        this.recorder = recorder;
    }
    public void send(String reference, int amount) {
        if (!reference.contains("@")) {
            throw new IllegalArgumentException("Notification recipient invalid");
        }
        queued += 1;
        recorder.record("notification.send");
    }
}
