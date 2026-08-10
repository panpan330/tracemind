package com.tracemind.inventory.service;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.mapper.InventoryMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class InventoryServiceTest {
    @Mock
    InventoryMapper inventoryMapper;
    @InjectMocks
    InventoryService inventoryService;

    @Test
    void queryStock_returnsInventory_whenFound() {
        when(inventoryMapper.selectBySkuAndWarehouse(42L, 7L)).thenReturn(
                new Inventory(1L, 42L, 7L, 100, 0));
        Optional<Inventory> result = inventoryService.queryStock(42L, 7L);
        assertThat(result).isPresent();
        assertThat(result.get().getQuantity()).isEqualTo(100);
    }

    @Test
    void queryStock_returnsEmpty_whenNotFound() {
        when(inventoryMapper.selectBySkuAndWarehouse(999L, 999L)).thenReturn(null);
        assertThat(inventoryService.queryStock(999L, 999L)).isEmpty();
    }
}
