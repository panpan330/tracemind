package com.tracemind.common.obs;

public record ObservationRecord(
        String service, String traceId, String stage,
        long durationMs, boolean success, long occurredAtMillis) {
}
