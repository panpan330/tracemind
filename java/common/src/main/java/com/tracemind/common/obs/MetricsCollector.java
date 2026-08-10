package com.tracemind.common.obs;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Comparator;
import java.util.List;

@Component
public class MetricsCollector {
    private final MeterRegistry meterRegistry;
    private final ObservationStore store;
    private final String serviceName;
    private final Timer timer;

    public MetricsCollector(MeterRegistry meterRegistry, ObservationStore store,
                            @Value("${spring.application.name}") String serviceName) {
        this.meterRegistry = meterRegistry;
        this.store = store;
        this.serviceName = serviceName;
        this.timer = Timer.builder("http.server.requests.duration")
                .publishPercentiles(0.95)
                .register(meterRegistry);
    }

    /** 供各服务拦截器调用:同时计入 Micrometer Timer 与错误率。 */
    public void record(long durationMs, boolean success) {
        timer.record(Duration.ofMillis(durationMs));
        if (!success) {
            Counter.builder("http.server.requests.errors")
                    .tag("service", serviceName)
                    .register(meterRegistry).increment();
        }
    }

    /** 内部汇总端点:固定结构,不暴露 Actuator 原始响应。 */
    public Summary summary(long windowSeconds) {
        List<ObservationRecord> recs = store.recent(windowSeconds).stream()
                .filter(r -> r.service().equals(serviceName) && r.stage().equals("total"))
                .toList();
        if (recs.isEmpty()) {
            return new Summary(serviceName, windowSeconds, null, 0.0, null, null);
        }
        List<Long> durations = recs.stream().map(ObservationRecord::durationMs)
                .sorted().toList();
        long p95 = durations.get((int) Math.ceil(durations.size() * 0.95) - 1);
        long successes = recs.stream().filter(ObservationRecord::success).count();
        double errorRate = (recs.size() - successes) / (double) recs.size();
        ObservationRecord slowest = recs.stream()
                .max(Comparator.comparingLong(ObservationRecord::durationMs)).orElseThrow();
        return new Summary(serviceName, windowSeconds, p95,
                recs.size() / (double) windowSeconds, errorRate, slowest.traceId());
    }

    public record Summary(String service, long windowSeconds, Long p95Ms,
                          double qps, Double errorRate, String representativeSlowTraceId) {
    }
}
