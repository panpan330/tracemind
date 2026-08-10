package com.tracemind.common.obs;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

class MetricsCollectorTest {
    @Test
    void computesP95QpsAndSlowTrace() {
        MeterRegistry registry = new SimpleMeterRegistry();
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("s", "t1", "total", 100, true);
        store.record("s", "t2", "total", 200, true);
        store.record("s", "t3", "total", 300, true);
        store.record("s", "t4", "total", 400, true);
        store.record("s", "t5", "total", 500, true);   // 5 条,p95 = 第 5 条(排序后 500)
        MetricsCollector collector = new MetricsCollector(registry, store, "s");
        MetricsCollector.Summary summary = collector.summary(300);
        assertThat(summary.p95Ms()).isEqualTo(500);
        assertThat(summary.qps()).isEqualTo(5.0 / 300.0, within(0.001));
        assertThat(summary.errorRate()).isZero();
        assertThat(summary.representativeSlowTraceId()).isEqualTo("t5");
    }

    @Test
    void emptyWindowReturnsNulls() {
        MetricsCollector collector = new MetricsCollector(
                new SimpleMeterRegistry(), new ObservationStore(10, 600_000), "s");
        MetricsCollector.Summary summary = collector.summary(300);
        assertThat(summary.p95Ms()).isNull();
        assertThat(summary.representativeSlowTraceId()).isNull();
    }
}
