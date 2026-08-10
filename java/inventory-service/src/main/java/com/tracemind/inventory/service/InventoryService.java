package com.tracemind.inventory.service;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.mapper.InventoryMapper;
import org.springframework.stereotype.Service;

import java.util.Optional;

@Service
public class InventoryService {
    private final InventoryMapper inventoryMapper;

    public InventoryService(InventoryMapper inventoryMapper) {
        this.inventoryMapper = inventoryMapper;
    }

    public Optional<Inventory> queryStock(long skuId, long warehouseId) {
        return Optional.ofNullable(inventoryMapper.selectBySkuAndWarehouse(skuId, warehouseId));
    }
}
