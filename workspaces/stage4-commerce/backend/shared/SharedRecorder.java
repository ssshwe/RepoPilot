package learning.shared;
public final class SharedRecorder {
    private int events = 0;
    public void record(String event) {
        events += 1;
    }
    public int count() {
        return events;
    }
}
