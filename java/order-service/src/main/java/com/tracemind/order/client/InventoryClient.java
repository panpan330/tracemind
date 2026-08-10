package com.tracemind.order.client;

import com.tracemind.common.obs.ObservationStore;
import com.tracemind.common.trace.TraceIdFilter;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

@Component
public class InventoryClient {
    private final RestClient restClient;
    private final ObservationStore observationStore;

    public InventoryClient(@Value("${INVENTORY_SERVICE_URL:http://localhost:8082}") String baseUrl,
                           ObservationStore observationStore) {
        this.restClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(new JdkClientHttpRequestFactory())
                .build();
        this.observationStore = observationStore;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryStock(long skuId, long warehouseId) {
        long start = System.nanoTime();
        try {
            return restClient.get()
                    .uri(uriBuilder -> uriBuilder.path("/api/inventory")
                            .queryParam("skuId", skuId).queryParam("warehouseId", warehouseId).build())
                    .header(TraceIdFilter.TRACE_ID_HEADER, MDC.get("traceId"))
                    .retrieve()
                    .body(Map.class);
        } catch (org.springframework.web.client.RestClientResponseException e) {
            if (e.getStatusCode().value() == 404) {
                // 库存记录不存在 = 库存 0,不是服务故障
                return Map.of("quantity", 0);
            }
            throw e;
        } finally {
            long httpMs = (System.nanoTime() - start) / 1_000_000;
            String traceId = MDC.get("traceId");
            if (traceId != null) {
                observationStore.record("order-service", traceId, "inventory_http", httpMs, true);
            }
        }
    }
}
