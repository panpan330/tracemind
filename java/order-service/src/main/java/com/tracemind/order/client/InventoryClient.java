package com.tracemind.order.client;

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

    public InventoryClient(@Value("${INVENTORY_SERVICE_URL:http://localhost:8082}") String baseUrl) {
        this.restClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(new JdkClientHttpRequestFactory())
                .build();
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryStock(long skuId, long warehouseId) {
        return restClient.get()
                .uri(uriBuilder -> uriBuilder.path("/api/inventory")
                        .queryParam("skuId", skuId).queryParam("warehouseId", warehouseId).build())
                .header(TraceIdFilter.TRACE_ID_HEADER, MDC.get("traceId"))
                .retrieve()
                .body(Map.class);
    }
}
