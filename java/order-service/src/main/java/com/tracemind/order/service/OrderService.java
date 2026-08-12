package com.tracemind.order.service;

import com.tracemind.order.client.InventoryClient;
import io.opentelemetry.instrumentation.annotations.WithSpan;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class OrderService {
    private final InventoryClient inventoryClient;

    public OrderService(InventoryClient inventoryClient) {
        this.inventoryClient = inventoryClient;
    }

    @WithSpan("order.reserve-inventory")
    public boolean checkStock(long orderId, long skuId, long warehouseId, int quantity) {
        Map<String, Object> stock = inventoryClient.queryStock(skuId, warehouseId);
        int available = ((Number) stock.getOrDefault("quantity", 0)).intValue();
        return available >= quantity;
    }
}
