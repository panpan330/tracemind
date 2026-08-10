package com.tracemind.common.obs;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/internal/observations")
public class ObservationController {
    private final ObservationStore observationStore;
    private final MetricsCollector metricsCollector;

    public ObservationController(ObservationStore observationStore, MetricsCollector metricsCollector) {
        this.observationStore = observationStore;
        this.metricsCollector = metricsCollector;
    }

    @GetMapping("/traces/{traceId}")
    public ResponseEntity<?> trace(@PathVariable String traceId) {
        List<ObservationRecord> records = observationStore.get(traceId);
        if (records.isEmpty()) {
            return ResponseEntity.status(404).body(Map.of("error", "TRACE_NOT_FOUND"));
        }
        return ResponseEntity.ok(records);
    }

    @GetMapping("/metrics")
    public ResponseEntity<?> metrics(@RequestParam(name = "window_seconds", defaultValue = "300") long windowSeconds) {
        MetricsCollector.Summary summary = metricsCollector.summary(windowSeconds);
        return ResponseEntity.ok(summary);
    }
}
