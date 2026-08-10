package com.tracemind.order.trace;

import com.tracemind.common.trace.TraceIdFilter;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class TraceIdFilterTest {
    @Test
    void generatesTraceId_whenHeaderAbsent() throws Exception {
        TraceIdFilter filter = new TraceIdFilter();
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/api/x");
        MockHttpServletResponse res = new MockHttpServletResponse();
        java.util.concurrent.atomic.AtomicReference<String> mdcInChain = new java.util.concurrent.atomic.AtomicReference<>();
        FilterChain chain = (request, response) -> mdcInChain.set(MDC.get("traceId"));
        filter.doFilter(req, res, chain);
        assertThat(res.getHeader("x-trace-id")).isNotBlank();
        assertThat(mdcInChain.get()).isEqualTo(res.getHeader("x-trace-id"));
    }

    @Test
    void propagatesTraceId_whenHeaderPresent() throws Exception {
        TraceIdFilter filter = new TraceIdFilter();
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/api/x");
        req.addHeader("x-trace-id", "trace-abc");
        MockHttpServletResponse res = new MockHttpServletResponse();
        filter.doFilter(req, res, mock(FilterChain.class));
        assertThat(res.getHeader("x-trace-id")).isEqualTo("trace-abc");
    }
}
