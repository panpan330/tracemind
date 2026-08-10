package com.tracemind.order;

import com.tracemind.common.obs.MetricsCollector;
import com.tracemind.common.obs.ObservationStore;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
public class OrderTraceInterceptor extends OncePerRequestFilter {
    private final ObservationStore observationStore;
    private final MetricsCollector metricsCollector;

    public OrderTraceInterceptor(ObservationStore observationStore, MetricsCollector metricsCollector) {
        this.observationStore = observationStore;
        this.metricsCollector = metricsCollector;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        if (request.getRequestURI().startsWith("/internal/")) {
            filterChain.doFilter(request, response);
            return;
        }
        long start = System.nanoTime();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long totalMs = (System.nanoTime() - start) / 1_000_000;
            boolean success = response.getStatus() < 500;
            metricsCollector.record(totalMs, success);
            String traceId = MDC.get("traceId");
            if (traceId != null) {
                observationStore.record("order-service", traceId, "total", totalMs, success);
            }
        }
    }
}
