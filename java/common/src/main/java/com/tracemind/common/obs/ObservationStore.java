package com.tracemind.common.obs;

import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.function.Supplier;

@Component
public class ObservationStore {
    private final int maxRecords;
    private final long ttlMillis;
    private final ConcurrentLinkedDeque<ObservationRecord> records = new ConcurrentLinkedDeque<>();
    private Supplier<Long> clock = () -> Instant.now().toEpochMilli();

    public ObservationStore() {
        this(10_000, 600_000);
    }

    public ObservationStore(int maxRecords, long ttlMillis) {
        this.maxRecords = maxRecords;
        this.ttlMillis = ttlMillis;
    }

    public void record(String service, String traceId, String stage, long durationMs, boolean success) {
        long now = clock.get();
        records.addLast(new ObservationRecord(service, traceId, stage, durationMs, success, now));
        evict(now);
    }

    public List<ObservationRecord> get(String traceId) {
        long now = clock.get();
        return records.stream()
                .filter(r -> now - r.occurredAtMillis() <= ttlMillis)
                .filter(r -> r.traceId().equals(traceId))
                .toList();
    }

    public List<ObservationRecord> recent(long windowSeconds) {
        long now = clock.get();
        return records.stream()
                .filter(r -> now - r.occurredAtMillis() <= windowSeconds * 1000L)
                .toList();
    }

    public void clear() {
        records.clear();
    }

    void advanceClockForTest(long deltaMillis) {
        long base = clock.get();
        clock = () -> base + deltaMillis;
    }

    private void evict(long now) {
        while (records.size() > maxRecords) {
            records.pollFirst();
        }
        ObservationRecord first;
        while ((first = records.peekFirst()) != null && now - first.occurredAtMillis() > ttlMillis) {
            records.pollFirst();
        }
    }
}
