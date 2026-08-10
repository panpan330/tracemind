package com.tracemind.inventory.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("inventory")
public class Inventory {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long skuId;
    private Long warehouseId;
    private Integer quantity;
    private Integer version;

    public Inventory() {
    }

    public Inventory(Long id, Long skuId, Long warehouseId, Integer quantity, Integer version) {
        this.id = id;
        this.skuId = skuId;
        this.warehouseId = warehouseId;
        this.quantity = quantity;
        this.version = version;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getSkuId() { return skuId; }
    public void setSkuId(Long skuId) { this.skuId = skuId; }
    public Long getWarehouseId() { return warehouseId; }
    public void setWarehouseId(Long warehouseId) { this.warehouseId = warehouseId; }
    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }
    public Integer getVersion() { return version; }
    public void setVersion(Integer version) { this.version = version; }
}
