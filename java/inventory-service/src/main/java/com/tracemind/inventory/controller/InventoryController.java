package com.tracemind.inventory.controller;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.service.InventoryService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/inventory")
public class InventoryController {
    private final InventoryService inventoryService;

    public InventoryController(InventoryService inventoryService) {
        this.inventoryService = inventoryService;
    }

    @GetMapping
    public ResponseEntity<?> query(@RequestParam long skuId, @RequestParam long warehouseId) {
        return inventoryService.queryStock(skuId, warehouseId)
                .<ResponseEntity<?>>map(i -> ResponseEntity.ok(Map.of(
                        "skuId", i.getSkuId(),
                        "warehouseId", i.getWarehouseId(),
                        "quantity", i.getQuantity())))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
