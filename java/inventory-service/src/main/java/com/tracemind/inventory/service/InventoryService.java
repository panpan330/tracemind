package com.tracemind.inventory.service;

import com.tracemind.common.obs.ObservationStore;
import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.mapper.InventoryMapper;
import org.slf4j.MDC;
import org.springframework.stereotype.Service;

import java.util.Optional;

@Service
public class InventoryService {
    private final InventoryMapper inventoryMapper;
    private final ObservationStore observationStore;

    public InventoryService(InventoryMapper inventoryMapper, ObservationStore observationStore) {
        this.inventoryMapper = inventoryMapper;
        this.observationStore = observationStore;
    }

    public Optional<Inventory> queryStock(long skuId, long warehouseId) {
        long start = System.nanoTime();
        Inventory inventory = inventoryMapper.selectBySkuAndWarehouse(skuId, warehouseId);
        long dbMs = (System.nanoTime() - start) / 1_000_000;
        String traceId = MDC.get("traceId");
        if (traceId != null) {
            observationStore.record("inventory-service", traceId, "database", dbMs, true);
        }
        return Optional.ofNullable(inventory);
    }
}
